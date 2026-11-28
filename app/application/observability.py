"""Provider-neutral observability primitives."""
from __future__ import annotations
import json
import logging
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Iterator, Mapping

@dataclass
class Metrics:
    counters: dict[str,int]=field(default_factory=dict)
    gauges: dict[str,float]=field(default_factory=dict)
    timings_ms: dict[str,list[float]]=field(default_factory=dict)
    def inc(self,name:str,value:int=1)->None:self.counters[name]=self.counters.get(name,0)+value
    def set(self,name:str,value:float)->None:self.gauges[name]=value
    @contextmanager
    def timing(self,name:str)->Iterator[None]:
        start=time.perf_counter()
        try: yield
        finally:self.timings_ms.setdefault(name,[]).append((time.perf_counter()-start)*1000)
    def snapshot(self)->dict[str,object]:return {"counters":dict(self.counters),"gauges":dict(self.gauges),"timings_ms":{k:list(v) for k,v in self.timings_ms.items()}}

class JsonFormatter(logging.Formatter):
    def format(self,record:logging.LogRecord)->str:
        payload={"timestamp":self.formatTime(record,"%Y-%m-%dT%H:%M:%S%z"),"level":record.levelname,"logger":record.name,"message":record.getMessage()}
        for key in ("correlation_id","causation_id","event_id","strategy_id","portfolio_id"): 
            if hasattr(record,key): payload[key]=getattr(record,key)
        return json.dumps(payload,sort_keys=True)

def configure_logging(level:str|None=None)->None:
    handler=logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root=logging.getLogger()
    root.handlers.clear(); root.addHandler(handler); root.setLevel((level or os.getenv("LOG_LEVEL","INFO")).upper())

@dataclass(frozen=True,slots=True)
class HealthStatus:
    status:str
    checks:Mapping[str,bool]
    def to_dict(self)->dict[str,object]:return {"status":self.status,"checks":dict(self.checks)}
