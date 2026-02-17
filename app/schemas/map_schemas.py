"""
Pydantic schemas for map and location-related API endpoints.
"""

from typing import List, Optional
from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, Field


class DropPointResponse(BaseModel):
    """Response schema for a drop point."""
    id: UUID
    lat: float
    lng: float
    name: str
    status: str
    client_id: Optional[UUID] = None
    dropper_id: Optional[UUID] = None
    created_at: datetime
    
    class Config:
        from_attributes = True


class DropZoneCreate(BaseModel):
    """Schema for creating a drop zone."""
    polygon_json: dict = Field(
        ...,
        description="Polygon coordinates as array of {lat, lng} objects or GeoJSON format"
    )
    name: Optional[str] = Field(
        None,
        max_length=255,
        description="Optional name for the drop zone"
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


class DropZoneResponse(BaseModel):
    """Response schema for a drop zone."""
    id: UUID
    polygon_json: dict
    client_id: UUID
    name: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None
    
    class Config:
        from_attributes = True


class DroperLocationUpdate(BaseModel):
    """Schema for updating dropper location."""
    lat: float = Field(..., ge=-90, le=90, description="Latitude")
    lng: float = Field(..., ge=-180, le=180, description="Longitude")
    
    class Config:
        json_schema_extra = {
            "example": {
                "lat": -33.8688,
                "lng": 151.2093
            }
        }


class DroperLocationResponse(BaseModel):
    """Response schema for dropper location."""
    id: UUID
    dropper_id: UUID
    lat: float
    lng: float
    timestamp: datetime
    
    class Config:
        from_attributes = True

