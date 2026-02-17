"""
Dispute management API endpoints.
Handles dispute creation, resolution, and tracking.
"""

from typing import List, Optional, Dict, Any
from uuid import UUID
from datetime import datetime
import logging
from fastapi import APIRouter, Depends, HTTPException, status, Body, Path, Query
from sqlmodel import Session, select, or_
from sqlalchemy import desc

from app.database import get_session
from app.models import Dispute, DisputeNote, User, DropJob, JobAssignment
from app.api.deps import get_current_active_user, require_admin_role, require_client_or_admin, require_dropper_or_admin

logger = logging.getLogger(__name__)

router = APIRouter(tags=["disputes"])


# ====================================================================
# Dispute CRUD Endpoints
# ====================================================================

@router.get(
    "/",
    summary="Get disputes",
    description="Fetch disputes based on user role and filters"
)
async def get_disputes(
    status_filter: Optional[str] = Query(None, description="Filter by status: pending, resolved, escalated"),
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_session)
):
    """Get disputes based on user role."""
    try:
        statement = select(Dispute)
        
        # Filter by role
        if current_user.role == UserRole.CLIENT:
            statement = statement.where(Dispute.client_id == current_user.id)
        elif current_user.role == UserRole.DROPPER:
            statement = statement.where(Dispute.dropper_id == current_user.id)
        # Admin sees all disputes
        
        # Filter by status
        if status_filter:
            statement = statement.where(Dispute.status == status_filter)
        
        statement = statement.order_by(desc(Dispute.created_at))
        
        disputes = db.exec(statement).all()
        
        # Get related job and user info
        result = []
        for dispute in disputes:
            job = db.exec(select(DropJob).where(DropJob.id == dispute.job_id)).first()
            client = db.exec(select(User).where(User.id == dispute.client_id)).first()
            dropper = db.exec(select(User).where(User.id == dispute.dropper_id)).first()
            
            result.append({
                "id": str(dispute.id),
                "jobId": str(dispute.job_id),
                "clientId": str(dispute.client_id),
                "dropperId": str(dispute.dropper_id),
                "clientName": client.name if client else "Unknown",
                "dropperName": dropper.name if dropper else "Unknown",
                "jobTitle": job.title if job else "Unknown Job",
                "reason": dispute.reason,
                "description": dispute.description,
                "photos": dispute.photos or [],
                "status": dispute.status,
                "createdAt": dispute.created_at.isoformat(),
                "resolvedAt": dispute.resolved_at.isoformat() if dispute.resolved_at else None,
                "amount": dispute.amount_pence / 100,  # Convert to dollars
                "refundAmount": dispute.refund_amount_pence / 100 if dispute.refund_amount_pence else None,
                "resolution": dispute.resolution,
                "assignedTo": str(dispute.assigned_to) if dispute.assigned_to else None
            })
        
        return result
    except Exception as e:
        logger.error(f"Error fetching disputes: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch disputes: {str(e)}"
        )


@router.get(
    "/{dispute_id}",
    summary="Get dispute details",
    description="Fetch detailed information about a specific dispute"
)
async def get_dispute_details(
    dispute_id: UUID,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_session)
):
    """Get detailed dispute information including notes."""
    try:
        dispute = db.exec(select(Dispute).where(Dispute.id == dispute_id)).first()
        if not dispute:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Dispute not found"
            )
        
        # Check access permissions
        if current_user.role == UserRole.CLIENT and dispute.client_id != current_user.id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
        if current_user.role == UserRole.DROPPER and dispute.dropper_id != current_user.id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
        
        # Get related info
        job = db.exec(select(DropJob).where(DropJob.id == dispute.job_id)).first()
        client = db.exec(select(User).where(User.id == dispute.client_id)).first()
        dropper = db.exec(select(User).where(User.id == dispute.dropper_id)).first()
        
        # Get notes
        notes = db.exec(
            select(DisputeNote)
            .where(DisputeNote.dispute_id == dispute_id)
            .order_by(DisputeNote.created_at.desc())
        ).all()
        
        notes_data = []
        for note in notes:
            note_creator = db.exec(select(User).where(User.id == note.created_by)).first()
            notes_data.append({
                "id": str(note.id),
                "note": note.note,
                "isInternal": note.is_internal,
                "createdBy": note_creator.name if note_creator else "Unknown",
                "createdAt": note.created_at.isoformat()
            })
        
        return {
            "id": str(dispute.id),
            "jobId": str(dispute.job_id),
            "clientId": str(dispute.client_id),
            "dropperId": str(dispute.dropper_id),
            "clientName": client.name if client else "Unknown",
            "dropperName": dropper.name if dropper else "Unknown",
            "jobTitle": job.title if job else "Unknown Job",
            "reason": dispute.reason,
            "description": dispute.description,
            "photos": dispute.photos or [],
            "status": dispute.status,
            "createdAt": dispute.created_at.isoformat(),
            "resolvedAt": dispute.resolved_at.isoformat() if dispute.resolved_at else None,
            "amount": dispute.amount_pence / 100,
            "refundAmount": dispute.refund_amount_pence / 100 if dispute.refund_amount_pence else None,
            "resolution": dispute.resolution,
            "assignedTo": str(dispute.assigned_to) if dispute.assigned_to else None,
            "notes": notes_data
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching dispute details: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch dispute details: {str(e)}"
        )


@router.post(
    "/",
    summary="Create dispute",
    description="Create a new dispute for a job",
    status_code=status.HTTP_201_CREATED
)
async def create_dispute(
    dispute_data: Dict[str, Any] = Body(...),
    current_user: User = Depends(require_client_or_admin()),
    db: Session = Depends(get_session)
):
    """Create a new dispute (clients can create disputes)."""
    try:
        job_id = UUID(dispute_data["jobId"])
        job = db.exec(select(DropJob).where(DropJob.id == job_id)).first()
        if not job:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Job not found"
            )
        
        # Verify client owns the job
        if current_user.role == UserRole.CLIENT and job.client_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You can only create disputes for your own jobs"
            )
        
        # Get dropper from job assignment
        assignment = db.exec(
            select(JobAssignment).where(JobAssignment.job_id == job_id)
        ).first()
        if not assignment:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Job must be assigned to a dropper before creating a dispute"
            )
        
        dropper_id = assignment.dropper_id
        client_id = job.client_id if current_user.role == UserRole.ADMIN else current_user.id
        
        new_dispute = Dispute(
            job_id=job_id,
            client_id=client_id,
            dropper_id=dropper_id,
            reason=dispute_data["reason"],
            description=dispute_data["description"],
            photos=dispute_data.get("photos", []),
            status="pending",
            amount_pence=int(dispute_data["amount"] * 100),  # Convert to pence
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )
        
        db.add(new_dispute)
        db.commit()
        db.refresh(new_dispute)
        
        return {
            "id": str(new_dispute.id),
            "jobId": str(new_dispute.job_id),
            "status": new_dispute.status,
            "createdAt": new_dispute.created_at.isoformat()
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating dispute: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create dispute: {str(e)}"
        )


@router.post(
    "/{dispute_id}/approve",
    summary="Approve dispute",
    description="Approve a dispute and issue refund (admin only)"
)
async def approve_dispute(
    dispute_id: UUID,
    resolution_data: Dict[str, Any] = Body(...),
    current_user: User = Depends(require_admin_role()),
    db: Session = Depends(get_session)
):
    """Approve a dispute and issue refund."""
    try:
        dispute = db.exec(select(Dispute).where(Dispute.id == dispute_id)).first()
        if not dispute:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Dispute not found"
            )
        
        if dispute.status != "pending":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Dispute is already {dispute.status}"
            )
        
        dispute.status = "resolved"
        dispute.resolution = resolution_data.get("resolution", "Dispute approved in favor of client")
        dispute.refund_amount_pence = int(resolution_data.get("refundAmount", dispute.amount_pence / 100) * 100)
        dispute.resolved_at = datetime.utcnow()
        dispute.updated_at = datetime.utcnow()
        
        db.add(dispute)
        db.commit()
        db.refresh(dispute)
        
        return {
            "success": True,
            "message": "Dispute approved and resolved in favor of client",
            "data": {
                "disputeId": str(dispute.id),
                "status": dispute.status,
                "resolution": dispute.resolution,
                "refundAmount": dispute.refund_amount_pence / 100,
                "resolvedAt": dispute.resolved_at.isoformat()
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error approving dispute: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to approve dispute: {str(e)}"
        )


@router.post(
    "/{dispute_id}/reject",
    summary="Reject dispute",
    description="Reject a dispute in favor of dropper (admin only)"
)
async def reject_dispute(
    dispute_id: UUID,
    resolution_data: Dict[str, Any] = Body(...),
    current_user: User = Depends(require_admin_role()),
    db: Session = Depends(get_session)
):
    """Reject a dispute in favor of dropper."""
    try:
        dispute = db.exec(select(Dispute).where(Dispute.id == dispute_id)).first()
        if not dispute:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Dispute not found"
            )
        
        if dispute.status != "pending":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Dispute is already {dispute.status}"
            )
        
        dispute.status = "resolved"
        dispute.resolution = resolution_data.get("reason", "Dispute rejected after review")
        dispute.refund_amount_pence = None
        dispute.resolved_at = datetime.utcnow()
        dispute.updated_at = datetime.utcnow()
        
        db.add(dispute)
        db.commit()
        db.refresh(dispute)
        
        return {
            "success": True,
            "message": "Dispute rejected and resolved in favor of dropper",
            "data": {
                "disputeId": str(dispute.id),
                "status": dispute.status,
                "resolution": dispute.resolution,
                "resolvedAt": dispute.resolved_at.isoformat()
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error rejecting dispute: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to reject dispute: {str(e)}"
        )


@router.post(
    "/{dispute_id}/escalate",
    summary="Escalate dispute",
    description="Escalate a dispute for senior review (admin only)"
)
async def escalate_dispute(
    dispute_id: UUID,
    escalation_data: Dict[str, Any] = Body(...),
    current_user: User = Depends(require_admin_role()),
    db: Session = Depends(get_session)
):
    """Escalate a dispute for senior review."""
    try:
        dispute = db.exec(select(Dispute).where(Dispute.id == dispute_id)).first()
        if not dispute:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Dispute not found"
            )
        
        dispute.status = "escalated"
        assigned_to = UUID(escalation_data.get("assignTo", str(current_user.id)))
        dispute.assigned_to = assigned_to
        dispute.updated_at = datetime.utcnow()
        
        db.add(dispute)
        db.commit()
        db.refresh(dispute)
        
        return {
            "success": True,
            "message": "Dispute escalated for senior review",
            "data": {
                "disputeId": str(dispute.id),
                "status": dispute.status,
                "assignedTo": str(dispute.assigned_to),
                "escalatedAt": dispute.updated_at.isoformat()
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error escalating dispute: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to escalate dispute: {str(e)}"
        )


@router.post(
    "/{dispute_id}/notes",
    summary="Add note to dispute",
    description="Add a note to a dispute (admin only for internal notes)"
)
async def add_dispute_note(
    dispute_id: UUID,
    note_data: Dict[str, Any] = Body(...),
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_session)
):
    """Add a note to a dispute."""
    try:
        dispute = db.exec(select(Dispute).where(Dispute.id == dispute_id)).first()
        if not dispute:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Dispute not found"
            )
        
        is_internal = note_data.get("isInternal", False)
        # Only admins can add internal notes
        if is_internal and current_user.role != UserRole.ADMIN:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only admins can add internal notes"
            )
        
        new_note = DisputeNote(
            dispute_id=dispute_id,
            note=note_data["note"],
            is_internal=is_internal,
            created_by=current_user.id,
            created_at=datetime.utcnow()
        )
        
        db.add(new_note)
        db.commit()
        db.refresh(new_note)
        
        return {
            "success": True,
            "message": "Note added to dispute",
            "data": {
                "id": str(new_note.id),
                "disputeId": str(new_note.dispute_id),
                "note": new_note.note,
                "isInternal": new_note.is_internal,
                "createdAt": new_note.created_at.isoformat(),
                "createdBy": current_user.name
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error adding dispute note: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to add note: {str(e)}"
        )


@router.get(
    "/analytics",
    summary="Get dispute analytics",
    description="Get dispute statistics and analytics (admin only)"
)
async def get_dispute_analytics(
    timeframe: str = Query("month", description="Timeframe: week, month, quarter, year"),
    current_user: User = Depends(require_admin_role()),
    db: Session = Depends(get_session)
):
    """Get dispute analytics."""
    try:
        # Calculate date range based on timeframe
        now = datetime.utcnow()
        if timeframe == "week":
            start_date = now.replace(day=now.day - 7)
        elif timeframe == "month":
            start_date = now.replace(month=now.month - 1) if now.month > 1 else now.replace(year=now.year - 1, month=12)
        elif timeframe == "quarter":
            start_date = now.replace(month=now.month - 3) if now.month > 3 else now.replace(year=now.year - 1, month=now.month + 9)
        elif timeframe == "year":
            start_date = now.replace(year=now.year - 1)
        else:
            start_date = now.replace(month=now.month - 1)
        
        all_disputes = db.exec(
            select(Dispute).where(Dispute.created_at >= start_date)
        ).all()
        
        total_disputes = len(all_disputes)
        pending_disputes = len([d for d in all_disputes if d.status == "pending"])
        resolved_disputes = len([d for d in all_disputes if d.status == "resolved"])
        escalated_disputes = len([d for d in all_disputes if d.status == "escalated"])
        
        # Count resolutions by outcome
        resolved_with_refund = len([d for d in all_disputes if d.status == "resolved" and d.refund_amount_pence])
        client_favored = resolved_with_refund
        dropper_favored = resolved_disputes - resolved_with_refund
        
        # Calculate average resolution time
        resolved_with_date = [d for d in all_disputes if d.status == "resolved" and d.resolved_at]
        if resolved_with_date:
            total_days = sum((d.resolved_at - d.created_at).days for d in resolved_with_date)
            avg_days = total_days / len(resolved_with_date)
            avg_resolution_time = f"{avg_days:.1f} days"
        else:
            avg_resolution_time = "N/A"
        
        # Top dispute reasons
        reason_counts = {}
        for dispute in all_disputes:
            reason = dispute.reason
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
        
        top_reasons = sorted(reason_counts.items(), key=lambda x: x[1], reverse=True)[:5]
        
        return {
            "success": True,
            "data": {
                "totalDisputes": total_disputes,
                "pendingDisputes": pending_disputes,
                "resolvedDisputes": resolved_disputes,
                "escalatedDisputes": escalated_disputes,
                "averageResolutionTime": avg_resolution_time,
                "clientFavoredResolutions": client_favored,
                "dropperFavoredResolutions": dropper_favored,
                "topDisputeReasons": [
                    {"reason": reason, "count": count}
                    for reason, count in top_reasons
                ]
            }
        }
    except Exception as e:
        logger.error(f"Error fetching dispute analytics: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch analytics: {str(e)}"
        )

