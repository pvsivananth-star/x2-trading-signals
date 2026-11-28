"""Readiness/liveness aggregation."""
from __future__ import annotations
from app.application.observability import HealthStatus

def health_status(*,data_healthy:bool,broker_healthy:bool,reconciled:bool,database_healthy:bool=True)->HealthStatus:
    checks={"data":data_healthy,"broker":broker_healthy,"reconciled":reconciled,"database":database_healthy}
    return HealthStatus("healthy" if all(checks.values()) else "degraded",checks)
