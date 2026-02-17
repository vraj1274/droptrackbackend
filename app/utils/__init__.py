"""
Utility functions for the DropTrack application.
"""

import logging
import logging.config
import sys
import json
import os
from typing import Dict, Any, Optional
from datetime import datetime
from pythonjsonlogger import jsonlogger


class StructuredFormatter(jsonlogger.JsonFormatter):
    """
    Custom JSON formatter for structured logging.
    Adds consistent fields and formats for all log entries.
    """
    
    def add_fields(self, log_record, record, message_dict):
        super().add_fields(log_record, record, message_dict)
        
        # Add standard fields
        log_record['timestamp'] = datetime.utcnow().isoformat()
        log_record['service'] = 'droptrack-api'
        log_record['level'] = record.levelname
        log_record['logger'] = record.name
        
        # Add request context if available
        if hasattr(record, 'request_id'):
            log_record['request_id'] = record.request_id
        if hasattr(record, 'user_id'):
            log_record['user_id'] = record.user_id
        if hasattr(record, 'path'):
            log_record['path'] = record.path
        if hasattr(record, 'method'):
            log_record['method'] = record.method
        
        # Add correlation ID for tracing
        if hasattr(record, 'correlation_id'):
            log_record['correlation_id'] = record.correlation_id


def setup_logging(debug: bool = False) -> None:
    """
    Configure application logging with structured JSON format and appropriate levels.
    
    Args:
        debug: Whether to enable debug logging
    """
    log_level = logging.DEBUG if debug else logging.INFO
    
    # Determine if we should use JSON logging (production) or simple format (development)
    use_json_logging = not debug and os.getenv("LOG_FORMAT", "json").lower() == "json"
    
    if use_json_logging:
        # Structured JSON logging for production
        formatter = StructuredFormatter(
            fmt='%(timestamp)s %(level)s %(logger)s %(message)s'
        )
    else:
        # Human-readable logging for development
        formatter = logging.Formatter(
            fmt="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
    
    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    
    # Remove existing handlers
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    
    # Create console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)
    
    # Configure audit logger with separate handler if needed
    audit_logger = logging.getLogger("audit")
    audit_logger.setLevel(logging.INFO)
    audit_logger.propagate = False  # Don't propagate to root logger
    
    # Create audit handler (could be file, database, or external service)
    audit_handler = logging.StreamHandler(sys.stdout)
    if use_json_logging:
        audit_formatter = StructuredFormatter(
            fmt='%(timestamp)s %(level)s %(logger)s %(message)s'
        )
    else:
        audit_formatter = logging.Formatter(
            fmt="%(asctime)s - AUDIT - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
    audit_handler.setFormatter(audit_formatter)
    audit_logger.addHandler(audit_handler)
    
    # Set specific logger levels
    logging.getLogger("uvicorn").setLevel(logging.INFO)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING if not debug else logging.INFO)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING if not debug else logging.INFO)
    
    # Suppress noisy third-party loggers
    logging.getLogger("stripe").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)
    logging.getLogger("boto3").setLevel(logging.WARNING)
    logging.getLogger("botocore").setLevel(logging.WARNING)
    
    # Log configuration completion
    logger = logging.getLogger(__name__)
    logger.info(
        f"Logging configured - Level: {log_level}, JSON: {use_json_logging}, Debug: {debug}"
    )


def create_audit_log_entry(
    user_id: str,
    action: str,
    resource_type: str,
    resource_id: str = None,
    metadata: Dict[str, Any] = None,
    correlation_id: str = None,
    client_ip: str = None
) -> Dict[str, Any]:
    """
    Create a structured audit log entry for sensitive operations.
    
    Args:
        user_id: ID of the user performing the action
        action: Action being performed (e.g., 'create', 'update', 'delete')
        resource_type: Type of resource (e.g., 'job', 'user', 'payment')
        resource_id: ID of the specific resource
        metadata: Additional metadata about the action
        correlation_id: Request correlation ID for tracing
        client_ip: Client IP address
        
    Returns:
        Structured audit log entry
    """
    return {
        "timestamp": datetime.utcnow().isoformat(),
        "event_type": "audit",
        "user_id": user_id,
        "action": action,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "correlation_id": correlation_id,
        "client_ip": client_ip,
        "metadata": metadata or {}
    }


def log_audit_event(
    user_id: str,
    action: str,
    resource_type: str,
    resource_id: str = None,
    metadata: Dict[str, Any] = None,
    correlation_id: str = None,
    client_ip: str = None
) -> None:
    """
    Log an audit event for sensitive operations.
    
    Args:
        user_id: ID of the user performing the action
        action: Action being performed
        resource_type: Type of resource
        resource_id: ID of the specific resource
        metadata: Additional metadata
        correlation_id: Request correlation ID for tracing
        client_ip: Client IP address
    """
    audit_logger = logging.getLogger("audit")
    audit_entry = create_audit_log_entry(
        user_id=user_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        metadata=metadata,
        correlation_id=correlation_id,
        client_ip=client_ip
    )
    
    # Log with structured data
    audit_logger.info(
        f"Audit event: {action} on {resource_type}",
        extra=audit_entry
    )


def log_security_event(
    event_type: str,
    message: str,
    user_id: str = None,
    client_ip: str = None,
    correlation_id: str = None,
    metadata: Dict[str, Any] = None
) -> None:
    """
    Log security-related events for monitoring and alerting.
    
    Args:
        event_type: Type of security event (e.g., 'failed_login', 'suspicious_activity')
        message: Human-readable description of the event
        user_id: ID of the user involved (if applicable)
        client_ip: Client IP address
        correlation_id: Request correlation ID for tracing
        metadata: Additional event metadata
    """
    security_logger = logging.getLogger("security")
    
    security_entry = {
        "timestamp": datetime.utcnow().isoformat(),
        "event_type": "security",
        "security_event_type": event_type,
        "message": message,
        "user_id": user_id,
        "client_ip": client_ip,
        "correlation_id": correlation_id,
        "metadata": metadata or {}
    }
    
    security_logger.warning(
        f"Security event: {event_type} - {message}",
        extra=security_entry
    )


def log_performance_metric(
    operation: str,
    duration_ms: float,
    metadata: Dict[str, Any] = None,
    correlation_id: str = None
) -> None:
    """
    Log performance metrics for monitoring and optimization.
    
    Args:
        operation: Name of the operation being measured
        duration_ms: Duration in milliseconds
        metadata: Additional performance metadata
        correlation_id: Request correlation ID for tracing
    """
    perf_logger = logging.getLogger("performance")
    
    perf_entry = {
        "timestamp": datetime.utcnow().isoformat(),
        "event_type": "performance",
        "operation": operation,
        "duration_ms": duration_ms,
        "correlation_id": correlation_id,
        "metadata": metadata or {}
    }
    
    # Log as info for normal operations, warning for slow operations
    log_level = logging.WARNING if duration_ms > 5000 else logging.INFO
    
    perf_logger.log(
        log_level,
        f"Performance: {operation} took {duration_ms:.2f}ms",
        extra=perf_entry
    )


def get_logger_with_context(
    name: str,
    user_id: str = None,
    correlation_id: str = None,
    **kwargs
) -> logging.Logger:
    """
    Get a logger with pre-configured context for consistent logging.
    
    Args:
        name: Logger name
        user_id: User ID to include in all log messages
        correlation_id: Correlation ID to include in all log messages
        **kwargs: Additional context to include
        
    Returns:
        Logger with context adapter
    """
    logger = logging.getLogger(name)
    
    # Create context dictionary
    context = {
        "user_id": user_id,
        "correlation_id": correlation_id,
        **kwargs
    }
    
    # Filter out None values
    context = {k: v for k, v in context.items() if v is not None}
    
    # Return adapter that adds context to all log calls
    return logging.LoggerAdapter(logger, context)


def sanitize_error_message(error: Exception, include_details: bool = False) -> str:
    """
    Sanitize error messages to avoid exposing sensitive information.
    
    Args:
        error: The exception to sanitize
        include_details: Whether to include detailed error information
        
    Returns:
        Sanitized error message
    """
    if include_details:
        return str(error)
    
    # Generic error messages for common exception types
    error_type = type(error).__name__
    
    if "database" in str(error).lower() or "sql" in str(error).lower():
        return "Database operation failed"
    elif "stripe" in str(error).lower():
        return "Payment processing error"
    elif "cognito" in str(error).lower() or "jwt" in str(error).lower():
        return "Authentication error"
    elif error_type in ["ValidationError", "ValueError"]:
        return "Invalid input data"
    else:
        return "An unexpected error occurred"
