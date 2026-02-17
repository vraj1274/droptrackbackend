"""
Advanced logging configuration for the DropTrack application.

This module provides comprehensive logging setup with support for:
- Structured JSON logging
- Multiple log levels and handlers
- Audit and security logging
- Performance monitoring
- Request tracing
"""

import logging
import logging.config
import os
from typing import Dict, Any


def get_logging_config(debug: bool = False) -> Dict[str, Any]:
    """
    Get comprehensive logging configuration dictionary.
    
    Args:
        debug: Whether to enable debug logging
        
    Returns:
        Logging configuration dictionary for logging.config.dictConfig
    """
    log_level = "DEBUG" if debug else "INFO"
    use_json = not debug and os.getenv("LOG_FORMAT", "json").lower() == "json"
    
    # Base formatter configurations
    formatters = {
        "standard": {
            "format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            "datefmt": "%Y-%m-%d %H:%M:%S"
        },
        "detailed": {
            "format": "%(asctime)s - %(name)s - %(levelname)s - %(funcName)s:%(lineno)d - %(message)s",
            "datefmt": "%Y-%m-%d %H:%M:%S"
        }
    }
    
    if use_json:
        formatters["json"] = {
            "()": "app.utils.StructuredFormatter",
            "fmt": "%(timestamp)s %(level)s %(logger)s %(message)s"
        }
        default_formatter = "json"
    else:
        default_formatter = "standard"
    
    # Handler configurations
    handlers = {
        "console": {
            "class": "logging.StreamHandler",
            "level": log_level,
            "formatter": default_formatter,
            "stream": "ext://sys.stdout"
        },
        "audit": {
            "class": "logging.StreamHandler",
            "level": "INFO",
            "formatter": default_formatter,
            "stream": "ext://sys.stdout"
        },
        "security": {
            "class": "logging.StreamHandler",
            "level": "WARNING",
            "formatter": default_formatter,
            "stream": "ext://sys.stdout"
        },
        "performance": {
            "class": "logging.StreamHandler",
            "level": "INFO",
            "formatter": default_formatter,
            "stream": "ext://sys.stdout"
        }
    }
    
    # Add file handlers if configured
    log_dir = os.getenv("LOG_DIR")
    if log_dir and os.path.exists(log_dir):
        handlers.update({
            "file": {
                "class": "logging.handlers.RotatingFileHandler",
                "level": log_level,
                "formatter": default_formatter,
                "filename": os.path.join(log_dir, "droptrack.log"),
                "maxBytes": 10485760,  # 10MB
                "backupCount": 5
            },
            "audit_file": {
                "class": "logging.handlers.RotatingFileHandler",
                "level": "INFO",
                "formatter": default_formatter,
                "filename": os.path.join(log_dir, "audit.log"),
                "maxBytes": 10485760,  # 10MB
                "backupCount": 10
            },
            "security_file": {
                "class": "logging.handlers.RotatingFileHandler",
                "level": "WARNING",
                "formatter": default_formatter,
                "filename": os.path.join(log_dir, "security.log"),
                "maxBytes": 10485760,  # 10MB
                "backupCount": 10
            }
        })
    
    # Logger configurations
    loggers = {
        "": {  # Root logger
            "level": log_level,
            "handlers": ["console"] + (["file"] if log_dir else [])
        },
        "audit": {
            "level": "INFO",
            "handlers": ["audit"] + (["audit_file"] if log_dir else []),
            "propagate": False
        },
        "security": {
            "level": "WARNING",
            "handlers": ["security"] + (["security_file"] if log_dir else []),
            "propagate": False
        },
        "performance": {
            "level": "INFO",
            "handlers": ["performance"],
            "propagate": False
        },
        # Application service loggers - Suppress verbose logs
        "app.services.cognito": {
            "level": "ERROR",
            "propagate": False
        },
        # Third-party logger configurations - Suppress verbose logs
        "uvicorn": {
            "level": "WARNING",
            "propagate": False
        },
        "uvicorn.access": {
            "level": "WARNING",
            "propagate": False
        },
        "uvicorn.error": {
            "level": "WARNING",
            "propagate": False
        },
        "sqlalchemy.engine": {
            "level": "ERROR",
            "propagate": False
        },
        "sqlalchemy.pool": {
            "level": "ERROR",
            "propagate": False
        },
        "httpcore": {
            "level": "ERROR",
            "propagate": False
        },
        "httpx": {
            "level": "ERROR",
            "propagate": False
        },
        "stripe": {
            "level": "WARNING",
            "propagate": True
        },
        "urllib3": {
            "level": "WARNING",
            "propagate": True
        },
        "requests": {
            "level": "WARNING",
            "propagate": True
        },
        "boto3": {
            "level": "WARNING",
            "propagate": True
        },
        "botocore": {
            "level": "WARNING",
            "propagate": True
        }
    }
    
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": formatters,
        "handlers": handlers,
        "loggers": loggers
    }


def setup_advanced_logging(debug: bool = False) -> None:
    """
    Set up advanced logging configuration using dictConfig.
    
    Args:
        debug: Whether to enable debug logging
    """
    config = get_logging_config(debug)
    logging.config.dictConfig(config)
    
    # Don't log configuration completion - keep output minimal


# Logging context manager for request tracing
class LoggingContext:
    """
    Context manager for adding request-specific context to all log messages.
    """
    
    def __init__(
        self,
        correlation_id: str = None,
        user_id: str = None,
        request_path: str = None,
        **kwargs
    ):
        self.context = {
            "correlation_id": correlation_id,
            "user_id": user_id,
            "request_path": request_path,
            **kwargs
        }
        self.old_factory = None
    
    def __enter__(self):
        self.old_factory = logging.getLogRecordFactory()
        
        def record_factory(*args, **kwargs):
            record = self.old_factory(*args, **kwargs)
            for key, value in self.context.items():
                if value is not None:
                    setattr(record, key, value)
            return record
        
        logging.setLogRecordFactory(record_factory)
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        logging.setLogRecordFactory(self.old_factory)


# Decorator for automatic performance logging
def log_performance(operation_name: str = None):
    """
    Decorator to automatically log function performance.
    
    Args:
        operation_name: Custom operation name (defaults to function name)
    """
    def decorator(func):
        import functools
        import time
        from app.utils import log_performance_metric
        
        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            start_time = time.time()
            op_name = operation_name or f"{func.__module__}.{func.__name__}"
            
            try:
                result = await func(*args, **kwargs)
                duration_ms = (time.time() - start_time) * 1000
                log_performance_metric(op_name, duration_ms)
                return result
            except Exception as e:
                duration_ms = (time.time() - start_time) * 1000
                log_performance_metric(
                    op_name,
                    duration_ms,
                    metadata={"error": str(e), "exception_type": type(e).__name__}
                )
                raise
        
        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            start_time = time.time()
            op_name = operation_name or f"{func.__module__}.{func.__name__}"
            
            try:
                result = func(*args, **kwargs)
                duration_ms = (time.time() - start_time) * 1000
                log_performance_metric(op_name, duration_ms)
                return result
            except Exception as e:
                duration_ms = (time.time() - start_time) * 1000
                log_performance_metric(
                    op_name,
                    duration_ms,
                    metadata={"error": str(e), "exception_type": type(e).__name__}
                )
                raise
        
        # Return appropriate wrapper based on function type
        import asyncio
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper
    
    return decorator