"""
Access Control Logging Service

Provides comprehensive logging and pattern detection for access control events.
Tracks successful and denied access attempts for security monitoring.
"""

import logging
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta
from collections import defaultdict
from uuid import UUID

from app.models import User

logger = logging.getLogger(__name__)


class AccessLogService:
    """
    Service for logging and analyzing access control events.
    
    Provides structured logging for security monitoring and pattern detection
    to identify potential security threats or unauthorized access attempts.
    """
    
    # In-memory storage for recent access attempts (in production, use Redis)
    _recent_attempts: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    _max_attempts_per_user = 100  # Keep last 100 attempts per user
    
    @classmethod
    def log_access_attempt(
        cls,
        action: str,
        user: User,
        resource_type: str,
        resource_id: Optional[UUID] = None,
        access_granted: bool = True,
        reason: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ):
        """
        Log an access control event with structured data.
        
        Args:
            action: Action being performed (e.g., "view_job", "update_job", "list_jobs")
            user: User attempting the action
            resource_type: Type of resource (e.g., "job", "client_profile", "dropper_profile")
            resource_id: ID of the specific resource (if applicable)
            access_granted: Whether access was granted or denied
            reason: Reason for denial (if access_granted=False)
            metadata: Additional context (e.g., endpoint, filters, counts)
        """
        log_level = logging.INFO if access_granted else logging.WARNING
        status = "GRANTED" if access_granted else "DENIED"
        timestamp = datetime.utcnow()
        
        # Build structured log data
        log_data = {
            "event": "access_control",
            "action": action,
            "status": status,
            "resource_type": resource_type,
            "user_id": str(user.id),
            "user_email": user.email,
            "user_role": user.role.value,
            "timestamp": timestamp.isoformat()
        }
        
        if resource_id:
            log_data["resource_id"] = str(resource_id)
        
        if reason:
            log_data["reason"] = reason
        
        if metadata:
            log_data["metadata"] = metadata
        
        # Format as structured log message
        log_message = f"ACCESS_CONTROL: {status} - {action} on {resource_type} | "
        log_message += f"user={user.email}({user.role.value}) "
        
        if resource_id:
            log_message += f"resource={resource_id} "
        
        if reason:
            log_message += f"reason='{reason}' "
        
        if metadata:
            log_message += f"metadata={metadata}"
        
        # Log the event
        logger.log(log_level, log_message, extra=log_data)
        
        # Store in recent attempts for pattern detection
        user_key = str(user.id)
        cls._recent_attempts[user_key].append({
            "timestamp": timestamp,
            "action": action,
            "resource_type": resource_type,
            "resource_id": str(resource_id) if resource_id else None,
            "access_granted": access_granted,
            "reason": reason
        })
        
        # Keep only recent attempts
        if len(cls._recent_attempts[user_key]) > cls._max_attempts_per_user:
            cls._recent_attempts[user_key] = cls._recent_attempts[user_key][-cls._max_attempts_per_user:]
        
        # Check for suspicious patterns
        if not access_granted:
            cls._check_for_suspicious_patterns(user, resource_type, resource_id)
    
    @classmethod
    def _check_for_suspicious_patterns(
        cls,
        user: User,
        resource_type: str,
        resource_id: Optional[UUID]
    ):
        """
        Check for suspicious access patterns that may indicate security threats.
        
        Detects:
        - Multiple denied access attempts in short time period
        - Attempts to access many different resources
        - Repeated attempts to access the same unauthorized resource
        """
        user_key = str(user.id)
        recent = cls._recent_attempts.get(user_key, [])
        
        if not recent:
            return
        
        # Check last 5 minutes
        five_minutes_ago = datetime.utcnow() - timedelta(minutes=5)
        recent_denials = [
            attempt for attempt in recent
            if not attempt["access_granted"] and attempt["timestamp"] > five_minutes_ago
        ]
        
        # Alert on multiple denials in short time
        if len(recent_denials) >= 5:
            logger.warning(
                f"🚨 SECURITY ALERT: User {user.email} ({user.id}) has {len(recent_denials)} "
                f"denied access attempts in the last 5 minutes. Possible unauthorized access attempt."
            )
        
        # Check for repeated attempts on same resource
        if resource_id:
            same_resource_denials = [
                attempt for attempt in recent_denials
                if attempt["resource_id"] == str(resource_id)
            ]
            
            if len(same_resource_denials) >= 3:
                logger.warning(
                    f"🚨 SECURITY ALERT: User {user.email} ({user.id}) has {len(same_resource_denials)} "
                    f"denied attempts to access {resource_type} {resource_id}. "
                    f"Possible targeted unauthorized access attempt."
                )
    
    @classmethod
    def get_user_access_summary(cls, user_id: UUID, hours: int = 24) -> Dict[str, Any]:
        """
        Get summary of access attempts for a user in the last N hours.
        
        Args:
            user_id: User ID to get summary for
            hours: Number of hours to look back (default 24)
            
        Returns:
            Dictionary with access attempt statistics
        """
        user_key = str(user_id)
        recent = cls._recent_attempts.get(user_key, [])
        
        cutoff_time = datetime.utcnow() - timedelta(hours=hours)
        relevant_attempts = [
            attempt for attempt in recent
            if attempt["timestamp"] > cutoff_time
        ]
        
        granted = sum(1 for a in relevant_attempts if a["access_granted"])
        denied = sum(1 for a in relevant_attempts if not a["access_granted"])
        
        # Group by resource type
        by_resource_type = defaultdict(lambda: {"granted": 0, "denied": 0})
        for attempt in relevant_attempts:
            resource_type = attempt["resource_type"]
            if attempt["access_granted"]:
                by_resource_type[resource_type]["granted"] += 1
            else:
                by_resource_type[resource_type]["denied"] += 1
        
        return {
            "user_id": str(user_id),
            "period_hours": hours,
            "total_attempts": len(relevant_attempts),
            "granted": granted,
            "denied": denied,
            "by_resource_type": dict(by_resource_type),
            "recent_denials": [
                {
                    "timestamp": a["timestamp"].isoformat(),
                    "action": a["action"],
                    "resource_type": a["resource_type"],
                    "resource_id": a["resource_id"],
                    "reason": a["reason"]
                }
                for a in relevant_attempts
                if not a["access_granted"]
            ][-10:]  # Last 10 denials
        }
    
    @classmethod
    def clear_user_history(cls, user_id: UUID):
        """Clear access history for a user (useful for testing or privacy)."""
        user_key = str(user_id)
        if user_key in cls._recent_attempts:
            del cls._recent_attempts[user_key]
            logger.info(f"Cleared access history for user {user_id}")


# Convenience function for backward compatibility
def log_access_control(
    action: str,
    user: User,
    job_id: Optional[UUID] = None,
    job_client_id: Optional[UUID] = None,
    access_granted: bool = True,
    reason: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None
):
    """
    Log access control events for job resources.
    
    This is a convenience wrapper around AccessLogService.log_access_attempt()
    specifically for job-related access control.
    
    Args:
        action: Action being performed (e.g., "view_job", "update_job", "list_jobs")
        user: User attempting the action
        job_id: Job ID being accessed (if applicable)
        job_client_id: Client ID who owns the job (if applicable)
        access_granted: Whether access was granted or denied
        reason: Reason for denial (if access_granted=False)
        metadata: Additional context (e.g., endpoint, filters, counts)
    """
    # Add job_client_id to metadata if provided
    if job_client_id and metadata is None:
        metadata = {}
    if job_client_id:
        if metadata is None:
            metadata = {}
        metadata["job_client_id"] = str(job_client_id)
    
    AccessLogService.log_access_attempt(
        action=action,
        user=user,
        resource_type="job",
        resource_id=job_id,
        access_granted=access_granted,
        reason=reason,
        metadata=metadata
    )
