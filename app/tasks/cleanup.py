"""
Background tasks for database cleanup and maintenance.
"""

import logging
from datetime import datetime, timedelta
from sqlmodel import Session, select
from app.database import engine
from app.models import User

logger = logging.getLogger(__name__)


def cleanup_pending_users(days_old: int = 7) -> dict:
    """
    Clean up pending users older than specified days.
    
    Pending users are users who signed up but never verified their email
    or signed in. They have cognito_sub starting with "pending-".
    
    Args:
        days_old: Delete pending users older than this many days (default: 7)
        
    Returns:
        dict with cleanup statistics
    """
    cutoff_date = datetime.utcnow() - timedelta(days=days_old)
    
    stats = {
        "cutoff_date": cutoff_date.isoformat(),
        "deleted_count": 0,
        "errors": []
    }
    
    try:
        with Session(engine) as session:
            # Find all pending users older than cutoff date
            pending_users = session.exec(
                select(User).where(
                    sql_func.lower(User.cognito_sub).like("pending-%"),
                    User.created_at < cutoff_date
                )
            ).all()
            
            deleted_count = 0
            for user in pending_users:
                try:
                    logger.info(
                        "🗑️  Deleting old pending user: email=%s, created_at=%s, age_days=%.1f",
                        user.email,
                        user.created_at.isoformat() if user.created_at else "unknown",
                        (datetime.utcnow() - user.created_at).total_seconds() / 86400 if user.created_at else 0
                    )
                    session.delete(user)
                    deleted_count += 1
                except Exception as e:
                    error_msg = f"Failed to delete pending user {user.email}: {str(e)}"
                    logger.error("❌ %s", error_msg)
                    stats["errors"].append(error_msg)
            
            if deleted_count > 0:
                session.commit()
                logger.info("✅ Cleaned up %d old pending users", deleted_count)
            else:
                logger.debug("ℹ️  No old pending users to clean up")
            
            stats["deleted_count"] = deleted_count
            
    except Exception as e:
        error_msg = f"Error during pending user cleanup: {str(e)}"
        logger.error("❌ %s", error_msg, exc_info=True)
        stats["errors"].append(error_msg)
    
    return stats


def run_cleanup_job():
    """
    Run all cleanup jobs.
    Called by scheduler or manually.
    """
    logger.info("🧹 Starting cleanup jobs...")
    
    results = {
        "pending_users": cleanup_pending_users(days_old=7),
        "timestamp": datetime.utcnow().isoformat()
    }
    
    total_deleted = results["pending_users"]["deleted_count"]
    total_errors = len(results["pending_users"]["errors"])
    
    logger.info(
        "✅ Cleanup jobs completed: deleted=%d, errors=%d",
        total_deleted, total_errors
    )
    
    return results

