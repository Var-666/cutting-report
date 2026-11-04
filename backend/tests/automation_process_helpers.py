"""Small lifecycle substitute for automation loop unit tests; PostgreSQL is covered separately."""
from contextlib import nullcontext
from types import SimpleNamespace
from app.automation_worker import AutomationProcess
from app.services.automation_runtime import AutomationRuntime


def automation_process(settings, service, runtime=None):
    state = SimpleNamespace(automation_service=service,automation_runtime=runtime or AutomationRuntime(settings),
        automation=SimpleNamespace(recover_interrupted_deliveries=lambda:None),
        coordination=SimpleNamespace(acquire=lambda *a,**kw:'test-owner',renew=lambda *a,**kw:True,release=lambda *a:None),
        database=SimpleNamespace(close=lambda:None),database_runtime=SimpleNamespace(close=lambda:None))
    process = AutomationProcess(settings,state)
    process.scope = nullcontext
    process._heartbeat = lambda:None
    return process
