"""Small lifecycle substitute for automation loop unit tests; PostgreSQL is covered separately."""
from contextlib import nullcontext
from types import SimpleNamespace
from app.automation_worker import AutomationProcess
from tests.runtime_factories import make_application_runtime
from app.modules.automation.runtime import AutomationRuntime


def automation_process(settings, service, runtime=None):
    service.delivery = SimpleNamespace(process_next_email=service.process_next_email)
    state = make_application_runtime(automation_service=service,automation_runtime=runtime or AutomationRuntime(settings),
        automation=SimpleNamespace(deliveries=SimpleNamespace(recover_interrupted_deliveries=lambda:None)),
        coordination=SimpleNamespace(acquire=lambda *a,**kw:'test-owner',renew=lambda *a,**kw:True,release=lambda *a:None),
        database=SimpleNamespace(close=lambda:None),database_runtime=SimpleNamespace(close=lambda:None))
    process = AutomationProcess(settings,state)
    process.scope = nullcontext
    process._heartbeat = lambda:None
    return process
