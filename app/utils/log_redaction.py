"""
HIGH-RISK FIX 4: Sensitive data redaction for logging.
Masks PII and sensitive identifiers in log messages.
"""

import re
from typing import Any


def redact_email(email: str) -> str:
    """
    Redact email address for logging.
    
    Examples:
        user@example.com -> u***@example.com
        admin@test.co.uk -> a***@test.co.uk
    """
    if not email or '@' not in email:
        return email
    
    local, domain = email.split('@', 1)
    if len(local) <= 1:
        return f"{local[0]}***@{domain}"
    return f"{local[0]}***@{domain}"


def redact_stripe_id(stripe_id: str) -> str:
    """
    Redact Stripe ID for logging.
    
    Examples:
        cus_1234567890abcdef -> cus_***cdef
        pi_1234567890abcdef -> pi_***cdef
        sk_live_1234567890 -> sk_live_***
    """
    if not stripe_id or len(stripe_id) < 8:
        return stripe_id
    
    # Keep prefix and last 4 chars
    if '_' in stripe_id:
        prefix = stripe_id.split('_')[0] + '_'
        if len(stripe_id) > len(prefix) + 4:
            return f"{prefix}***{stripe_id[-4:]}"
        return f"{prefix}***"
    
    return f"{stripe_id[:4]}***{stripe_id[-4:]}"


def redact_jwt_token(token: str) -> str:
    """
    Redact JWT token for logging.
    
    Examples:
        eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9... -> eyJ***[JWT]
    """
    if not token or len(token) < 10:
        return token
    
    return f"{token[:3]}***[JWT]"


def redact_sensitive_data(message: str) -> str:
    """
    Redact sensitive data from log message.
    
    Automatically detects and redacts:
    - Email addresses
    - JWT tokens (Bearer tokens)
    - Stripe IDs
    """
    # Redact email addresses
    message = re.sub(
        r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b',
        lambda m: redact_email(m.group(0)),
        message
    )
    
    # Redact Bearer tokens
    message = re.sub(
        r'Bearer\s+([A-Za-z0-9_\-\.]+)',
        lambda m: f"Bearer {redact_jwt_token(m.group(1))}",
        message,
        flags=re.IGNORECASE
    )
    
    # Redact Stripe IDs (cus_, pi_, sk_, etc.)
    message = re.sub(
        r'\b(cus|pi|sk|pm|ch|re|tr|acct|ba|card|src|tok|sub|in|price|prod)_[A-Za-z0-9]+',
        lambda m: redact_stripe_id(m.group(0)),
        message
    )
    
    return message


def safe_log_format(message: str, *args: Any, **kwargs: Any) -> str:
    """
    Format log message with automatic redaction.
    
    Usage:
        logger.info(safe_log_format("User %s logged in", user.email))
    """
    # Format the message with args
    if args:
        try:
            formatted = message % args
        except (TypeError, ValueError):
            formatted = f"{message} {args}"
    else:
        formatted = message
    
    # Redact sensitive data
    return redact_sensitive_data(formatted)
