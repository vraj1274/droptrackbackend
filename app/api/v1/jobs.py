"""
Job management API endpoints.
Handles job creation, payment, listing, and management for clients.
"""

from typing import List, Optional, Dict, Any
from uuid import UUID
import logging
from fastapi import APIRouter, Depends, HTTPException, status, Query, Request
from pydantic import BaseModel
from datetime import date, timedelta, datetime
from sqlmodel import Session, select

from app.database import get_session
from app.models import User, DropJob, JobStatus, JobAssignment, CostSettings
from app.api.deps import require_client_role, require_client_or_admin, get_current_active_user
from app.schemas.job_schemas import (
    JobCreate, JobUpdate, JobResponse, JobListResponse, PublicJobListResponse,
    JobPaymentRequest, JobPaymentResponse, JobCostCalculation, JobAreaCreate
)
from app.services.job_service import get_job_service, JobServiceError
from app.services.stripe_service import stripe_service

# CRITICAL FIX #4: Import rate limiter for endpoint protection
from slowapi import Limiter
from slowapi.util import get_remote_address

# Initialize logger
logger = logging.getLogger(__name__)

# Initialize rate limiter
limiter = Limiter(key_func=get_remote_address)

router = APIRouter(tags=["jobs"])


# --- GeoJSON validation helpers ---
AUSTRALIA_BOUNDS = {
    "min_lng": 112.0,  # Western Australia
    "min_lat": -44.0,  # Tasmania
    "max_lng": 154.0,  # Eastern Australia
    "max_lat": -10.0,  # Northern Australia
}


def _validate_polygon_geojson(geojson: Dict[str, Any]) -> None:
    """Validate GeoJSON polygon for basic constraints.

    - Must be type Polygon
    - At least 3 unique points (ring)
    - First and last coordinates equal (closed ring) – if not, reject
    - Max vertices: 100
    - All coordinates within Australia bounds
    """
    if not isinstance(geojson, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid geojson format")

    if geojson.get("type") != "Polygon":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="GeoJSON must be of type Polygon")

    coords = geojson.get("coordinates")
    if not coords or not isinstance(coords, list) or not coords[0] or not isinstance(coords[0], list):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid polygon coordinates")

    ring = coords[0]
    if len(ring) < 4:  # need at least 4 with closure
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Polygon must have at least 3 points and be closed")

    # Closed ring check
    if ring[0] != ring[-1]:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Polygon ring must be closed (first and last coordinates equal)")

    # Vertex limits and Australia bounds
    if len(ring) > 101:  # including duplicate closing point
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Polygon has too many vertices (max 100)")

    for pt in ring:
        if not isinstance(pt, (list, tuple)) or len(pt) != 2:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid coordinate format in polygon")
        lng, lat = pt
        if not (AUSTRALIA_BOUNDS["min_lng"] <= lng <= AUSTRALIA_BOUNDS["max_lng"] and AUSTRALIA_BOUNDS["min_lat"] <= lat <= AUSTRALIA_BOUNDS["max_lat"]):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Polygon coordinates must be within Australia bounds")


@router.post(
    "/calculate-cost",
    response_model=JobCostCalculation,
    summary="Calculate job cost",
    description="Calculate the total cost for a job based on household count"
)
async def calculate_job_cost(
    household_count: int = Query(..., gt=0, le=10000, description="Number of households"),
    db: Session = Depends(get_session)
):
    """
    Calculate job cost based on household count.
    Available to all authenticated users for cost estimation.
    """
    job_service = get_job_service(db)
    return job_service.calculate_job_cost(household_count)


@router.post(
    "/",
    response_model=JobResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new job",
    description="Create a new draft leaflet distribution job"
)
@limiter.limit("20/minute")  # CRITICAL FIX #4: Rate limit job creation to 20 per minute
async def create_job(
    request: Request,  # Required for rate limiting
    job_data: JobCreate,
    current_user: User = Depends(require_client_role()),
    db: Session = Depends(get_session)
):
    """
    Create a new draft job for leaflet distribution.
    Only accessible by users with client role.
    """
    try:
        # Additional GeoJSON validation for polygon areas (security + constraints)
        if job_data.job_area and job_data.job_area.area_type == "polygon" and job_data.job_area.geojson:
            _validate_polygon_geojson(job_data.job_area.geojson)
        job_service = get_job_service(db)
        job = job_service.create_job(job_data, current_user)
        
        # Load job with relationships for response
        db.refresh(job)
        return job
        
    except JobServiceError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except HTTPException:
        # Propagate validation/auth errors (e.g., polygon validation) without wrapping as 500
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create job: {str(e)}"
        )


@router.get(
    "/",
    response_model=List[JobListResponse],
    summary="List client jobs",
    description="Get a list of jobs for the current client"
)
async def list_client_jobs(
    status_filter: Optional[JobStatus] = Query(None, description="Filter jobs by status"),
    limit: int = Query(50, ge=1, le=100, description="Maximum number of jobs to return"),
    offset: int = Query(0, ge=0, description="Number of jobs to skip"),
    current_user: User = Depends(require_client_role()),
    db: Session = Depends(get_session)
):
    """
    Get a list of jobs for the current client.
    Supports filtering by status and pagination.
    """
    try:
        job_service = get_job_service(db)
        jobs = job_service.get_client_jobs(
            current_user,
            status=status_filter,
            limit=limit,
            offset=offset
        )
        
        return jobs
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve jobs: {str(e)}"
        )


@router.get(
    "/public",
    response_model=List[PublicJobListResponse],
    summary="List all public jobs",
    description="Get a list of all published jobs from all users (public listing). Excludes draft jobs."
)
async def list_public_jobs(
    status_filter: Optional[JobStatus] = Query(None, description="Filter jobs by status (PAID, ASSIGNED, or COMPLETED)"),
    limit: int = Query(50, ge=1, le=100, description="Maximum number of jobs to return"),
    offset: int = Query(0, ge=0, description="Number of jobs to skip"),
    current_user: User = Depends(require_client_role()),
    db: Session = Depends(get_session)
):
    """
    Get a list of all published jobs from all users.
    Only shows jobs with status PAID, ASSIGNED, or COMPLETED.
    Draft jobs are excluded (only visible to owner).
    Includes business name of job creator.
    """
    try:
        job_service = get_job_service(db)
        jobs = job_service.get_public_jobs(
            status=status_filter,
            limit=limit,
            offset=offset
        )
        
        # Convert to PublicJobListResponse with client business name
        from app.models import Client
        response_jobs = []
        for job in jobs:
            # Get client business name
            client = db.exec(
                select(Client).where(Client.user_id == job.client_id)
            ).first()
            
            business_name = client.business_name if client else None
            
            response_jobs.append(PublicJobListResponse(
                id=job.id,
                status=job.status,
                title=job.title,
                description=job.description,
                household_count=job.household_count,
                cost_total_pence=job.cost_total_pence,
                scheduled_date=job.scheduled_date,
                paid_at=job.paid_at,
                created_at=job.created_at,
                client_business_name=business_name
            ))
        
        return response_jobs
        
    except Exception as e:
        logger.error(f"Error fetching public jobs: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve public jobs: {str(e)}"
        )


def _map_job_to_ui(job: Any, db: Optional[Session] = None) -> Dict[str, Any]:
    """Map backend job model/schema to UI-friendly shape expected by the frontend."""
    # Best-effort mapping with fallbacks when fields are not present
    # Uses snake_case to camelCase conversion where needed
    try:
        # Handle Row objects and tuples from joins (job, job_area)
        job_area_obj = None
        if hasattr(job, '_fields') or hasattr(job, '__getitem__'):
            # It's a Row object or tuple-like object
            try:
                if len(job) >= 2:
                    job = job[0]
                    job_area_obj = job[1] if job[1] is not None else None
                else:
                    job = job[0] if len(job) > 0 else job
            except (IndexError, TypeError):
                # Try attribute access for Row objects
                job = getattr(job, 'DropJob', None) or getattr(job, 'drop_job', None) or job
                job_area_obj = getattr(job, 'JobArea', None) or getattr(job, 'job_area', None)
        elif isinstance(job, tuple):
            job, job_area_obj = job[0], job[1] if len(job) > 1 else None
        
        # Ensure we have a proper job object
        if not hasattr(job, "id"):
            logger.error(f"Job object missing id attribute: {type(job)}, {job}")
            raise ValueError("Invalid job object - missing id attribute")
        
        created_at = getattr(job, "created_at", None)
        status_value = getattr(job, "status", None)
        
        # Map backend status to frontend status
        if hasattr(status_value, "value"):
            status_str_raw = status_value.value
        else:
            status_str_raw = status_value or "draft"
        
        # Map backend status values to frontend expectations
        status_map = {
            "draft": "draft",
            "paid": "active",  # Frontend uses 'active' for published/paid jobs
            "assigned": "in_progress",
            "completed": "completed",
            "rejected": "cancelled",
        }
        status_str = status_map.get(status_str_raw, status_str_raw)
        
        title = getattr(job, "title", "")
        description = getattr(job, "description", "")
        
        # Fetch job_area if available (try relationship first, then query if needed)
        if job_area_obj is None:
            job_area_obj = getattr(job, "job_area", None)
        
        if job_area_obj is None and db is not None:
            # Try to load job_area from database if relationship not loaded
            from app.models import JobArea
            from sqlmodel import select
            try:
                area_stmt = select(JobArea).where(JobArea.job_id == job.id)
                job_area_obj = db.exec(area_stmt).first()
            except:
                job_area_obj = None

        # Extract address metadata from job_area if stored there
        location_metadata = {}
        pickup_metadata = {}
        if job_area_obj and hasattr(job_area_obj, 'geojson') and isinstance(job_area_obj.geojson, dict):
            # Check if metadata is stored in geojson
            # Handle both direct metadata key and nested structure
            if 'metadata' in job_area_obj.geojson:
                metadata = job_area_obj.geojson.get('metadata', {})
                location_metadata = metadata.get('location', {}) if isinstance(metadata, dict) else {}
                pickup_metadata = metadata.get('pickupLocation', {}) if isinstance(metadata, dict) else {}
            # Also check if geojson itself has location/pickupLocation at top level (for backwards compatibility)
            elif 'location' in job_area_obj.geojson:
                location_metadata = job_area_obj.geojson.get('location', {})
            elif 'pickupLocation' in job_area_obj.geojson:
                pickup_metadata = job_area_obj.geojson.get('pickupLocation', {})

        # Derive simple placeholders if domain fields do not exist
        # Frontend expects nested location/area/requirements/compensation/schedule
        job_id = job.id if hasattr(job, "id") else getattr(job, "id", None)
        if not job_id:
            logger.error(f"Job missing ID: {type(job)}, {job}")
            raise ValueError("Job must have a valid ID")
        
        ui_job = {
            "id": str(job_id),
            "title": title,
            "description": description or "",
            "jobType": "leaflet_distribution",
            "location": {
                "address": location_metadata.get("address", ""),
                "city": location_metadata.get("city", ""),
                "state": location_metadata.get("state", ""),
                "zipCode": location_metadata.get("zipCode", ""),
                "coordinates": {
                    "lat": job_area_obj.center_lat,
                    "lng": job_area_obj.center_lng
                } if (job_area_obj and 
                      hasattr(job_area_obj, 'center_lat') and 
                      hasattr(job_area_obj, 'center_lng') and
                      job_area_obj.center_lat is not None and 
                      job_area_obj.center_lng is not None) else None,
            },
            "pickupLocation": {
                "address": pickup_metadata.get("address", ""),
                "city": pickup_metadata.get("city", ""),
                "state": pickup_metadata.get("state", ""),
                "zipCode": pickup_metadata.get("zipCode", ""),
                "coordinates": {
                    "lat": pickup_metadata.get("coordinates", {}).get("lat") if isinstance(pickup_metadata.get("coordinates"), dict) else None,
                    "lng": pickup_metadata.get("coordinates", {}).get("lng") if isinstance(pickup_metadata.get("coordinates"), dict) else None,
                } if pickup_metadata.get("coordinates") else None,
                "instructions": pickup_metadata.get("instructions", getattr(job, "special_instructions", "") or ""),
            },
            "area": {
                "radius": int(job_area_obj.radius_km * 1000) if (job_area_obj and 
                                                                 hasattr(job_area_obj, 'radius_km') and 
                                                                 job_area_obj.radius_km is not None) else 500,
                "specificLocations": job_area_obj.postcodes if (job_area_obj and 
                                                                 hasattr(job_area_obj, 'postcodes') and 
                                                                 job_area_obj.postcodes) else [],
            },
            "requirements": {
                "materials": getattr(job, "special_instructions", "") or "",
                "estimatedDays": 1,
            },
            "compensation": {
                "paymentType": "per_piece",
                # Convert pence to AUD amount placeholder if available; otherwise 0
                "amount": int(getattr(job, "cost_total_pence", 0) // 100) if getattr(job, "cost_total_pence", None) is not None else 0,
                "currency": "AUD",
            },
            "schedule": {
                "startDate": str(getattr(job, "scheduled_date", "") or ""),
                "endDate": str(getattr(job, "scheduled_date", "") or ""),
                "estimatedHours": 0,
                "flexibility": "flexible",
            },
            "status": status_str,
            "applicants": 0,
            "createdAt": created_at.isoformat() if (created_at and hasattr(created_at, "isoformat")) else (str(created_at) if created_at else ""),
        }
        return ui_job
    except Exception as e:
        # As a last resort, try to extract job ID even if other attributes fail
        try:
            # Handle tuple case
            if isinstance(job, tuple):
                actual_job = job[0] if len(job) > 0 else None
            else:
                actual_job = job
            
            if actual_job and hasattr(actual_job, "id"):
                job_id = actual_job.id
            else:
                job_id = getattr(actual_job if actual_job else job, "id", None)
            
            if not job_id:
                logger.error(f"Job missing ID in fallback: {type(job)}, error: {e}")
                # Re-raise to be caught by caller
                raise ValueError("Job missing ID - cannot map to UI")
            
            # Return minimal required fields with valid ID
            return {
                "id": str(job_id),
                "title": getattr(actual_job if actual_job else job, "title", "Unknown Job"),
                "description": getattr(actual_job if actual_job else job, "description", "") or "",
                "jobType": "leaflet_distribution",
                "location": {"address": "", "city": "", "state": "", "zipCode": "", "coordinates": None},
                "pickupLocation": {"address": "", "city": "", "state": "", "zipCode": "", "coordinates": None, "instructions": ""},
                "area": {"radius": 500, "specificLocations": []},
                "requirements": {"materials": "", "estimatedDays": 1},
                "compensation": {"paymentType": "per_piece", "amount": 0, "currency": "AUD"},
                "schedule": {"startDate": "", "endDate": "", "estimatedHours": 0, "flexibility": "flexible"},
                "status": "draft",
                "applicants": 0,
                "createdAt": "",
            }
        except Exception as fallback_error:
            logger.error(f"Complete failure in job mapping fallback: {fallback_error}", exc_info=True)
            raise ValueError(f"Cannot map job to UI format: {fallback_error}")


@router.get(
    "/ui",
    summary="List jobs for unified feed (UI shape)",
    description="Get jobs in a UI-friendly shape for the frontend, filtered by user role"
)
async def list_client_jobs_ui(
    status_filter: Optional[str] = Query(None, description="Filter jobs by status (draft, paid, assigned, completed)"),
    limit: int = Query(50, ge=1, le=100, description="Maximum number of jobs to return"),
    offset: int = Query(0, ge=0, description="Number of jobs to skip"),
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_session)
):
    try:
        # Convert string status to JobStatus enum if provided
        job_status_filter = None
        if status_filter:
            status_lower = status_filter.lower().strip()
            # Map frontend status strings to backend enum values
            status_map = {
                "draft": JobStatus.DRAFT,
                "paid": JobStatus.PAID,
                "assigned": JobStatus.ASSIGNED,
                "completed": JobStatus.COMPLETED,
                "rejected": JobStatus.REJECTED,
                "pending_approval": JobStatus.PENDING_APPROVAL,
            }
            job_status_filter = status_map.get(status_lower)
            if job_status_filter is None:
                logger.warning(f"Invalid status filter '{status_filter}', ignoring")
        
        job_service = get_job_service(db)
        jobs = job_service.get_jobs_for_feed(
            current_user,
            status=job_status_filter,
            limit=limit,
            offset=offset
        )
        # Map jobs and filter out any with invalid/missing IDs
        ui_jobs = []
        for job in jobs:
            try:
                # Skip if job is None or doesn't have required attributes
                if job is None:
                    continue
                if not hasattr(job, 'id'):
                    logger.warning(f"Skipping job without id attribute: {type(job)}")
                    continue
                
                ui_job = _map_job_to_ui(job, db)
                # Ensure job has a valid ID before adding
                if ui_job and ui_job.get("id") and str(ui_job["id"]).strip():
                    ui_jobs.append(ui_job)
                else:
                    logger.warning(f"Skipping job with invalid ID: {job.id if hasattr(job, 'id') else 'unknown'}")
            except (ValueError, AttributeError, TypeError) as e:
                logger.error(f"Error mapping job to UI (skipping): {e}", exc_info=True)
                # Skip jobs that can't be mapped properly - don't fail entire request
                continue
            except Exception as e:
                logger.error(f"Unexpected error mapping job to UI (skipping): {e}", exc_info=True)
                # Skip jobs that can't be mapped properly - don't fail entire request
                continue
        
        # Return empty list if no valid jobs found (instead of error)
        return ui_jobs
    except HTTPException:
        # Re-raise HTTP exceptions as-is
        raise
    except Exception as e:
        logger.error(f"Failed to retrieve UI jobs: {e}", exc_info=True)
        # Return more detailed error message for debugging
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve UI jobs: {str(e)}"
        )

class UIJobLocation(BaseModel):
    address: str = ""
    city: str = ""
    state: str = ""
    zipCode: str = ""
    coordinates: Optional[Dict[str, float]] = None


class UIJobPickupLocation(UIJobLocation):
    instructions: Optional[str] = ""


class UIJobArea(BaseModel):
    radius: int = 500
    specificLocations: List[str] = []


class UIJobRequirements(BaseModel):
    materials: str = ""
    estimatedDays: int = 1


class UIJobCompensation(BaseModel):
    paymentType: str = "per_piece"
    amount: int = 0
    currency: str = "AUD"


class UIJobSchedule(BaseModel):
    startDate: Optional[str] = None
    endDate: Optional[str] = None
    estimatedHours: int = 0
    flexibility: str = "flexible"


class UIJobCreate(BaseModel):
    title: str
    description: Optional[str] = ""
    jobType: Optional[str] = "leaflet_distribution"
    leafletImageUrl: Optional[str] = None  # Base64 or URL for leaflet image
    location: UIJobLocation
    pickupLocation: UIJobPickupLocation
    area: UIJobArea
    requirements: UIJobRequirements
    compensation: UIJobCompensation
    schedule: UIJobSchedule
    qualifications: Optional[List[str]] = []
    job_area: Optional[Dict[str, Any]] = None  # For polygon GeoJSON from frontend


class UIJobUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    location: Optional[UIJobLocation] = None
    pickupLocation: Optional[UIJobPickupLocation] = None
    area: Optional[UIJobArea] = None
    schedule: Optional[UIJobSchedule] = None
    requirements: Optional[UIJobRequirements] = None
    compensation: Optional[UIJobCompensation] = None
    job_area: Optional[Dict[str, Any]] = None  # For polygon GeoJSON updates


def _ui_to_job_create(data: UIJobCreate, polygon_geojson: Optional[Dict[str, Any]] = None, cost_per_household_pence: int = 50) -> JobCreate:
    # Basic mapping with safe defaults to satisfy required fields
    # Infer household_count from compensation.amount based on standard pricing
    # Price is configurable (default 50 pence / $0.50 AUD per household)
    amount = data.compensation.amount or 0
    
    if amount > 0:
        # Calculate households based on provided cost
        # amount is in dollars/pounds, cost is in pence
        cost_in_dollars = cost_per_household_pence / 100
        if cost_in_dollars > 0:
            households = int(amount / cost_in_dollars)
        else:
            households = 100 # Fallback to avoid division by zero
    else:
        households = 100 # Default fallback

    # Scheduled date: preferred startDate, else tomorrow
    try:
        if data.schedule.startDate:
            # Handle both string and date objects
            if isinstance(data.schedule.startDate, str):
                scheduled = date.fromisoformat(data.schedule.startDate)
            elif isinstance(data.schedule.startDate, date):
                scheduled = data.schedule.startDate
            else:
                scheduled = date.today() + timedelta(days=1)
        else:
            scheduled = date.today() + timedelta(days=1)
        
        # Ensure scheduled date is not in the past
        if scheduled <= date.today():
            scheduled = date.today() + timedelta(days=1)
    except Exception:
        scheduled = date.today() + timedelta(days=1)

    # Convert UI area data to JobAreaCreate
    
    # Extract coordinates from location if available - use safe access
    location_coords = None
    try:
        location_coords = getattr(data.location, 'coordinates', None)
    except AttributeError:
        location_coords = None
    
    # Ensure location_coords is a dict if not None
    if location_coords is None:
        location_coords = {}
    
    center_lat = location_coords.get("lat") if isinstance(location_coords, dict) and location_coords else None
    center_lng = location_coords.get("lng") if isinstance(location_coords, dict) and location_coords else None
    
    # Convert radius from meters to kilometers
    radius_km = max(0.1, (data.area.radius or 500) / 1000.0)
    
    # Use specific locations (postcodes) from area, or default
    postcodes = data.area.specificLocations if data.area.specificLocations else []
    if not postcodes:
        postcodes = ["0000"]  # Default placeholder
    
    # Store location and pickup location metadata
    location_metadata = {
        "address": getattr(data.location, 'address', '') or '',
        "city": getattr(data.location, 'city', '') or '',
        "state": getattr(data.location, 'state', '') or '',
        "zipCode": getattr(data.location, 'zipCode', '') or '',
    }
    pickup_metadata = {
        "address": getattr(data.pickupLocation, 'address', '') or '',
        "city": getattr(data.pickupLocation, 'city', '') or '',
        "state": getattr(data.pickupLocation, 'state', '') or '',
        "zipCode": getattr(data.pickupLocation, 'zipCode', '') or '',
        "instructions": getattr(data.pickupLocation, 'instructions', '') or '',
        "coordinates": getattr(data.pickupLocation, 'coordinates', None),
    }
    
    # Determine area type: polygon if GeoJSON provided, otherwise postcodes
    if polygon_geojson:
        # Validate polygon GeoJSON
        _validate_polygon_geojson(polygon_geojson)
        
        # Extract center from polygon if coordinates not provided
        if not center_lat or not center_lng:
            try:
                coords = polygon_geojson.get("coordinates", [])
                if coords and len(coords) > 0 and len(coords[0]) > 0:
                    # Calculate centroid from polygon coordinates
                    ring = coords[0]
                    lat_sum = sum(coord[1] for coord in ring if len(coord) >= 2)
                    lng_sum = sum(coord[0] for coord in ring if len(coord) >= 2)
                    count = len(ring)
                    if count > 0:
                        center_lat = lat_sum / count
                        center_lng = lng_sum / count
            except (KeyError, IndexError, TypeError, ZeroDivisionError):
                pass  # Keep None if calculation fails
        
        # Store metadata in geojson
        if not isinstance(polygon_geojson, dict):
            polygon_geojson = {}
        if 'metadata' not in polygon_geojson:
            polygon_geojson['metadata'] = {}
        polygon_geojson['metadata']['location'] = location_metadata
        polygon_geojson['metadata']['pickupLocation'] = pickup_metadata
        
        job_area = JobAreaCreate(
            area_type="polygon",
            geojson=polygon_geojson,
            postcodes=None,
            center_lat=center_lat,
            center_lng=center_lng,
            radius_km=radius_km
        )
    else:
        # For postcodes, create a minimal geojson to store metadata
        metadata_geojson = {
            "type": "Feature",
            "properties": {},
            "metadata": {
                "location": location_metadata,
                "pickupLocation": pickup_metadata
            }
        }
        
        job_area = JobAreaCreate(
            area_type="postcodes",
            postcodes=postcodes,
            center_lat=center_lat,
            center_lng=center_lng,
            radius_km=radius_km,
            geojson=metadata_geojson
        )
    
    # Use provided leaflet image URL or fallback to placeholder
    leaflet_url = data.leafletImageUrl or "https://example.com/leaflets/placeholder.pdf"
    
    return JobCreate(
        title=data.title,
        description=data.description or "",
        leaflet_file_url=leaflet_url,
        household_count=households,
        scheduled_date=scheduled,
        special_instructions=data.pickupLocation.instructions or data.requirements.materials or None,
        job_area=job_area,
    )


def _ui_to_job_update(data: UIJobUpdate, cost_per_household_pence: int = 50) -> JobUpdate:
    scheduled = None
    if data.schedule and data.schedule.startDate:
        try:
            sd = date.fromisoformat(data.schedule.startDate)
            if sd > date.today():
                scheduled = sd
        except Exception:
            scheduled = None

    # Extract household count from compensation if available
    household_count = None
    if data.compensation and data.compensation.amount:
        amount = data.compensation.amount
        # Estimate household count based on standard pricing
        if amount > 0:
            cost_in_dollars = cost_per_household_pence / 100
            if cost_in_dollars > 0:
                household_count = int(amount / cost_in_dollars)

    return JobUpdate(
        title=data.title,
        description=data.description,
        scheduled_date=scheduled,
        special_instructions=data.requirements.materials if data.requirements else None,
        leaflet_file_url=None,
        household_count=household_count,
    )


@router.post(
    "/ui",
    summary="Create job (UI shape)",
    description="Create a draft job from UI form data",
    status_code=status.HTTP_201_CREATED,
    response_model=Dict[str, Any]
)
async def create_job_ui(
    job_data: UIJobCreate,
    current_user: User = Depends(require_client_role()),
    db: Session = Depends(get_session)
):
    """
    Create a new job from UI form data.
    Returns job in UI-friendly format.
    """
    try:
        job_service = get_job_service(db)
        
        # Extract polygon GeoJSON if provided in job_area field
        polygon_geojson = None
        if job_data.job_area and isinstance(job_data.job_area, dict):
            if job_data.job_area.get("area_type") == "polygon" and job_data.job_area.get("geojson"):
                polygon_geojson = job_data.job_area.get("geojson")
        
        # Get current cost settings
        cost_settings = db.exec(select(CostSettings).limit(1)).first()
        cost_per_household = cost_settings.price_per_household_pence if cost_settings else 50

        # Validate and process the job data
        mapped = _ui_to_job_create(job_data, polygon_geojson=polygon_geojson, cost_per_household_pence=cost_per_household)
        job = job_service.create_job(mapped, current_user)
        # Job service already commits, refresh to get the job with relationships
        db.refresh(job)
        return _map_job_to_ui(job, db)
    except JobServiceError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Job creation error: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to create job: {str(e)}")


@router.get(
    "/{job_id}/ui",
    summary="Get job details (UI shape)",
    description="Get detailed job information in UI-friendly shape. Allows public viewing of published jobs."
)
async def get_job_ui(
    job_id: UUID,
    allow_public: bool = Query(False, description="Allow viewing other users' published jobs (read-only)"),
    current_user: User = Depends(require_client_or_admin()),
    db: Session = Depends(get_session)
):
    """
    Get detailed job information in UI-friendly shape.
    If allow_public=True, allows viewing other users' published jobs (read-only).
    Draft jobs are always restricted to owner only.
    """
    try:
        job_service = get_job_service(db)
        job = job_service.get_job_by_id(job_id, current_user, allow_public=allow_public)
        if not job:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Job not found or you don't have permission to view it"
            )
        db.refresh(job)
        return _map_job_to_ui(job, db)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve job: {str(e)}"
        )


@router.patch(
    "/{job_id}/ui",
    summary="Update job (UI shape)",
    description="Update a draft job from UI form data",
    response_model=Dict[str, Any]
)
async def update_job_ui(
    job_id: UUID,
    job_data: UIJobUpdate,
    current_user: User = Depends(require_client_role()),
    db: Session = Depends(get_session)
):
    """
    Update a draft job from UI form data.
    Returns updated job in UI-friendly format.
    """
    try:
        job_service = get_job_service(db)
        
        # Get current job to check status before updating
        current_job = job_service.get_job_by_id(job_id, current_user)
        if not current_job:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
        
        # Extract polygon GeoJSON if provided in job_area field (for updates)
        polygon_geojson = None
        if job_data.job_area and isinstance(job_data.job_area, dict):
            if job_data.job_area.get("area_type") == "polygon" and job_data.job_area.get("geojson"):
                polygon_geojson = job_data.job_area.get("geojson")
        
        # Get current cost settings
        cost_settings = db.exec(select(CostSettings).limit(1)).first()
        cost_per_household = cost_settings.price_per_household_pence if cost_settings else 50

        # Convert UI data to backend format
        mapped = _ui_to_job_update(job_data, cost_per_household_pence=cost_per_household)
        
        # If job is not in DRAFT or REJECTED status, remove household_count to prevent cost changes
        # Only draft/rejected jobs can have cost-related fields updated
        if current_job.status not in [JobStatus.DRAFT, JobStatus.REJECTED]:
            mapped.household_count = None
        
        # Update job
        job = job_service.update_job(job_id, mapped, current_user)
        if not job:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
        
        # Update job area if coordinates or polygon provided
        if polygon_geojson or job_data.location or job_data.area:
            from app.models import JobArea
            job_area = db.exec(select(JobArea).where(JobArea.job_id == job_id)).first()
            
            if job_area:
                # Extract coordinates from location if available
                location_coords = None
                try:
                    location_coords = getattr(job_data.location, 'coordinates', None) if job_data.location else None
                except AttributeError:
                    location_coords = None
                
                # Update center coordinates if provided
                if location_coords and isinstance(location_coords, dict):
                    job_area.center_lat = location_coords.get("lat")
                    job_area.center_lng = location_coords.get("lng")
                
                # Store location and pickup location metadata
                location_metadata = {}
                pickup_metadata = {}
                if job_data.location:
                    location_metadata = {
                        "address": getattr(job_data.location, 'address', '') or '',
                        "city": getattr(job_data.location, 'city', '') or '',
                        "state": getattr(job_data.location, 'state', '') or '',
                        "zipCode": getattr(job_data.location, 'zipCode', '') or '',
                    }
                if job_data.pickupLocation:
                    pickup_metadata = {
                        "address": getattr(job_data.pickupLocation, 'address', '') or '',
                        "city": getattr(job_data.pickupLocation, 'city', '') or '',
                        "state": getattr(job_data.pickupLocation, 'state', '') or '',
                        "zipCode": getattr(job_data.pickupLocation, 'zipCode', '') or '',
                        "instructions": getattr(job_data.pickupLocation, 'instructions', '') or '',
                        "coordinates": getattr(job_data.pickupLocation, 'coordinates', None),
                    }
                
                # Update polygon if provided
                if polygon_geojson:
                    _validate_polygon_geojson(polygon_geojson)
                    job_area.area_type = "polygon"
                    # Store metadata in geojson
                    if not isinstance(polygon_geojson, dict):
                        polygon_geojson = {}
                    if 'metadata' not in polygon_geojson:
                        polygon_geojson['metadata'] = {}
                    if location_metadata:
                        polygon_geojson['metadata']['location'] = location_metadata
                    if pickup_metadata:
                        polygon_geojson['metadata']['pickupLocation'] = pickup_metadata
                    job_area.geojson = polygon_geojson
                elif job_area.geojson and isinstance(job_area.geojson, dict):
                    # Update metadata in existing geojson
                    if 'metadata' not in job_area.geojson:
                        job_area.geojson['metadata'] = {}
                    if location_metadata:
                        job_area.geojson['metadata']['location'] = location_metadata
                    if pickup_metadata:
                        job_area.geojson['metadata']['pickupLocation'] = pickup_metadata
                elif location_metadata or pickup_metadata:
                    # Create metadata geojson if doesn't exist
                    job_area.geojson = {
                        "type": "Feature",
                        "properties": {},
                        "metadata": {
                            "location": location_metadata,
                            "pickupLocation": pickup_metadata
                        }
                    }
                    # Calculate center from polygon if not set (only if we have polygon data)
                    if not job_area.center_lat or not job_area.center_lng:
                        # Try to get coordinates from location if available
                        if location_coords and isinstance(location_coords, dict):
                            job_area.center_lat = location_coords.get("lat")
                            job_area.center_lng = location_coords.get("lng")
                        elif polygon_geojson and isinstance(polygon_geojson, dict):
                            try:
                                coords = polygon_geojson.get("coordinates", [])
                                if coords and len(coords) > 0 and len(coords[0]) > 0:
                                    ring = coords[0]
                                    lat_sum = sum(coord[1] for coord in ring if len(coord) >= 2)
                                    lng_sum = sum(coord[0] for coord in ring if len(coord) >= 2)
                                    count = len(ring)
                                    if count > 0:
                                        job_area.center_lat = lat_sum / count
                                        job_area.center_lng = lng_sum / count
                            except (KeyError, IndexError, TypeError, ZeroDivisionError):
                                pass
                
                # Update radius if provided
                if job_data.area and job_data.area.radius:
                    job_area.radius_km = max(0.1, job_data.area.radius / 1000.0)
                
                db.add(job_area)
        
        db.commit()
        db.refresh(job)
        return _map_job_to_ui(job, db)
    except JobServiceError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update UI job: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to update UI job: {str(e)}")


# ====================================================================
# CRITICAL: Route ordering matters in FastAPI
# More specific routes with additional path segments MUST come before generic /{job_id} routes
# FastAPI matches routes in order, so /{job_id}/publish must come before /{job_id}
# ====================================================================

@router.post(
    "/{job_id}/publish",
    response_model=Dict[str, Any],
    summary="Publish job (DEPRECATED)",
    description="DEPRECATED: Direct publishing is disabled. Jobs now require admin approval."
)
async def publish_job(
    job_id: UUID,
    current_user: User = Depends(require_client_role()),
    db: Session = Depends(get_session)
):
    """
    DEPRECATED: Direct publishing is disabled.
    
    Jobs now require admin approval workflow:
    1. Client creates job → Status: DRAFT
    2. Admin approves job via /admin/jobs/{job_id}/approve → Status: PAID
    3. Only PAID jobs are visible to droppers
    
    Clients can no longer publish jobs directly.
    """
    # Disable direct publishing - require admin approval instead
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Direct publishing is disabled. Jobs must be approved by an admin. Please contact an administrator to approve your job."
    )


@router.post(
    "/{job_id}/pay",
    response_model=JobPaymentResponse,
    summary="Pay for job",
    description="Process payment for a draft job using Stripe"
)
@limiter.limit("10/minute")  # CRITICAL FIX #4: Rate limit payment attempts to 10 per minute
async def pay_for_job(
    request: Request,  # Required for rate limiting
    job_id: UUID,
    payment_data: JobPaymentRequest,
    current_user: User = Depends(require_client_role()),
    db: Session = Depends(get_session)
):
    """
    Process payment for a draft job using Stripe.
    Creates a PaymentIntent and handles payment processing.
    """
    try:
        job_service = get_job_service(db)
        job = job_service.get_job_by_id(job_id, current_user)
        
        if not job:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Job not found"
            )
        
        if job.status != JobStatus.DRAFT:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Only draft jobs can be paid for"
            )
        
        # Get or create Stripe customer
        customer_id = await stripe_service.get_or_create_customer(current_user)
        
        # Update user with customer ID if not already set
        if not current_user.stripe_customer_id:
            current_user.stripe_customer_id = customer_id
            db.add(current_user)
            db.commit()
        
        # Create PaymentIntent with transaction rollback support
        try:
            payment_intent_data = await stripe_service.create_payment_intent(
                job=job,
                customer_id=customer_id,
                payment_method_id=payment_data.payment_method_id
            )
            
            # Update job with payment intent ID (but don't change status yet - wait for webhook confirmation)
            job.payment_intent_id = payment_intent_data["id"]
            db.add(job)
            db.commit()
            
            # Determine if payment requires additional action
            requires_action = payment_intent_data["status"] in ["requires_action", "requires_source_action"]
            
            return JobPaymentResponse(
                payment_intent_id=payment_intent_data["id"],
                client_secret=payment_intent_data["client_secret"],
                status=payment_intent_data["status"],
                amount=payment_intent_data["amount"],
                currency=payment_intent_data["currency"],
                requires_action=requires_action
            )
        except Exception as payment_error:
            # Rollback: If payment intent creation fails, ensure job is not modified
            db.rollback()
            logger.error(f"Payment intent creation failed for job {job_id}: {payment_error}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to create payment intent: {str(payment_error)}"
            )
        
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Unexpected error processing payment for job {job_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to process payment: {str(e)}"
        )


@router.get(
    "/{job_id}/payment-status",
    summary="Get payment status",
    description="Check the current payment status of a job"
)
async def get_job_payment_status(
    job_id: UUID,
    current_user: User = Depends(require_client_role()),
    db: Session = Depends(get_session)
):
    """
    Check the current payment status of a job.
    Returns payment information if payment has been initiated.
    """
    try:
        job_service = get_job_service(db)
        job = job_service.get_job_by_id(job_id, current_user)
        
        if not job:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Job not found"
            )
        
        if not job.payment_intent_id:
            return {
                "status": "no_payment_initiated",
                "job_status": job.status.value,
                "paid_at": job.paid_at
            }
        
        # Get payment status from Stripe
        payment_intent_data = await stripe_service.retrieve_payment_intent(job.payment_intent_id)
        
        return {
            "payment_intent_id": job.payment_intent_id,
            "status": payment_intent_data["status"],
            "amount": payment_intent_data["amount"],
            "currency": payment_intent_data["currency"],
            "job_status": job.status.value,
            "paid_at": job.paid_at
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get payment status: {str(e)}"
        )


# Generic routes must come AFTER all specific routes
# This ensures /{job_id}/publish, /{job_id}/pay, etc. are matched first

@router.get(
    "/{job_id}",
    response_model=JobResponse,
    summary="Get job details",
    description="Get detailed information about a specific job. Allows public viewing of published jobs."
)
async def get_job(
    job_id: UUID,
    allow_public: bool = Query(False, description="Allow viewing other users' published jobs (read-only)"),
    current_user: User = Depends(require_client_or_admin()),
    db: Session = Depends(get_session)
):
    """
    Get detailed information about a specific job.
    If allow_public=True, allows viewing other users' published jobs (read-only).
    Draft jobs are always restricted to owner only.
    Clients can always access their own jobs, admins can access all jobs.
    """
    try:
        job_service = get_job_service(db)
        job = job_service.get_job_by_id(job_id, current_user, allow_public=allow_public)
        
        if not job:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Job not found or you don't have permission to view it"
            )
        
        return job
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve job: {str(e)}"
        )


@router.patch(
    "/{job_id}",
    response_model=JobResponse,
    summary="Update job",
    description="Update a job with new information. All jobs can be updated, but cost-related fields can only be changed for draft/rejected jobs."
)
async def update_job(
    job_id: UUID,
    job_data: JobUpdate,
    current_user: User = Depends(require_client_role()),
    db: Session = Depends(get_session)
):
    """
    Update a job with new information.
    
    Jobs in any status can be updated:
    - Draft/Rejected jobs: All fields can be updated
    - Paid/Active/Completed jobs: Only non-cost fields can be updated (title, description, leaflet_file_url, scheduled_date, special_instructions)
    """
    try:
        job_service = get_job_service(db)
        job = job_service.update_job(job_id, job_data, current_user)
        
        if not job:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Job not found"
            )
        
        return job
        
    except JobServiceError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update job: {str(e)}"
        )


@router.delete(
    "/{job_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete job",
    description="Delete a job. All jobs can be deleted."
)
async def delete_job(
    job_id: UUID,
    current_user: User = Depends(require_client_role()),
    db: Session = Depends(get_session)
):
    """
    Delete a job.
    
    Jobs in any status can be deleted. However, deleting paid/active/completed jobs
    may require refunds or other business logic considerations.
    """
    try:
        job_service = get_job_service(db)
        job = job_service.get_job_by_id(job_id, current_user)
        
        if not job:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Job not found"
            )
        
        # Allow deletion of any job status
        # Log a warning if deleting a paid/active job (might need refund logic later)
        if job.status in [JobStatus.PENDING_APPROVAL, JobStatus.PAID, JobStatus.ASSIGNED]:
            logger.warning(
                f"Deleting job {job_id} with status {job.status.value}. "
                f"Payment intent: {job.payment_intent_id}"
            )
        
        # Delete job area first (if exists)
        from app.models import JobArea
        job_area = db.exec(select(JobArea).where(JobArea.job_id == job_id)).first()
        if job_area:
            db.delete(job_area)
        
        # Delete job
        db.delete(job)
        db.commit()
        
        return None
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete job: {str(e)}"
        )


# ====================================================================
# Map and Location Endpoints
# ====================================================================

@router.get(
    "/map/all",
    response_model=List[Dict[str, Any]],
    summary="Get all jobs for map display",
    description="Get all jobs with location data for map visualization (role-based filtering)"
)
async def get_jobs_for_map(
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_session)
):
    """
    Get all jobs with location data for map display.
    Returns jobs filtered by user role:
    - Droppers: only assigned/active jobs
    - Clients: only their own jobs
    - Admins: all jobs
    """
    try:
        from app.models import JobArea, JobAssignment, UserRole
        
        # Filter jobs based on user role and join with JobArea to avoid N+1
        if current_user.role == UserRole.DROPPER:
            # Droppers see only assigned/active jobs assigned to them
            statement = (
                select(DropJob, JobArea)
                .join(JobAssignment, DropJob.id == JobAssignment.job_id)
                .outerjoin(JobArea, DropJob.id == JobArea.job_id)
                .where(
                    (JobAssignment.dropper_id == current_user.id) &
                    (DropJob.status.in_([JobStatus.ASSIGNED, JobStatus.PAID, JobStatus.PENDING_APPROVAL]))
                )
            )
        elif current_user.role == UserRole.CLIENT:
            # Clients see only their own jobs
            statement = (
                select(DropJob, JobArea)
                .outerjoin(JobArea, DropJob.id == JobArea.job_id)
                .where(DropJob.client_id == current_user.id)
            )
        else:
            # Admins see all jobs
            statement = (
                select(DropJob, JobArea)
                .outerjoin(JobArea, DropJob.id == JobArea.job_id)
            )
        
        results = db.exec(statement).all()
        
        map_jobs = []
        for job, job_area in results:
            if job_area and job_area.center_lat and job_area.center_lng:
                map_jobs.append({
                    "id": str(job.id),
                    "title": job.title,
                    "description": job.description,
                    "status": job.status.value if hasattr(job.status, "value") else str(job.status),
                    "location": {
                        "lat": job_area.center_lat,
                        "lng": job_area.center_lng,
                    },
                    "radius": int(job_area.radius_km * 1000) if job_area.radius_km else 500,
                    "geojson": job_area.geojson,
                    "created_at": job.created_at.isoformat() if job.created_at else None,
                })
        
        return map_jobs
        
    except Exception as e:
        logger.error(f"Error getting jobs for map: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve jobs for map: {str(e)}"
        )


@router.get(
    "/{job_id}/location",
    response_model=Dict[str, Any],
    summary="Get job location details",
    description="Get detailed location information for a specific job"
)
async def get_job_location(
    job_id: UUID,
    current_user: User = Depends(require_client_or_admin()),
    db: Session = Depends(get_session)
):
    """
    Get detailed location information for a specific job.
    Includes coordinates, area definition, and GeoJSON if available.
    """
    try:
        job_service = get_job_service(db)
        job = job_service.get_job_by_id(job_id, current_user)
        
        if not job:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Job not found"
            )
        
        from app.models import JobArea
        job_area = db.exec(select(JobArea).where(JobArea.job_id == job_id)).first()
        
        if not job_area:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Job location not found"
            )
        
        return {
            "job_id": str(job_id),
            "location": {
                "lat": job_area.center_lat,
                "lng": job_area.center_lng,
            },
            "radius_km": job_area.radius_km,
            "radius_m": int(job_area.radius_km * 1000) if job_area.radius_km else None,
            "area_type": job_area.area_type,
            "geojson": job_area.geojson,
            "postcodes": job_area.postcodes,
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting job location: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve job location: {str(e)}"
        )