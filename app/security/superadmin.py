"""
Helper utilities for managing superadmin accounts.

SECURITY NOTE: In production, superadmin emails MUST be configured via
the SUPERADMIN_EMAILS environment variable. The hardcoded defaults are
ONLY used in development/local environments for convenience.
"""

from __future__ import annotations

import logging
import os
from typing import List, Optional

from app.config import settings

logger = logging.getLogger(__name__)

# Default fallback list used ONLY in development when environment variables are not provided
# These are DISABLED in production for security - you MUST configure SUPERADMIN_EMAILS
_DEFAULT_SUPERADMIN_EMAILS: List[str] = [
    "vraj.suthar+admin@thelinetech.uk",
    "info@thelinetech.uk",
]

# DEPRECATED: Kept for backwards compatibility but should use _DEFAULT_SUPERADMIN_EMAILS
DEFAULT_SUPERADMIN_EMAILS = _DEFAULT_SUPERADMIN_EMAILS


def normalize_email(email: Optional[str]) -> Optional[str]:
    """Normalize email addresses for consistent comparisons."""
    if not email:
        return None
    return email.strip().lower()


def _unique(sequence: List[str]) -> List[str]:
    """Return list with duplicates removed while preserving order."""
    seen = set()
    unique_items: List[str] = []
    for item in sequence:
        if item not in seen:
            seen.add(item)
            unique_items.append(item)
    return unique_items


def get_superadmin_emails() -> List[str]:
    """
    Return the configured list of superadmin emails (normalized).

    Reads from Settings.superadmin_emails and:
    - In PRODUCTION: Returns ONLY explicitly configured emails (no fallbacks)
    - In DEVELOPMENT: Falls back to _DEFAULT_SUPERADMIN_EMAILS if not configured
    
    Returns:
        List of normalized email addresses that have superadmin privileges.
    """
    configured = settings.superadmin_emails_list
    is_production = os.getenv("ENVIRONMENT", "").lower() == "production"
    
    if configured:
        # Explicitly configured - use these
        emails = configured
    elif is_production:
        # Production WITHOUT configuration - NO fallback for security
        logger.warning(
            "⚠️  SUPERADMIN_EMAILS not configured in production. "
            "No superadmin accounts will be available. "
            "Set SUPERADMIN_EMAILS environment variable to enable superadmin access."
        )
        emails = []
    else:
        # Development - use fallback defaults
        logger.debug(
            "Using default superadmin emails in development: %s",
            _DEFAULT_SUPERADMIN_EMAILS
        )
        emails = _DEFAULT_SUPERADMIN_EMAILS
    
    normalized = [normalize_email(email) for email in emails if normalize_email(email)]
    return _unique(normalized)


def get_primary_superadmin_email() -> Optional[str]:
    """Return the first configured superadmin email (normalized)."""
    emails = get_superadmin_emails()
    return emails[0] if emails else None


def is_superadmin_email(email: Optional[str]) -> bool:
    """Check if the supplied email belongs to a configured superadmin."""
    normalized = normalize_email(email)
    if not normalized:
        return False
    return normalized in get_superadmin_emails()


