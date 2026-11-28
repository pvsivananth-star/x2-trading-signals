import logging
from app.application.health import health_status
from app.application.observability import Metrics, configure_logging
from app.security import SecuritySettings

def test_health_is_fail_closed():
    assert health_status(data_healthy=True,broker_healthy=True,reconciled=True).status=="healthy"
    assert health_status(data_healthy=True,broker_healthy=False,reconciled=True).status=="degraded"

def test_metrics_and_logging_are_structured():
    m=Metrics()
    m.inc("orders",2)
    m.set("risk",1)
    assert m.snapshot()["counters"]["orders"]==2
    configure_logging("WARNING")
    assert logging.getLogger().level==logging.WARNING

def test_security_defaults_disable_live_and_manual():
    s=SecuritySettings.from_env()
    assert s.live_broker_enabled is False
    assert s.allow_manual_execution is False
