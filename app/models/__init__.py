"""
Database models using SQLModel for DropTrack platform.
Defines all database entities with relationships and constraints.
"""

from typing import Optional, List
from datetime import datetime, date
from enum import Enum
from uuid import UUID, uuid4
from sqlmodel import SQLModel, Field, Relationship, Column, String, Integer, Float, Boolean, DateTime, Date, Text, JSON
from sqlalchemy import Index, UniqueConstraint, ForeignKey
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID, ARRAY
from pydantic import field_validator


# Enums for model validation
class UserRole(str, Enum):
    CLIENT = "CLIENT"
    DROPPER = "DROPPER"
    ADMIN = "ADMIN"
    SUPERADMIN = "SUPERADMIN"


class JobStatus(str, Enum):
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"  # Paid but awaiting superadmin approval
    APPROVED = "approved"  # Approved by superadmin, ready for broadcasting
    PAID = "paid"  # Approved and available for droppers
    BROADCASTED = "broadcasted"  # Job has been broadcasted to droppers
    ASSIGNED = "assigned"
    COMPLETED = "completed"
    REJECTED = "rejected"


class VerificationStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class PaymentStatus(str, Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
    REFUNDED = "refunded"


# Core User Models
class User(SQLModel, table=True):
    """
    Core user model integrated with Amazon Cognito.
    Stores user information extracted from JWT tokens.
    """
    __tablename__ = "users"
    
    id: UUID = Field(
        default_factory=uuid4,
        sa_column=Column(PostgresUUID(as_uuid=True), primary_key=True)
    )
    cognito_sub: str = Field(
        unique=True,
        index=True,
        max_length=255,
        description="Cognito user identifier from JWT sub claim"
    )
    email: str = Field(
        index=True,
        max_length=255,
        description="User email address from Cognito"
    )
    name: str = Field(
        max_length=255,
        description="User full name from Cognito"
    )
    role: UserRole = Field(
        description="User role from custom:user_role claim"
    )
    stripe_customer_id: Optional[str] = Field(
        default=None,
        max_length=255,
        description="Stripe customer ID for payment processing"
    )
    is_active: bool = Field(
        default=True,
        description="Whether the user account is active"
    )
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(DateTime(timezone=True))
    )
    updated_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True))
    )
    
    # Relationships
    client_profile: Optional["Client"] = Relationship(back_populates="user")
    dropper_profile: Optional["Dropper"] = Relationship(back_populates="user")
    jobs_created: List["DropJob"] = Relationship(back_populates="client")
    job_assignments: List["JobAssignment"] = Relationship(
        back_populates="dropper",
        sa_relationship_kwargs={"foreign_keys": "JobAssignment.dropper_id"}
    )
    transactions: List["Transaction"] = Relationship(back_populates="user")
    invoices: List["Invoice"] = Relationship(back_populates="user")
    
    class Config:
        json_schema_extra = {
            "example": {
                "cognito_sub": "12345678-1234-1234-1234-123456789012",
                "email": "user@example.com",
                "name": "John Doe",
                "role": "client",
                "is_active": True
            }
        }


class Client(SQLModel, table=True):
    """
    Client profile for users who create leaflet distribution jobs.
    Extends User with client-specific information.
    """
    __tablename__ = "clients"
    
    id: UUID = Field(
        default_factory=uuid4,
        sa_column=Column(PostgresUUID(as_uuid=True), primary_key=True)
    )
    user_id: UUID = Field(
        foreign_key="users.id",
        unique=True,
        description="Reference to the User record"
    )
    business_name: str = Field(
        max_length=255,
        description="Name of the client's business"
    )
    business_type: str = Field(
        max_length=100,
        description="Type of business (e.g., restaurant, retail, service)"
    )
    business_address: Optional[str] = Field(
        default=None,
        max_length=500,
        description="Business address for billing purposes"
    )
    phone_number: Optional[str] = Field(
        default=None,
        max_length=20,
        description="Contact phone number"
    )
    role: str = Field(
        default='CLIENT',
        max_length=10,
        description="Client role: 'ADMIN' or 'CLIENT'. Only configured superadmin emails can be 'admin'"
    )
    # New profile fields
    website: Optional[str] = Field(
        default=None,
        max_length=500,
        description="Client's website URL"
    )
    description: Optional[str] = Field(
        default=None,
        sa_column=Column(Text),
        description="Business description"
    )
    street: Optional[str] = Field(
        default=None,
        max_length=255,
        description="Street address"
    )
    city: Optional[str] = Field(
        default=None,
        max_length=100,
        description="City"
    )
    state: Optional[str] = Field(
        default=None,
        max_length=100,
        description="State or county"
    )
    zip_code: Optional[str] = Field(
        default=None,
        max_length=20,
        description="Postal/ZIP code"
    )
    email_notifications: bool = Field(
        default=True,
        description="Whether to receive email notifications"
    )
    sms_notifications: bool = Field(
        default=False,
        description="Whether to receive SMS notifications"
    )
    timezone: str = Field(
        default='Europe/London',
        max_length=50,
        description="User's timezone"
    )
    language: str = Field(
        default='en',
        max_length=10,
        description="User's preferred language"
    )
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(DateTime(timezone=True))
    )
    
    # Relationships
    user: User = Relationship(back_populates="client_profile")
    
    class Config:
        json_schema_extra = {
            "example": {
                "business_name": "Joe's Pizza",
                "business_type": "restaurant",
                "business_address": "123 Main St, City, State 12345",
                "phone_number": "+1234567890",
                "website": "https://joespizza.com",
                "description": "Best pizza in town"
            }
        }


class Dropper(SQLModel, table=True):
    """
    Dropper profile for users who complete leaflet distribution jobs.
    Extends User with dropper-specific information and service area.
    """
    __tablename__ = "droppers"
    
    id: UUID = Field(
        default_factory=uuid4,
        sa_column=Column(PostgresUUID(as_uuid=True), primary_key=True)
    )
    user_id: UUID = Field(
        foreign_key="users.id",
        unique=True,
        description="Reference to the User record"
    )
    id_verified: bool = Field(
        default=False,
        description="Whether the dropper's identity has been verified"
    )
    service_radius_km: int = Field(
        default=5,
        ge=1,
        le=50,
        description="Service radius in kilometers for job matching"
    )
    base_location_lat: Optional[float] = Field(
        default=None,
        ge=-90,
        le=90,
        description="Base latitude for service area center"
    )
    base_location_lng: Optional[float] = Field(
        default=None,
        ge=-180,
        le=180,
        description="Base longitude for service area center"
    )
    rating: float = Field(
        default=0.0,
        ge=0.0,
        le=5.0,
        description="Average rating from completed jobs"
    )
    total_jobs_completed: int = Field(
        default=0,
        ge=0,
        description="Total number of jobs completed"
    )
    stripe_connect_account_id: Optional[str] = Field(
        default=None,
        max_length=255,
        description="Stripe Connect account ID for payouts"
    )
    phone_number: Optional[str] = Field(
        default=None,
        max_length=20,
        description="Contact phone number"
    )
    emergency_contact_name: Optional[str] = Field(
        default=None,
        max_length=255,
        description="Emergency contact name"
    )
    emergency_contact_phone: Optional[str] = Field(
        default=None,
        max_length=20,
        description="Emergency contact phone number"
    )
    is_available: bool = Field(
        default=True,
        description="Whether the dropper is available for new jobs"
    )
    # New profile fields
    email_notifications: bool = Field(
        default=True,
        description="Whether to receive email notifications"
    )
    sms_notifications: bool = Field(
        default=False,
        description="Whether to receive SMS notifications"
    )
    timezone: str = Field(
        default='Europe/London',
        max_length=50,
        description="User's timezone"
    )
    language: str = Field(
        default='en',
        max_length=10,
        description="User's preferred language"
    )
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(DateTime(timezone=True))
    )
    
    # Relationships
    user: User = Relationship(back_populates="dropper_profile")
    
    class Config:
        json_schema_extra = {
            "example": {
                "id_verified": True,
                "service_radius_km": 10,
                "base_location_lat": 51.5074,
                "base_location_lng": -0.1278,
                "rating": 4.5,
                "total_jobs_completed": 25,
                "phone_number": "+1234567890",
                "is_available": True,
                "email_notifications": True,
                "sms_notifications": False
            }
        }


# Database indexes for performance optimization
User.__table_args__ = (
    Index("idx_users_cognito_sub", "cognito_sub"),
    Index("idx_users_email", "email"),
    Index("idx_users_role", "role"),
    Index("idx_users_created_at", "created_at"),
)

Client.__table_args__ = (
    Index("idx_clients_user_id", "user_id"),
    Index("idx_clients_business_name", "business_name"),
)

Dropper.__table_args__ = (
    Index("idx_droppers_user_id", "user_id"),
    Index("idx_droppers_service_radius", "service_radius_km"),
    Index("idx_droppers_location", "base_location_lat", "base_location_lng"),
    Index("idx_droppers_rating", "rating"),
    Index("idx_droppers_available", "is_available"),
)


# Job-Related Models
class DropJob(SQLModel, table=True):
    """
    Main job entity for leaflet distribution tasks.
    Created by clients and assigned to droppers.
    """
    __tablename__ = "drop_jobs"
    
    id: UUID = Field(
        default_factory=uuid4,
        sa_column=Column(PostgresUUID(as_uuid=True), primary_key=True)
    )
    client_id: UUID = Field(
        foreign_key="users.id",
        description="Client who created this job"
    )
    status: JobStatus = Field(
        default=JobStatus.DRAFT,
        description="Current status of the job"
    )
    title: str = Field(
        max_length=255,
        description="Job title/description"
    )
    description: Optional[str] = Field(
        default=None,
        sa_column=Column(Text),
        description="Detailed job description"
    )
    leaflet_file_url: Optional[str] = Field(
        default=None,
        max_length=500,
        description="URL to the leaflet file to be distributed"
    )
    
    @field_validator("leaflet_file_url")
    @classmethod
    def validate_leaflet_url(cls, v: Optional[str]) -> Optional[str]:
        """
        CRITICAL FIX #7: Validate leaflet file URL for security.
        
        Prevents:
        - SSRF attacks (Server-Side Request Forgery)
        - Local file access
        - Non-HTTP(S) protocols
        - Private IP ranges
        
        Returns:
            Validated URL or None
            
        Raises:
            ValueError: If URL is invalid or potentially malicious
        """
        if v is None or v.strip() == "":
            return None
        
        import re
        from urllib.parse import urlparse
        
        url = v.strip()
        
        # Parse URL
        try:
            parsed = urlparse(url)
        except Exception as e:
            raise ValueError(f"Invalid URL format: {str(e)}")
        
        # Validate scheme (only http/https allowed)
        if parsed.scheme not in ["http", "https"]:
            raise ValueError(
                f"Invalid URL scheme: {parsed.scheme}. "
                "Only http:// and https:// are allowed."
            )
        
        # Validate hostname exists
        if not parsed.netloc:
            raise ValueError("URL must include a hostname")
        
        # Extract hostname (remove port if present)
        hostname = parsed.netloc.split(":")[0].lower()
        
        # Block localhost and private IP ranges (SSRF protection)
        blocked_hosts = [
            "localhost",
            "127.0.0.1",
            "0.0.0.0",
            "::1",
            "[::1]",
        ]
        
        if hostname in blocked_hosts:
            raise ValueError(
                f"Access to localhost/loopback addresses is not allowed: {hostname}"
            )
        
        # Block private IP ranges (10.x.x.x, 172.16-31.x.x, 192.168.x.x)
        private_ip_patterns = [
            r"^10\.",
            r"^172\.(1[6-9]|2[0-9]|3[0-1])\.",
            r"^192\.168\.",
            r"^169\.254\.",  # Link-local
            r"^fc00:",       # IPv6 private
            r"^fd00:",       # IPv6 private
        ]
        
        for pattern in private_ip_patterns:
            if re.match(pattern, hostname):
                raise ValueError(
                    f"Access to private IP ranges is not allowed: {hostname}"
                )
        
        # Block metadata endpoints (cloud provider SSRF)
        metadata_hosts = [
            "169.254.169.254",  # AWS/Azure/GCP metadata
            "metadata.google.internal",
            "metadata",
        ]
        
        if hostname in metadata_hosts:
            raise ValueError(
                f"Access to cloud metadata endpoints is not allowed: {hostname}"
            )
        
        # Validate URL length
        if len(url) > 500:
            raise ValueError(f"URL too long: {len(url)} characters (max 500)")
        
        return url
    household_count: int = Field(
        gt=0,
        description="Number of households to receive leaflets"
    )
    cost_per_household_pence: int = Field(
        default=50,  # 50 pence per household
        gt=0,
        description="Cost per household in pence"
    )
    cost_total_pence: int = Field(
        gt=0,
        description="Total job cost in pence"
    )
    platform_fee_pence: int = Field(
        default=0,
        ge=0,
        description="Platform fee in pence"
    )
    dropper_payout_pence: int = Field(
        default=0,
        ge=0,
        description="Amount to be paid to dropper in pence"
    )
    payment_intent_id: Optional[str] = Field(
        default=None,
        max_length=255,
        description="Stripe PaymentIntent ID"
    )
    scheduled_date: date = Field(
        description="Date when the job should be completed"
    )
    min_time_per_segment_sec: int = Field(
        default=300,  # 5 minutes minimum
        gt=0,
        description="Minimum time per segment in seconds"
    )
    special_instructions: Optional[str] = Field(
        default=None,
        sa_column=Column(Text),
        description="Special instructions for the dropper"
    )
    paid_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True)),
        description="Timestamp when payment was completed"
    )
    is_broadcasted: bool = Field(
        default=False,
        description="Whether this job is broadcasted to all droppers"
    )
    broadcasted_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True)),
        description="Timestamp when job was broadcasted"
    )
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(DateTime(timezone=True))
    )
    updated_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True))
    )
    
    # Relationships
    client: User = Relationship(back_populates="jobs_created")
    job_area: Optional["JobArea"] = Relationship(back_populates="job")
    assignment: Optional["JobAssignment"] = Relationship(back_populates="job")
    transactions: List["Transaction"] = Relationship(back_populates="job")
    
    @field_validator("leaflet_file_url")
    @classmethod
    def validate_leaflet_file_url(cls, v: Optional[str]) -> Optional[str]:
        """
        SECURITY FIX 7: Validate file upload URLs
        - Enforces HTTPS only
        - Whitelists allowed domains (S3, CloudFront, trusted CDNs)
        - Prevents arbitrary URL injection
        """
        if v is None or v.strip() == "":
            return v
        
        # Normalize URL
        url = v.strip()
        
        # SECURITY: Enforce HTTPS only
        if not url.startswith("https://"):
            raise ValueError(
                "File URL must use HTTPS protocol for security. "
                f"Got: {url[:50]}..."
            )
        
        # SECURITY: Whitelist allowed domains
        # Add your trusted domains here (S3, CloudFront, etc.)
        allowed_domains = [
            ".amazonaws.com",      # AWS S3
            ".cloudfront.net",     # AWS CloudFront
            ".s3.amazonaws.com",   # S3 direct
            ".r2.cloudflarestorage.com",  # Cloudflare R2
            # Add your custom domain if you host files
            # "cdn.yourdomain.com",
        ]
        
        # Extract domain from URL
        try:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            domain = parsed.netloc.lower()
            
            # Check if domain matches any allowed pattern
            is_allowed = any(
                domain.endswith(allowed_domain) or domain == allowed_domain.lstrip(".")
                for allowed_domain in allowed_domains
            )
            
            if not is_allowed:
                raise ValueError(
                    f"File URL domain not allowed. Must be from trusted CDN. "
                    f"Got domain: {domain}. "
                    f"Allowed: {', '.join(allowed_domains)}"
                )
        except Exception as e:
            if isinstance(e, ValueError):
                raise
            raise ValueError(f"Invalid file URL format: {str(e)}")
        
        return url
    
    class Config:
        json_schema_extra = {
            "example": {
                "title": "Pizza Restaurant Leaflet Drop",
                "description": "Distribute pizza menu leaflets in residential area",
                "leaflet_file_url": "https://example.com/leaflet.pdf",
                "household_count": 500,
                "cost_total_pence": 25000,
                "scheduled_date": "2024-03-15",
                "special_instructions": "Please avoid apartments with 'No Junk Mail' signs"
            }
        }


class JobArea(SQLModel, table=True):
    """
    Defines the geographical area for leaflet distribution.
    Supports both polygon geojson and postcode-based areas.
    """
    __tablename__ = "job_areas"
    
    id: UUID = Field(
        default_factory=uuid4,
        sa_column=Column(PostgresUUID(as_uuid=True), primary_key=True)
    )
    job_id: UUID = Field(
        foreign_key="drop_jobs.id",
        unique=True,
        description="Reference to the DropJob"
    )
    area_type: str = Field(
        max_length=50,
        description="Type of area definition: 'polygon' or 'postcodes'"
    )
    geojson: Optional[dict] = Field(
        default=None,
        sa_column=Column(JSON),
        description="GeoJSON polygon defining the distribution area"
    )
    postcodes: Optional[List[str]] = Field(
        default=None,
        sa_column=Column(JSON),
        description="List of postcodes for distribution area"
    )
    center_lat: Optional[float] = Field(
        default=None,
        ge=-90,
        le=90,
        description="Center latitude of the area"
    )
    center_lng: Optional[float] = Field(
        default=None,
        ge=-180,
        le=180,
        description="Center longitude of the area"
    )
    radius_km: Optional[float] = Field(
        default=None,
        gt=0,
        description="Approximate radius of the area in kilometers"
    )
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(DateTime(timezone=True))
    )
    
    # Relationships
    job: DropJob = Relationship(back_populates="job_area")
    
    class Config:
        json_schema_extra = {
            "example": {
                "area_type": "polygon",
                "geojson": {
                    "type": "Polygon",
                    "coordinates": [[[-0.1, 51.5], [-0.1, 51.6], [0.0, 51.6], [0.0, 51.5], [-0.1, 51.5]]]
                },
                "center_lat": 51.55,
                "center_lng": -0.05,
                "radius_km": 2.5
            }
        }


class JobAssignment(SQLModel, table=True):
    """
    Represents the assignment of a job to a dropper.
    Tracks completion status and proof submission.
    """
    __tablename__ = "job_assignments"
    
    id: UUID = Field(
        default_factory=uuid4,
        sa_column=Column(PostgresUUID(as_uuid=True), primary_key=True)
    )
    job_id: UUID = Field(
        foreign_key="drop_jobs.id",
        unique=True,
        description="Reference to the DropJob"
    )
    dropper_id: UUID = Field(
        foreign_key="users.id",
        description="Dropper assigned to this job"
    )
    status: str = Field(
        default="active",
        description="Assignment status (active, completed, etc.)"
    )
    accepted_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=Column("assigned_at", DateTime(timezone=True)),
        description="When the dropper accepted the job"
    )
    started_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True)),
        description="When the dropper started the job"
    )
    completed_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True)),
        description="When the dropper completed the job"
    )
    time_spent_sec: Optional[int] = Field(
        default=None,
        ge=0,
        description="Time spent on the job in seconds"
    )
    proof_photos: Optional[List[str]] = Field(
        default=None,
        sa_column=Column(ARRAY(String)),
        description="URLs of proof photos submitted by dropper"
    )
    gps_log: Optional[dict] = Field(
        default=None,
        sa_column=Column(JSON),
        description="GPS tracking data during job completion"
    )
    verification_status: VerificationStatus = Field(
        default=VerificationStatus.PENDING,
        description="Admin verification status"
    )
    verification_notes: Optional[str] = Field(
        default=None,
        sa_column=Column(Text),
        description="Admin notes on verification"
    )
    verified_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True)),
        description="When the job was verified by admin"
    )
    verified_by: Optional[UUID] = Field(
        default=None,
        foreign_key="users.id",
        description="Admin who verified the job"
    )
    rejection_reason: Optional[str] = Field(
        default=None,
        sa_column=Column(Text),
        description="Reason for rejection if verification failed"
    )
    
    # Relationships
    job: DropJob = Relationship(back_populates="assignment")
    dropper: User = Relationship(
        back_populates="job_assignments",
        sa_relationship_kwargs={"foreign_keys": "JobAssignment.dropper_id"}
    )
    
    class Config:
        json_schema_extra = {
            "example": {
                "time_spent_sec": 3600,
                "proof_photos": [
                    "https://example.com/proof1.jpg",
                    "https://example.com/proof2.jpg"
                ],
                "gps_log": {
                    "start_location": {"lat": 51.5074, "lng": -0.1278},
                    "end_location": {"lat": 51.5080, "lng": -0.1270},
                    "waypoints": []
                },
                "verification_status": "pending"
            }
        }


# Database indexes for job-related models
DropJob.__table_args__ = (
    Index("idx_drop_jobs_client_id", "client_id"),
    Index("idx_drop_jobs_status", "status"),
    Index("idx_drop_jobs_scheduled_date", "scheduled_date"),
    Index("idx_drop_jobs_created_at", "created_at"),
    Index("idx_drop_jobs_status_scheduled", "status", "scheduled_date"),
    Index("idx_drop_jobs_broadcasted_status", "is_broadcasted", "status"),
)

JobArea.__table_args__ = (
    Index("idx_job_areas_job_id", "job_id"),
    Index("idx_job_areas_center", "center_lat", "center_lng"),
    Index("idx_job_areas_type", "area_type"),
)

JobAssignment.__table_args__ = (
    Index("idx_job_assignments_job_id", "job_id"),
    Index("idx_job_assignments_dropper_id", "dropper_id"),
    Index("idx_job_assignments_verification_status", "verification_status"),
    Index("idx_job_assignments_completed_at", "completed_at"),
    Index("idx_job_assignments_accepted_at", "assigned_at"),
)


# Transaction and Payment Models
class Transaction(SQLModel, table=True):
    """
    Tracks all financial transactions in the platform.
    Includes payments from clients and payouts to droppers.
    """
    __tablename__ = "transactions"
    
    id: UUID = Field(
        default_factory=uuid4,
        sa_column=Column(PostgresUUID(as_uuid=True), primary_key=True)
    )
    user_id: UUID = Field(
        foreign_key="users.id",
        description="User associated with this transaction"
    )
    job_id: Optional[UUID] = Field(
        default=None,
        foreign_key="drop_jobs.id",
        description="Job associated with this transaction"
    )
    transaction_type: str = Field(
        max_length=50,
        description="Type of transaction: 'payment', 'payout', 'refund', 'fee'"
    )
    amount_pence: int = Field(
        description="Transaction amount in pence"
    )
    currency: str = Field(
        default="AUD",
        max_length=3,
        description="Currency code (ISO 4217)"
    )
    status: PaymentStatus = Field(
        default=PaymentStatus.PENDING,
        description="Current status of the transaction"
    )
    stripe_payment_intent_id: Optional[str] = Field(
        default=None,
        max_length=255,
        description="Stripe PaymentIntent ID for payments"
    )
    stripe_transfer_id: Optional[str] = Field(
        default=None,
        max_length=255,
        description="Stripe Transfer ID for payouts"
    )
    stripe_charge_id: Optional[str] = Field(
        default=None,
        max_length=255,
        description="Stripe Charge ID"
    )
    stripe_refund_id: Optional[str] = Field(
        default=None,
        max_length=255,
        description="Stripe Refund ID for refunded transactions"
    )
    description: str = Field(
        max_length=500,
        description="Human-readable description of the transaction"
    )
    transaction_metadata: Optional[dict] = Field(
        default=None,
        sa_column=Column(JSON),
        description="Additional metadata for the transaction"
    )
    failure_reason: Optional[str] = Field(
        default=None,
        sa_column=Column(Text),
        description="Reason for transaction failure"
    )
    processed_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True)),
        description="When the transaction was processed"
    )
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(DateTime(timezone=True))
    )
    updated_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True))
    )
    
    # Relationships
    user: User = Relationship(back_populates="transactions")
    job: Optional[DropJob] = Relationship(back_populates="transactions")
    
    class Config:
        json_schema_extra = {
            "example": {
                "transaction_type": "payment",
                "amount_pence": 25000,
                "currency": "AUD",
                "status": "completed",
                "description": "Payment for leaflet distribution job",
                "metadata": {
                    "job_title": "Pizza Restaurant Leaflet Drop",
                    "household_count": 500
                }
            }
        }


class PaymentMethod(SQLModel, table=True):
    """
    Stores payment method information for users.
    Links to Stripe payment methods for secure processing.
    """
    __tablename__ = "payment_methods"
    
    id: UUID = Field(
        default_factory=uuid4,
        sa_column=Column(PostgresUUID(as_uuid=True), primary_key=True)
    )
    user_id: UUID = Field(
        foreign_key="users.id",
        description="User who owns this payment method"
    )
    stripe_payment_method_id: str = Field(
        max_length=255,
        unique=True,
        description="Stripe PaymentMethod ID"
    )
    payment_method_type: str = Field(
        max_length=50,
        description="Type of payment method: 'card', 'bank_account', etc."
    )
    card_brand: Optional[str] = Field(
        default=None,
        max_length=50,
        description="Card brand (visa, mastercard, etc.)"
    )
    card_last4: Optional[str] = Field(
        default=None,
        max_length=4,
        description="Last 4 digits of card number"
    )
    card_exp_month: Optional[int] = Field(
        default=None,
        ge=1,
        le=12,
        description="Card expiration month"
    )
    card_exp_year: Optional[int] = Field(
        default=None,
        ge=2024,
        description="Card expiration year"
    )
    is_default: bool = Field(
        default=False,
        description="Whether this is the user's default payment method"
    )
    is_active: bool = Field(
        default=True,
        description="Whether this payment method is active"
    )
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(DateTime(timezone=True))
    )
    
    class Config:
        json_schema_extra = {
            "example": {
                "payment_method_type": "card",
                "card_brand": "visa",
                "card_last4": "4242",
                "card_exp_month": 12,
                "card_exp_year": 2025,
                "is_default": True
            }
        }


# Database indexes for transaction and payment models
Transaction.__table_args__ = (
    Index("idx_transactions_user_id", "user_id"),
    Index("idx_transactions_job_id", "job_id"),
    Index("idx_transactions_type", "transaction_type"),
    Index("idx_transactions_status", "status"),
    Index("idx_transactions_created_at", "created_at"),
    Index("idx_transactions_stripe_payment_intent", "stripe_payment_intent_id"),
    Index("idx_transactions_user_type_status", "user_id", "transaction_type", "status"),
)

PaymentMethod.__table_args__ = (
    Index("idx_payment_methods_user_id", "user_id"),
    Index("idx_payment_methods_stripe_id", "stripe_payment_method_id"),
    Index("idx_payment_methods_default", "user_id", "is_default"),
    Index("idx_payment_methods_active", "is_active"),
)


# Map and Location Models
class DropPoint(SQLModel, table=True):
    """
    Represents a drop point location where leaflets should be distributed.
    Can be assigned to a specific dropper.
    """
    __tablename__ = "drop_points"
    
    id: UUID = Field(
        default_factory=uuid4,
        sa_column=Column(PostgresUUID(as_uuid=True), primary_key=True)
    )
    job_id: Optional[UUID] = Field(
        default=None,
        foreign_key="drop_jobs.id",
        description="Job this drop point belongs to"
    )
    lat: float = Field(
        ge=-90,
        le=90,
        description="Latitude of the drop point"
    )
    lng: float = Field(
        ge=-180,
        le=180,
        description="Longitude of the drop point"
    )
    name: str = Field(
        max_length=255,
        description="Name or description of the drop point"
    )
    client_id: UUID = Field(
        foreign_key="users.id",
        description="Client who owns this drop point"
    )
    dropper_id: Optional[UUID] = Field(
        default=None,
        foreign_key="users.id",
        description="Dropper assigned to this drop point"
    )
    status: str = Field(
        default="draft",
        max_length=50,
        description="Status: draft, assigned, active, completed"
    )
    order: Optional[int] = Field(
        default=None,
        description="Order in the route (for route optimization)"
    )
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(DateTime(timezone=True))
    )
    updated_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True))
    )
    
    class Config:
        json_schema_extra = {
            "example": {
                "lat": -33.8688,
                "lng": 151.2093,
                "name": "Main Street Drop Point",
                "status": "active"
            }
        }


class DropZone(SQLModel, table=True):
    """
    Represents a polygon zone (drop zone) defined by a client on the map.
    Stores the polygon geometry as GeoJSON.
    """
    __tablename__ = "drop_zones"
    
    id: UUID = Field(
        default_factory=uuid4,
        sa_column=Column(PostgresUUID(as_uuid=True), primary_key=True)
    )
    polygon_json: dict = Field(
        sa_column=Column(JSON),
        description="Polygon coordinates as JSON array of {lat, lng} objects"
    )
    client_id: UUID = Field(
        foreign_key="users.id",
        description="Client who created this drop zone"
    )
    name: Optional[str] = Field(
        default=None,
        max_length=255,
        description="Optional name for the drop zone"
    )
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(DateTime(timezone=True))
    )
    updated_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True))
    )
    
    class Config:
        json_schema_extra = {
            "example": {
                "polygon_json": {
                    "coordinates": [
                        {"lat": -33.8688, "lng": 151.2093},
                        {"lat": -33.8700, "lng": 151.2100},
                        {"lat": -33.8700, "lng": 151.2093},
                        {"lat": -33.8688, "lng": 151.2093}
                    ]
                },
                "name": "City Center Zone"
            }
        }


class DroperLocation(SQLModel, table=True):
    """
    Tracks the current location of a dropper for real-time tracking.
    Stores location updates with timestamps.
    """
    __tablename__ = "droper_locations"
    
    id: UUID = Field(
        default_factory=uuid4,
        sa_column=Column(PostgresUUID(as_uuid=True), primary_key=True)
    )
    dropper_id: UUID = Field(
        foreign_key="users.id",
        description="Dropper whose location is being tracked"
    )
    lat: float = Field(
        ge=-90,
        le=90,
        description="Current latitude"
    )
    lng: float = Field(
        ge=-180,
        le=180,
        description="Current longitude"
    )
    timestamp: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(DateTime(timezone=True)),
        description="Timestamp when location was recorded"
    )
    
    class Config:
        json_schema_extra = {
            "example": {
                "dropper_id": "12345678-1234-1234-1234-123456789012",
                "lat": -33.8688,
                "lng": 151.2093,
                "timestamp": "2024-01-15T10:30:00Z"
            }
        }


# Database indexes for map and location models
DropPoint.__table_args__ = (
    Index("idx_drop_points_client_id", "client_id"),
    Index("idx_drop_points_dropper_id", "dropper_id"),
    Index("idx_drop_points_status", "status"),
    Index("idx_drop_points_location", "lat", "lng"),
    Index("idx_drop_points_created_at", "created_at"),
)

DropZone.__table_args__ = (
    Index("idx_drop_zones_client_id", "client_id"),
    Index("idx_drop_zones_created_at", "created_at"),
)

DroperLocation.__table_args__ = (
    Index("idx_droper_locations_dropper_id", "dropper_id"),
    Index("idx_droper_locations_timestamp", "timestamp"),
    Index("idx_droper_locations_dropper_timestamp", "dropper_id", "timestamp"),
)


class PricingTier(SQLModel, table=True):
    """
    Pricing tier configuration for different delivery types.
    """
    __tablename__ = "pricing_tiers"
    
    id: UUID = Field(
        default_factory=uuid4,
        sa_column=Column(PostgresUUID(as_uuid=True), primary_key=True)
    )
    name: str = Field(description="Pricing tier name (e.g., 'Local Delivery', 'Express Delivery')")
    base_price_pence: int = Field(description="Base price in pence")
    price_per_mile_pence: int = Field(description="Price per mile in pence")
    max_distance_miles: float = Field(description="Maximum distance in miles for this tier")
    description: Optional[str] = Field(default=None, description="Description of the pricing tier")
    is_active: bool = Field(default=True, description="Whether this tier is currently active")
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(DateTime(timezone=True))
    )
    updated_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(DateTime(timezone=True))
    )


class CommissionRate(SQLModel, table=True):
    """
    Commission rate configuration for platform fees.
    """
    __tablename__ = "commission_rates"
    
    id: UUID = Field(
        default_factory=uuid4,
        sa_column=Column(PostgresUUID(as_uuid=True), primary_key=True)
    )
    rate_percentage: float = Field(ge=0, le=100, description="Commission rate as percentage (0-100)")
    minimum_fee_pence: int = Field(default=0, description="Minimum commission fee in pence")
    maximum_fee_pence: Optional[int] = Field(default=None, description="Maximum commission fee in pence")
    effective_date: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(DateTime(timezone=True)),
        description="Date when this rate becomes effective"
    )
    is_active: bool = Field(default=True, description="Whether this rate is currently active")
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(DateTime(timezone=True))
    )


class CostSettings(SQLModel, table=True):
    """
    Global cost settings for the platform.
    """
    __tablename__ = "cost_settings"
    
    id: UUID = Field(
        default_factory=uuid4,
        sa_column=Column(PostgresUUID(as_uuid=True), primary_key=True)
    )
    price_per_household_pence: int = Field(default=50, description="Price per household/leaflet in pence")
    platform_fee_percentage: float = Field(default=15.0, description="Platform fee as percentage (0-100)")
    platform_fee_pence: int = Field(default=100, description="Platform fee in pence (legacy)")
    processing_fee_pence: int = Field(default=30, description="Processing fee in pence")
    cancellation_fee_pence: int = Field(default=250, description="Cancellation fee in pence")
    dispute_fee_pence: int = Field(default=500, description="Dispute handling fee in pence")
    refund_processing_fee_pence: int = Field(default=150, description="Refund processing fee in pence")
    late_fee_pence: int = Field(default=300, description="Late delivery fee in pence")
    last_updated: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(DateTime(timezone=True))
    )


class SystemSettings(SQLModel, table=True):
    """
    System-wide platform settings.
    """
    __tablename__ = "system_settings"
    
    id: UUID = Field(
        default_factory=uuid4,
        sa_column=Column(PostgresUUID(as_uuid=True), primary_key=True)
    )
    # Platform Controls
    auto_assign_enabled: bool = Field(default=True, description="Auto-assign droppers to jobs")
    broadcast_enabled: bool = Field(default=True, description="Enable job broadcasting")
    maintenance_mode: bool = Field(default=False, description="Platform maintenance mode")
    require_checkins: bool = Field(default=True, description="Require dropper GPS check-ins")
    
    # Notification Settings
    email_alerts: bool = Field(default=True, description="Enable email alerts")
    sms_alerts: bool = Field(default=False, description="Enable SMS alerts")
    push_notifications: bool = Field(default=True, description="Enable push notifications")
    
    last_updated: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(DateTime(timezone=True))
    )


class Dispute(SQLModel, table=True):
    """
    Dispute model for handling disputes between clients and droppers.
    """
    __tablename__ = "disputes"
    
    id: UUID = Field(
        default_factory=uuid4,
        sa_column=Column(PostgresUUID(as_uuid=True), primary_key=True)
    )
    job_id: UUID = Field(
        foreign_key="drop_jobs.id",
        description="Job associated with the dispute"
    )
    client_id: UUID = Field(
        foreign_key="users.id",
        description="Client who raised the dispute"
    )
    dropper_id: UUID = Field(
        foreign_key="users.id",
        description="Dropper involved in the dispute"
    )
    reason: str = Field(description="Reason for the dispute")
    description: str = Field(sa_column=Column(Text), description="Detailed description of the dispute")
    photos: Optional[List[str]] = Field(
        default=None,
        sa_column=Column(JSON),
        description="List of photo URLs as evidence"
    )
    status: str = Field(
        default="pending",
        description="Dispute status: pending, resolved, escalated"
    )
    amount_pence: int = Field(description="Dispute amount in pence")
    refund_amount_pence: Optional[int] = Field(default=None, description="Refund amount in pence if approved")
    resolution: Optional[str] = Field(default=None, sa_column=Column(Text), description="Resolution details")
    assigned_to: Optional[UUID] = Field(
        default=None,
        foreign_key="users.id",
        description="Admin user assigned to handle the dispute"
    )
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(DateTime(timezone=True))
    )
    resolved_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True))
    )
    updated_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(DateTime(timezone=True))
    )


class DisputeNote(SQLModel, table=True):
    """
    Notes added to disputes for tracking investigation and resolution.
    """
    __tablename__ = "dispute_notes"
    
    id: UUID = Field(
        default_factory=uuid4,
        sa_column=Column(PostgresUUID(as_uuid=True), primary_key=True)
    )
    dispute_id: UUID = Field(
        foreign_key="disputes.id",
        description="Dispute this note belongs to"
    )
    note: str = Field(sa_column=Column(Text), description="Note content")
    is_internal: bool = Field(default=False, description="Whether this is an internal admin note")
    created_by: UUID = Field(
        foreign_key="users.id",
        description="User who created the note"
    )
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(DateTime(timezone=True))
    )


# Database indexes for pricing and dispute models
PricingTier.__table_args__ = (
    Index("idx_pricing_tiers_active", "is_active"),
    Index("idx_pricing_tiers_created_at", "created_at"),
)

CommissionRate.__table_args__ = (
    Index("idx_commission_rates_active", "is_active"),
    Index("idx_commission_rates_effective_date", "effective_date"),
)

Dispute.__table_args__ = (
    Index("idx_disputes_job_id", "job_id"),
    Index("idx_disputes_client_id", "client_id"),
    Index("idx_disputes_dropper_id", "dropper_id"),
    Index("idx_disputes_status", "status"),
    Index("idx_disputes_assigned_to", "assigned_to"),
    Index("idx_disputes_created_at", "created_at"),
)

DisputeNote.__table_args__ = (
    Index("idx_dispute_notes_dispute_id", "dispute_id"),
    Index("idx_dispute_notes_created_at", "created_at"),
)


class Invoice(SQLModel, table=True):
    """
    Invoice record for completed payments via Stripe Checkout.
    Stores payment transaction details for analytics and record-keeping.
    """
    __tablename__ = "invoices"
    
    id: UUID = Field(
        default_factory=uuid4,
        sa_column=Column(PostgresUUID(as_uuid=True), primary_key=True)
    )
    user_id: UUID = Field(
        foreign_key="users.id",
        description="User (client) who made the payment"
    )
    stripe_session_id: str = Field(
        unique=True,
        max_length=255,
        description="Stripe Checkout Session ID"
    )
    stripe_payment_intent_id: Optional[str] = Field(
        default=None,
        max_length=255,
        description="Stripe PaymentIntent ID"
    )
    amount_total_pence: int = Field(
        gt=0,
        description="Total amount paid in pence"
    )
    currency: str = Field(
        default="AUD",
        max_length=3,
        description="Currency code (ISO 4217)"
    )
    status: str = Field(
        max_length=50,
        description="Payment status: paid, refunded, failed"
    )
    job_ids: List[str] = Field(
        sa_column=Column(JSON),
        description="List of job IDs included in this invoice (stored as strings for JSON serialization)"
    )
    invoice_pdf_url: Optional[str] = Field(
        default=None,
        max_length=500,
        description="URL to download the invoice PDF from Stripe"
    )
    invoice_metadata: Optional[dict] = Field(
        default=None,
        sa_column=Column(JSON),
        description="Additional metadata for the invoice"
    )
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(DateTime(timezone=True)),
        description="When the invoice was created"
    )
    updated_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True)),
        description="When the invoice was last updated"
    )
    
    # Relationships
    user: User = Relationship()
    
    class Config:
        json_schema_extra = {
            "example": {
                "stripe_session_id": "cs_test_a1b2c3d4e5f6",
                "stripe_payment_intent_id": "pi_test_1234567890",
                "amount_total_pence": 25000,
                "currency": "AUD",
                "status": "paid",
                "job_ids": ["12345678-1234-1234-1234-123456789012"],
                "invoice_metadata": {
                    "job_count": 1,
                    "household_count": 500
                }
            }
        }


# Database indexes for Invoice model
Invoice.__table_args__ = (
    Index("idx_invoices_user_id", "user_id"),
    Index("idx_invoices_stripe_session_id", "stripe_session_id"),
    Index("idx_invoices_created_at", "created_at"),
    Index("idx_invoices_status", "status"),
)


class SavedJob(SQLModel, table=True):
    """
    Saved/bookmarked jobs for users (droppers can save jobs they're interested in).
    """
    __tablename__ = "saved_jobs"
    
    id: UUID = Field(
        default_factory=uuid4,
        sa_column=Column(PostgresUUID(as_uuid=True), primary_key=True)
    )
    user_id: UUID = Field(
        foreign_key="users.id",
        description="User who saved the job"
    )
    job_id: UUID = Field(
        foreign_key="drop_jobs.id",
        description="The saved job"
    )
    saved_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(DateTime(timezone=True)),
        description="When the job was saved"
    )


# Database indexes for SavedJob model
SavedJob.__table_args__ = (
    Index("idx_saved_jobs_user_id", "user_id"),
    Index("idx_saved_jobs_job_id", "job_id"),
    UniqueConstraint("user_id", "job_id", name="uq_saved_jobs_user_job"),
)


# Export all models for easy importing
__all__ = [
    "UserRole",
    "JobStatus", 
    "VerificationStatus",
    "PaymentStatus",
    "User",
    "Client",
    "Dropper",
    "DropJob",
    "JobArea",
    "JobAssignment",
    "Transaction",
    "PaymentMethod",
    "DropPoint",
    "DropZone",
    "DroperLocation",
    "PricingTier",
    "CommissionRate",
    "CostSettings",
    "Dispute",
    "DisputeNote",
    "Invoice",
    "SavedJob",
]