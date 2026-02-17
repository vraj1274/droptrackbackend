"""
Pydantic schemas for invoice-related API requests and responses.
Defines validation and serialization for invoice retrieval endpoints.
"""

from typing import Optional, List
from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, Field


class InvoiceResponse(BaseModel):
    """Schema for invoice in responses."""
    
    id: UUID
    user_id: UUID
    stripe_session_id: str
    stripe_payment_intent_id: Optional[str]
    amount_total_pence: int
    currency: str
    status: str
    job_ids: List[UUID]
    job_titles: List[str] = Field(
        default_factory=list,
        description="Titles of jobs included in this invoice"
    )
    invoice_pdf_url: Optional[str] = Field(
        default=None,
        description="URL to download the invoice PDF from Stripe"
    )
    invoice_metadata: Optional[dict]
    created_at: datetime
    updated_at: Optional[datetime]
    
    class Config:
        from_attributes = True


class InvoiceListResponse(BaseModel):
    """Schema for invoice list responses."""
    
    invoices: List[InvoiceResponse]
    total_count: int
    
    class Config:
        from_attributes = True


class InvoiceDetailResponse(BaseModel):
    """Schema for detailed invoice response with job details."""
    
    id: UUID
    user_id: UUID
    stripe_session_id: str
    stripe_payment_intent_id: Optional[str]
    amount_total_pence: int
    currency: str
    status: str
    job_ids: List[UUID]
    jobs: List[dict] = Field(
        default_factory=list,
        description="Detailed information about jobs in this invoice"
    )
    invoice_pdf_url: Optional[str] = Field(
        default=None,
        description="URL to download the invoice PDF from Stripe"
    )
    invoice_metadata: Optional[dict]
    created_at: datetime
    updated_at: Optional[datetime]
    
    class Config:
        from_attributes = True


# Export all schemas
__all__ = [
    "InvoiceResponse",
    "InvoiceListResponse",
    "InvoiceDetailResponse",
]
