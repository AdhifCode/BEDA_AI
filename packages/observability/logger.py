import json
import logging
import os
import sys
import time
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, Dict, Optional

# Context variables for tracing and correlation
request_id_ctx: ContextVar[Optional[str]] = ContextVar("request_id", default=None)
enquiry_id_ctx: ContextVar[Optional[str]] = ContextVar("enquiry_id", default=None)
step_id_ctx: ContextVar[Optional[str]] = ContextVar("step_id", default=None)


class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        log_obj: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        
        req_id = request_id_ctx.get()
        if req_id:
            log_obj["request_id"] = req_id
            
        enq_id = enquiry_id_ctx.get()
        if enq_id:
            log_obj["enquiry_id"] = enq_id
            
        st_id = step_id_ctx.get()
        if st_id:
            log_obj["step_id"] = st_id
            
        if hasattr(record, "extra_data") and isinstance(record.extra_data, dict):
            log_obj.update(record.extra_data)
            
        if record.exc_info:
            log_obj["exception"] = self.formatException(record.exc_info)
            
        return json.dumps(log_obj)


def get_logger(name: str = "beda") -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JSONFormatter())
        logger.addHandler(handler)
        level_name = os.getenv("LOG_LEVEL", "INFO").upper()
        logger.setLevel(getattr(logging, level_name, logging.INFO))
    return logger


# Global metrics tracker
class MetricsTracker:
    def __init__(self):
        self.counters: Dict[str, int] = {}
        self.latencies: Dict[str, list[float]] = {}

    def increment(self, name: str, value: int = 1) -> None:
        self.counters[name] = self.counters.get(name, 0) + value

    def observe_latency(self, name: str, latency_seconds: float) -> None:
        if name not in self.latencies:
            self.latencies[name] = []
        self.latencies[name].append(latency_seconds)

    def get_metrics(self) -> Dict[str, Any]:
        return {
            "counters": self.counters.copy(),
            "latencies_count": {k: len(v) for k, v in self.latencies.items()},
        }


metrics = MetricsTracker()
