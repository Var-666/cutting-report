from dataclasses import replace
import json
from threading import Lock
from time import monotonic

from app.database.coordination import CoordinationRepository
from app.database.postgres import PostgresDatabase

ROLES = ('scheduler', 'worker', 'automation')


def public_status(status):
    return {key: ({role:public_status(value) for role,value in item.items()} if key=='roles' else item)
            for key,item in status.items() if key!='configuration'}


def role_ready(role, status):
    if status.get('state') != 'running' or status.get('stalled_workers'): return False
    if role == 'scheduler': return bool(status.get('scheduler_alive'))
    if role == 'automation': return bool(status.get('automation_alive') and status.get('email_alive'))
    return bool(status.get('report_alive') and status.get('queue', {}).get('available') is not False)


def background_ready(status: dict) -> bool:
    return all(role_ready(role,status.get('roles',{}).get(role,{})) for role in ROLES)


def role_health(settings, role):
    try:
        with PostgresDatabase(replace(settings,postgres_pool_min_size=0,postgres_pool_max_size=1,postgres_connect_timeout_seconds=1)) as database:
            status = CoordinationRepository(database).worker_status(role)
        print(json.dumps({k:v for k,v in status.items() if k!='configuration'},ensure_ascii=False))
        return 0 if role_ready(role,status) else 1
    except Exception: return 1


class WorkerStatusReader:
    def __init__(self, database: PostgresDatabase) -> None:
        self.repository = CoordinationRepository(database)
        self._lock = Lock()
        self._next_read = 0.0
        self._cached = {'state':'missing','report_alive':False}

    def status(self) -> dict:
        with self._lock:
            if monotonic() >= self._next_read:
                try:
                    roles = {role:self.repository.worker_status(role) for role in ROLES}
                    self._cached = {**roles['worker'], 'roles':roles, 'database_available':True,
                        'background_state':'running' if all(role_ready(role,roles[role]) for role in ROLES) else 'degraded',
                        'automation_alive':role_ready('automation',roles['automation']),
                        'email_alive':bool(roles['automation'].get('email_alive')) and roles['automation']['state']=='running',
                        'scheduler_alive':role_ready('scheduler',roles['scheduler'])}
                except Exception:
                    self._cached = {'state':'unavailable','report_alive':False,'database_available':False,
                                    'roles':{role:{'state':'unavailable'} for role in ROLES}}
                self._next_read = monotonic()+2
            return dict(self._cached)
