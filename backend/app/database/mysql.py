"""Reusable MySQL read-only repeatable-read sessions and bounded connection pools."""
from __future__ import annotations

import logging
import hashlib
from math import ceil
from contextlib import contextmanager
from threading import RLock
from time import monotonic
from typing import Any, Iterator
from uuid import uuid4

from app.models.database_sources import DatabaseSourceConfig
from app.core.report_deadline import remaining_seconds
from app.core.request_cancellation import SqlRequestCancelled, current_request_id, sql_request_cancellations

logger = logging.getLogger(__name__)


class MySqlUnavailable(RuntimeError):
    def __init__(self, message: str, *, category: str = 'source_unavailable') -> None:
        super().__init__(message)
        self.category = category


class MySqlSession:
    def __init__(self, connection, timeout: int = 120, label: str = 'MySQL') -> None:
        self.label = label
        self.connection = connection
        self.timeout = timeout

    def fetch(self, sql: str, values: tuple = (), *, limit: int = 100001) -> list[dict[str, Any]]:
        if sql_request_cancellations.is_cancelled(current_request_id.get()):
            raise SqlRequestCancelled(f'{self.label}查询已取消')
        started = monotonic()
        remaining = remaining_seconds()
        if remaining is not None and remaining <= 0:
            raise MySqlUnavailable(f'{self.label}查询超时，请缩小日期或筛选范围后重试', category='query_timeout')
        timeout = self.timeout if remaining is None else max(1, min(self.timeout, ceil(remaining)))
        query_id = hashlib.sha256(sql.encode()).hexdigest()[:12]
        cursor = self.connection.cursor(dictionary=True, read_timeout=timeout, write_timeout=timeout)
        failed = False
        try:
            cursor.execute(sql, values)
            rows = cursor.fetchmany(limit)
            if sql_request_cancellations.is_cancelled(current_request_id.get()):
                raise SqlRequestCancelled(f'{self.label}查询已取消')
            logger.info('mysql query request_id=%s id=%s elapsed_ms=%d rows=%d', current_request_id.get(), query_id, round((monotonic() - started) * 1000), len(rows))
            return rows
        except Exception as exc:
            failed = True
            logger.warning('mysql query failed request_id=%s id=%s type=%s errno=%s elapsed_ms=%d', current_request_id.get(), query_id, type(exc).__name__, getattr(exc, 'errno', None), round((monotonic() - started) * 1000))
            raise
        finally:
            try:
                cursor.close()
            except Exception as exc:
                if not failed:
                    raise
                logger.warning('mysql cursor cleanup failure type=%s errno=%s', type(exc).__name__, getattr(exc, 'errno', None))


class MySqlClient:
    session_type = MySqlSession

    def __init__(self, config: DatabaseSourceConfig | None, *, label: str = 'MySQL') -> None:
        if config is not None and config.engine != 'mysql':
            raise ValueError('数据源不是 MySQL')
        self.config = config
        self.query_timeout = config.query_timeout if config else 120
        self.label = label
        self._pool = None
        self._lock = RLock()
        self._active: dict[int, int] = {}
        self._retired: dict[int, Any] = {}
        self._closed = False

    def connection_config(self) -> DatabaseSourceConfig:
        if self.config is None:
            raise ValueError('缺少 MySQL 连接配置')
        return self.config

    def _get_pool(self):
        cfg = self.connection_config()
        if not cfg.enabled:
            raise MySqlUnavailable(f'{self.label}数据源尚未启用')
        if not cfg.password.get_secret_value():
            raise MySqlUnavailable(f'{self.label}数据库配置不完整，请联系管理员')
        with self._lock:
            if self._closed:
                raise MySqlUnavailable(f'{self.label}客户端已关闭')
            if self._pool is None:
                from mysql.connector.pooling import MySQLConnectionPool
                self._pool = MySQLConnectionPool(
                    pool_name=f'source_{uuid4().hex[:16]}', pool_size=cfg.pool_size,
                    host=cfg.host, port=cfg.port, database=cfg.database,
                    user=cfg.username, password=cfg.password.get_secret_value(), charset=cfg.charset,
                    connection_timeout=cfg.connect_timeout,
                    read_timeout=cfg.query_timeout, write_timeout=cfg.query_timeout,
                    use_pure=True, autocommit=False,
                )
            return self._pool

    def _retire(self, pool) -> None:
        if self._pool is pool:
            self._pool = None
        self._retired[id(pool)] = pool
        self._drain()

    def _drain(self) -> None:
        for key, pool in list(self._retired.items()):
            if not self._active.get(key):
                # Connector/Python has no public pool close; drain only after all checkouts return.
                remove = getattr(pool, '_remove_connections', None)
                if remove is not None:
                    try:
                        remove()
                    except Exception as exc:
                        logger.warning('mysql pool cleanup failure type=%s', type(exc).__name__)
                del self._retired[key]

    def close(self) -> None:
        with self._lock:
            self._closed = True
            if self._pool is not None:
                self._retire(self._pool)
            self._drain()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    @contextmanager
    def snapshot(self) -> Iterator[MySqlSession]:
        connection = None
        pool = None
        discard_pool = False
        ready = False
        try:
            with self._lock:
                pool = self._get_pool()
                self._active[id(pool)] = self._active.get(id(pool), 0) + 1
            connection = pool.get_connection()
            connection.start_transaction(isolation_level='REPEATABLE READ', consistent_snapshot=True, readonly=True)
            ready = True
            yield self.session_type(connection, self.query_timeout, self.label)
        except (MySqlUnavailable, SqlRequestCancelled):
            raise
        except ValueError:
            if ready:
                raise
            raise MySqlUnavailable(f'{self.label}连接配置无效，请检查后端配置') from None
        except Exception as exc:
            # Driver messages can contain credentials, SQL values and host addresses.
            discard_pool = True
            logger.warning('mysql source failure request_id=%s type=%s errno=%s', current_request_id.get(), type(exc).__name__, getattr(exc, 'errno', None))
            code = getattr(exc, 'errno', None)
            if type(exc).__name__ == 'PoolError':
                discard_pool = False
                raise MySqlUnavailable(f'{self.label}数据库连接池已耗尽，请稍后重试', category='connection_pool_exhausted') from None
            if isinstance(exc, TimeoutError) or type(exc).__name__ in ('ReadTimeoutError', 'WriteTimeoutError') or code in (1205, 1317, 3024):
                raise MySqlUnavailable(f'{self.label}数据库查询超时，请缩小筛选范围或联系管理员核查SQL耗时', category='query_timeout') from None
            if code == 1045:
                raise MySqlUnavailable(f'{self.label}数据库认证失败，请检查账号和密码', category='authentication_failed') from None
            if code in (1142, 1143, 1227):
                raise MySqlUnavailable(f'{self.label}数据库只读查询权限不足，请联系管理员核对授权', category='permission_denied') from None
            if code in (1049, 1054, 1146):
                raise MySqlUnavailable(f'{self.label}数据库或表结构与预期不符，请核对数据库和字段映射', category='schema_mismatch') from None
            if code in (1064, 1149):
                raise MySqlUnavailable(f'{self.label}SQL执行失败，请联系管理员核查查询语句', category='sql_error') from None
            if code in (2002, 2003, 2005, 2006, 2013, 2055):
                raise MySqlUnavailable(f'{self.label}数据库连接失败或连接中断，请核查网络及数据库服务', category='connection_failed') from None
            if isinstance(exc, ImportError):
                raise MySqlUnavailable(f'服务器尚未安装{self.label}MySQL驱动，请更新部署依赖') from None
            raise MySqlUnavailable(f'{self.label}数据源暂不可用或查询超时，请稍后重试') from None
        finally:
            if connection is not None:
                try:
                    connection.rollback()
                except Exception as exc:
                    discard_pool = True
                    logger.warning('mysql rollback failure type=%s errno=%s', type(exc).__name__, getattr(exc, 'errno', None))
                finally:
                    try:
                        connection.close()
                    except Exception as exc:
                        discard_pool = True
                        logger.warning('mysql connection cleanup failure type=%s errno=%s', type(exc).__name__, getattr(exc, 'errno', None))
            if discard_pool and pool is not None:
                # A timed-out connector can retain unread protocol data even after reconnect.
                with self._lock:
                    if self._pool is pool:
                        self._retire(pool)

            if pool is not None:
                with self._lock:
                    key = id(pool)
                    self._active[key] -= 1
                    if not self._active[key]:
                        del self._active[key]
                    self._drain()
