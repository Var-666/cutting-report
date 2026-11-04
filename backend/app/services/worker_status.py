from dataclasses import replace
import json
from threading import Lock
from time import monotonic

from app.database.coordination import CoordinationRepository
from app.database.postgres import PostgresDatabase

ROLES = ('scheduler', 'worker', 'automation')


def public_status(status):
    if isinstance(status, dict):
        return {key: public_status(value) for key, value in status.items() if key != 'configuration'}
    if isinstance(status, list):
        return [public_status(value) for value in status]
    return status


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
                    instances = self.repository.consumer_statuses()
                    healthy = [item for item in instances if role_ready('worker', item)]
                    abnormal = [item for item in instances if item['state'] != 'stopped' and not role_ready('worker', item)]
                    sample = min(healthy or instances, key=lambda item: item['heartbeat_age_seconds'], default={})
                    worker = {**sample, 'state': 'running' if healthy and not abnormal else 'degraded' if healthy else 'unavailable',
                              'report_alive':bool(healthy), 'active_tasks':sum(item.get('active_tasks',0) for item in instances if item['state'] in ('running','stalled')),
                              'stalled_workers':[f"{item['instance_id']}:{name}" for item in instances if item['state'] in ('running','stalled') for name in item.get('stalled_workers',[])]}
                    roles = {role:self.repository.worker_status(role) for role in ('scheduler','automation')}
                    roles['worker'] = worker
                    self._cached = {**worker, 'roles':roles, 'database_available':True,
                        'consumer_instances':instances, 'consumer_running_count':len(healthy),
                        'consumer_lost_count':sum(item['state']=='lost' for item in instances),
                        'background_state':'running' if all(role_ready(role,roles[role]) for role in ROLES) else 'degraded',
                        'automation_alive':role_ready('automation',roles['automation']),
                        'email_alive':bool(roles['automation'].get('email_alive')) and roles['automation']['state']=='running',
                        'scheduler_alive':role_ready('scheduler',roles['scheduler'])}
                except Exception:
                    self._cached = {'state':'unavailable','report_alive':False,'database_available':False,
                                    'roles':{role:{'state':'unavailable'} for role in ROLES}}
                self._next_read = monotonic()+2
            return dict(self._cached)
