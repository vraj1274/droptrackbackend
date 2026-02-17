"""
Saved Jobs API endpoints.
Allows users to save/bookmark jobs for later viewing.
"""

import logging
from uuid import UUID
from datetime import datetime
from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select
from pydantic import BaseModel

from app.database import get_session
from app.models import User, DropJob, SavedJob
from app.api.deps import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(tags=["saved-jobs"])


class SavedJobResponse(BaseModel):
    """Response schema for saved job."""
    id: str
    job_id: str
    saved_at: str
    job: dict | None = None

    class Config:
        from_attributes = True


@router.get("/", response_model=List[SavedJobResponse])
async def get_saved_jobs(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session)
):
    """Get all saved jobs for the current user."""
    try:
        # OPTIMIZATION: Use separate queries for better performance
        # First, get saved jobs for the user (limited to prevent timeout)
        saved_jobs_list = db.exec(
            select(SavedJob)
            .where(SavedJob.user_id == current_user.id)
            .order_by(SavedJob.saved_at.desc())
            .limit(100)  # Limit to prevent timeout
        ).all()
        
        if not saved_jobs_list:
            return []
        
        # Extract job IDs
        job_ids = [saved.job_id for saved in saved_jobs_list]
        
        # Fetch jobs in a single query using IN clause (more efficient than left join)
        jobs = db.exec(
            select(DropJob).where(DropJob.id.in_(job_ids))  # type: ignore[attr-defined]
        ).all()
        
        # Create a map of job_id -> job for quick lookup
        job_map = {job.id: job for job in jobs}
        
        # Build results maintaining order
        results = []
        for saved_job in saved_jobs_list:
            job = job_map.get(saved_job.job_id)
            if job:  # Only include jobs that still exist
                results.append((saved_job, job))
    except Exception as e:
        logger.error(f"Error fetching saved jobs: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch saved jobs: {str(e)}"
        )
    
    saved_jobs = []
    for result in results:
        # Handle tuple result from join
        if isinstance(result, tuple):
            saved_job, job = result
        else:
            saved_job = result
            job = None
        
        # Skip if job was deleted (left join returns None)
        if not job:
            continue
            
        saved_jobs.append(SavedJobResponse(
            id=str(saved_job.id),
            job_id=str(saved_job.job_id),
            saved_at=saved_job.saved_at.isoformat() if saved_job.saved_at else datetime.utcnow().isoformat(),
            job={
                "id": str(job.id),
                "title": job.title,
                "description": job.description,
                "status": job.status.value,
                "household_count": job.household_count,
                "cost_total_pence": job.cost_total_pence,
                "scheduled_date": job.scheduled_date.isoformat() if job.scheduled_date else None,
            }
        ))
    
    return saved_jobs


@router.post("/{job_id}", response_model=SavedJobResponse)
async def save_job(
    job_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session)
):
    """Save/bookmark a job."""
    # Check if job exists
    job = db.exec(select(DropJob).where(DropJob.id == job_id)).first()
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job not found"
        )
    
    # Check if already saved
    existing = db.exec(
        select(SavedJob).where(
            SavedJob.user_id == current_user.id,
            SavedJob.job_id == job_id
        )
    ).first()
    
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Job is already saved"
        )
    
    # Create saved job
    saved_job = SavedJob(
        user_id=current_user.id,
        job_id=job_id,
        saved_at=datetime.utcnow()
    )
    
    db.add(saved_job)
    db.commit()
    db.refresh(saved_job)
    
    return SavedJobResponse(
        id=str(saved_job.id),
        job_id=str(saved_job.job_id),
        saved_at=saved_job.saved_at.isoformat() if saved_job.saved_at else datetime.utcnow().isoformat(),
        job={
            "id": str(job.id),
            "title": job.title,
            "description": job.description,
            "status": job.status.value,
            "household_count": job.household_count,
            "cost_total_pence": job.cost_total_pence,
            "scheduled_date": job.scheduled_date.isoformat() if job.scheduled_date else None,
        }
    )


@router.delete("/{job_id}")
async def unsave_job(
    job_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session)
):
    """Remove a job from saved list."""
    try:
        saved_job = db.exec(
            select(SavedJob).where(
                SavedJob.user_id == current_user.id,
                SavedJob.job_id == job_id
            )
        ).first()
        
        if not saved_job:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Saved job not found"
            )
        
        db.delete(saved_job)
        db.commit()
        
        return {"success": True, "message": "Job removed from saved list"}
    except HTTPException:
        # Re-raise HTTP exceptions (like 404)
        raise
    except Exception as e:
        logger.error(f"Error unsaving job {job_id} for user {current_user.id}: {str(e)}")
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to unsave job: {str(e)}"
        )


@router.get("/{job_id}/status")
async def check_saved_status(
    job_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session)
):
    """Check if a job is saved by the current user."""
    saved_job = db.exec(
        select(SavedJob).where(
            SavedJob.user_id == current_user.id,
            SavedJob.job_id == job_id
        )
    ).first()
    
    return {"is_saved": saved_job is not None}





