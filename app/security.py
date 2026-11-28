"""Security configuration: external secrets and least-privilege roles."""
from __future__ import annotations
import os
from dataclasses import dataclass

@dataclass(frozen=True,slots=True)
class SecuritySettings:
    api_token:str|None
    database_url:str|None
    live_broker_enabled:bool
    allow_manual_execution:bool
    @classmethod
    def from_env(cls)->"SecuritySettings":
        if os.getenv("TRADING_MODE","demo").lower() in {"live","production"}:
            raise RuntimeError("live trading is disabled by architecture policy")
        token=os.getenv("X2_API_TOKEN")
        db=os.getenv("DATABASE_URL")
        if db and ("password=" in db.lower() or "://"+":" in db):
            raise ValueError("database credentials must be supplied through external secrets, not source config")
        return cls(token,db,False,os.getenv("ALLOW_MANUAL_EXECUTION","0")=="1")

def require_secret(name:str)->str:
    value=os.getenv(name)
    if not value: raise RuntimeError(f"required external secret missing: {name}")
    return value
