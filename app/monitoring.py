"""
Monitoring and observability utilities for DropTrack API.

Provides:
- Error tracking (Sentry-ready)
- Metrics collection
- Performance monitoring
"""

import logging
import os
from typing import Optional, Dict, Any
from functools import wraps
import time

logger = logging.getLogger(__name__)

# Sentry integration (optional - only if SENTRY_DSN is set)
_sentry_initialized = False
_sentry_sdk = None

def init_error_tracking():
    """Initialize error tracking (Sentry) if DSN is configured."""
    global _sentry_initialized, _sentry_sdk
    
    sentry_dsn = os.getenv("SENTRY_DSN")
    if not sentry_dsn:
        logger.info("Sentry DSN not configured. Error tracking disabled.")
        return
    
    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration
        from sentry_sdk.integrations.logging import LoggingIntegration
        
        sentry_sdk.init(
            dsn=sentry_dsn,
            integrations=[
                FastApiIntegration(),
                SqlalchemyIntegration(),
                LoggingIntegration(
                    level=logging.INFO,
                    event_level=logging.ERROR
                ),
            ],
            traces_sample_rate=float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.1")),
            environment=os.getenv("ENVIRONMENT", "development"),
            release=os.getenv("APP_VERSION", "1.0.0"),
        )
        
        _sentry_sdk = sentry_sdk
        _sentry_initialized = True
        logger.info("✅ Sentry error tracking initialized")
    except ImportError:
        logger.warning("⚠️  Sentry SDK not installed. Install with: pip install sentry-sdk[fastapi]")
    except Exception as e:
        logger.error(f"❌ Failed to initialize Sentry: {e}")


def capture_exception(error: Exception, context: Optional[Dict[str, Any]] = None):
    """Capture an exception for error tracking."""
    if _sentry_initialized and _sentry_sdk:
        try:
            with _sentry_sdk.push_scope() as scope:
                if context:
                    for key, value in context.items():
                        scope.set_context(key, value)
                _sentry_sdk.capture_exception(error)
        except Exception as e:
            logger.error(f"Failed to capture exception in Sentry: {e}")
    else:
        # Fallback to logging
        logger.error(f"Exception occurred: {error}", exc_info=True, extra=context)


def capture_message(message: str, level: str = "info", context: Optional[Dict[str, Any]] = None):
    """Capture a message for error tracking."""
    if _sentry_initialized and _sentry_sdk:
        try:
            with _sentry_sdk.push_scope() as scope:
                if context:
                    for key, value in context.items():
                        scope.set_context(key, value)
                _sentry_sdk.capture_message(message, level=level)
        except Exception as e:
            logger.error(f"Failed to capture message in Sentry: {e}")
    else:
        # Fallback to logging
        log_level = getattr(logging, level.upper(), logging.INFO)
        logger.log(log_level, message, extra=context)


def track_performance(operation_name: str):
    """Decorator to track performance metrics."""
    def decorator(func):
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            start_time = time.time()
            try:
                result = await func(*args, **kwargs)
                duration = time.time() - start_time
                
                # Log performance metric
                logger.info(
                    f"Performance: {operation_name}",
                    extra={
                        "operation": operation_name,
                        "duration_ms": round(duration * 1000, 2),
                        "status": "success"
                    }
                )
                
                # Track in Sentry if available
                if _sentry_initialized:
                    _sentry_sdk.set_measurement(operation_name, duration, unit="second")
                
                return result
            except Exception as e:
                duration = time.time() - start_time
                logger.error(
                    f"Performance: {operation_name} failed",
                    extra={
                        "operation": operation_name,
                        "duration_ms": round(duration * 1000, 2),
                        "status": "error",
                        "error": str(e)
                    }
                )
                raise
        
        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            start_time = time.time()
            try:
                result = func(*args, **kwargs)
                duration = time.time() - start_time
                
                logger.info(
                    f"Performance: {operation_name}",
                    extra={
                        "operation": operation_name,
                        "duration_ms": round(duration * 1000, 2),
                        "status": "success"
                    }
                )
                
                return result
            except Exception as e:
                duration = time.time() - start_time
                logger.error(
                    f"Performance: {operation_name} failed",
                    extra={
                        "operation": operation_name,
                        "duration_ms": round(duration * 1000, 2),
                        "status": "error",
                        "error": str(e)
                    }
                )
                raise
        
        import asyncio
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper
    
    return decorator


# Initialize error tracking on module import
init_error_tracking()









