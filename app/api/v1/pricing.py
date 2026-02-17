"""
Pricing management API endpoints.
Handles pricing tiers, commission rates, and cost settings.
"""

from typing import List, Optional, Dict, Any
from uuid import UUID
from datetime import datetime
import logging
from fastapi import APIRouter, Depends, HTTPException, status, Body
from sqlmodel import Session, select

from app.database import get_session
from app.models import PricingTier, CommissionRate, CostSettings, SystemSettings, User
from app.api.deps import get_current_active_user, require_admin_role

logger = logging.getLogger(__name__)

router = APIRouter(tags=["pricing"])


# ====================================================================
# Pricing Tiers Endpoints
# ====================================================================

@router.get(
    "/tiers",
    summary="Get all pricing tiers",
    description="Fetch all pricing tiers (active and inactive)"
)
async def get_pricing_tiers(
    active_only: bool = False,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_session)
):
    """Get all pricing tiers, optionally filtered to active only."""
    try:
        statement = select(PricingTier)
        if active_only:
            statement = statement.where(PricingTier.is_active == True)
        statement = statement.order_by(PricingTier.created_at.desc())
        
        tiers = db.exec(statement).all()
        
        return [
            {
                "id": str(tier.id),
                "name": tier.name,
                "basePrice": tier.base_price_pence / 100,  # Convert to dollars
                "pricePerMile": tier.price_per_mile_pence / 100,
                "maxDistance": tier.max_distance_miles,
                "description": tier.description,
                "isActive": tier.is_active,
                "createdAt": tier.created_at.isoformat(),
                "updatedAt": tier.updated_at.isoformat()
            }
            for tier in tiers
        ]
    except Exception as e:
        logger.error(f"Error fetching pricing tiers: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch pricing tiers: {str(e)}"
        )


@router.post(
    "/tiers",
    summary="Create new pricing tier",
    description="Create a new pricing tier configuration",
    status_code=status.HTTP_201_CREATED
)
async def create_pricing_tier(
    tier_data: Dict[str, Any] = Body(...),
    current_user: User = Depends(require_admin_role()),
    db: Session = Depends(get_session)
):
    """Create a new pricing tier."""
    try:
        new_tier = PricingTier(
            name=tier_data["name"],
            base_price_pence=int(tier_data["basePrice"] * 100),  # Convert to pence
            price_per_mile_pence=int(tier_data["pricePerMile"] * 100),
            max_distance_miles=float(tier_data["maxDistance"]),
            description=tier_data.get("description"),
            is_active=tier_data.get("isActive", True),
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )
        
        db.add(new_tier)
        db.commit()
        db.refresh(new_tier)
        
        return {
            "id": str(new_tier.id),
            "name": new_tier.name,
            "basePrice": new_tier.base_price_pence / 100,
            "pricePerMile": new_tier.price_per_mile_pence / 100,
            "maxDistance": new_tier.max_distance_miles,
            "description": new_tier.description,
            "isActive": new_tier.is_active,
            "createdAt": new_tier.created_at.isoformat(),
            "updatedAt": new_tier.updated_at.isoformat()
        }
    except Exception as e:
        logger.error(f"Error creating pricing tier: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create pricing tier: {str(e)}"
        )


@router.put(
    "/tiers/{tier_id}",
    summary="Update pricing tier",
    description="Update an existing pricing tier"
)
async def update_pricing_tier(
    tier_id: UUID,
    tier_data: Dict[str, Any] = Body(...),
    current_user: User = Depends(require_admin_role()),
    db: Session = Depends(get_session)
):
    """Update an existing pricing tier."""
    try:
        tier = db.exec(select(PricingTier).where(PricingTier.id == tier_id)).first()
        if not tier:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Pricing tier not found"
            )
        
        if "name" in tier_data:
            tier.name = tier_data["name"]
        if "basePrice" in tier_data:
            tier.base_price_pence = int(tier_data["basePrice"] * 100)
        if "pricePerMile" in tier_data:
            tier.price_per_mile_pence = int(tier_data["pricePerMile"] * 100)
        if "maxDistance" in tier_data:
            tier.max_distance_miles = float(tier_data["maxDistance"])
        if "description" in tier_data:
            tier.description = tier_data["description"]
        if "isActive" in tier_data:
            tier.is_active = tier_data["isActive"]
        
        tier.updated_at = datetime.utcnow()
        
        db.add(tier)
        db.commit()
        db.refresh(tier)
        
        return {
            "id": str(tier.id),
            "name": tier.name,
            "basePrice": tier.base_price_pence / 100,
            "pricePerMile": tier.price_per_mile_pence / 100,
            "maxDistance": tier.max_distance_miles,
            "description": tier.description,
            "isActive": tier.is_active,
            "createdAt": tier.created_at.isoformat(),
            "updatedAt": tier.updated_at.isoformat()
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating pricing tier: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update pricing tier: {str(e)}"
        )


@router.delete(
    "/tiers/{tier_id}",
    summary="Delete pricing tier",
    description="Delete a pricing tier (soft delete by setting is_active=False)",
    status_code=status.HTTP_204_NO_CONTENT
)
async def delete_pricing_tier(
    tier_id: UUID,
    current_user: User = Depends(require_admin_role()),
    db: Session = Depends(get_session)
):
    """Delete (deactivate) a pricing tier."""
    try:
        tier = db.exec(select(PricingTier).where(PricingTier.id == tier_id)).first()
        if not tier:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Pricing tier not found"
            )
        
        tier.is_active = False
        tier.updated_at = datetime.utcnow()
        
        db.add(tier)
        db.commit()
        
        return None
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting pricing tier: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete pricing tier: {str(e)}"
        )


# ====================================================================
# Commission Rates Endpoints
# ====================================================================

@router.get(
    "/commission-rates",
    summary="Get commission rates",
    description="Fetch all commission rate configurations"
)
async def get_commission_rates(
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_session)
):
    """Get all commission rates."""
    try:
        rates = db.exec(
            select(CommissionRate)
            .where(CommissionRate.is_active == True)
            .order_by(CommissionRate.effective_date.desc())
        ).all()
        
        return [
            {
                "id": str(rate.id),
                "ratePercentage": rate.rate_percentage,
                "minimumFee": rate.minimum_fee_pence / 100,
                "maximumFee": rate.maximum_fee_pence / 100 if rate.maximum_fee_pence else None,
                "effectiveDate": rate.effective_date.isoformat(),
                "isActive": rate.is_active,
                "createdAt": rate.created_at.isoformat()
            }
            for rate in rates
        ]
    except Exception as e:
        logger.error(f"Error fetching commission rates: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch commission rates: {str(e)}"
        )


@router.post(
    "/commission-rates",
    summary="Create or update commission rate",
    description="Create a new commission rate or update existing active rate",
    status_code=status.HTTP_201_CREATED
)
async def update_commission_rates(
    rate_data: Dict[str, Any] = Body(...),
    current_user: User = Depends(require_admin_role()),
    db: Session = Depends(get_session)
):
    """Update commission rates."""
    try:
        # Deactivate existing active rates
        existing_rates = db.exec(
            select(CommissionRate).where(CommissionRate.is_active == True)
        ).all()
        for rate in existing_rates:
            rate.is_active = False
        
        # Create new rate
        new_rate = CommissionRate(
            rate_percentage=float(rate_data["ratePercentage"]),
            minimum_fee_pence=int(rate_data.get("minimumFee", 0) * 100),
            maximum_fee_pence=int(rate_data["maximumFee"] * 100) if rate_data.get("maximumFee") else None,
            effective_date=datetime.utcnow(),
            is_active=True,
            created_at=datetime.utcnow()
        )
        
        db.add(new_rate)
        db.commit()
        db.refresh(new_rate)
        
        return {
            "id": str(new_rate.id),
            "ratePercentage": new_rate.rate_percentage,
            "minimumFee": new_rate.minimum_fee_pence / 100,
            "maximumFee": new_rate.maximum_fee_pence / 100 if new_rate.maximum_fee_pence else None,
            "effectiveDate": new_rate.effective_date.isoformat(),
            "isActive": new_rate.is_active,
            "createdAt": new_rate.created_at.isoformat()
        }
    except Exception as e:
        logger.error(f"Error updating commission rates: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update commission rates: {str(e)}"
        )


# ====================================================================
# Cost Settings Endpoints
# ====================================================================

@router.get(
    "/cost-settings",
    summary="Get cost settings",
    description="Fetch global cost settings"
)
async def get_cost_settings(
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_session)
):
    """Get cost settings (returns single record or creates default)."""
    try:
        settings = db.exec(select(CostSettings).limit(1)).first()
        
        if not settings:
            # Create default settings
            settings = CostSettings(
                price_per_household_pence=50,
                platform_fee_percentage=15.0,
                platform_fee_pence=100,
                processing_fee_pence=30,
                cancellation_fee_pence=250,
                dispute_fee_pence=500,
                refund_processing_fee_pence=150,
                late_fee_pence=300,
                last_updated=datetime.utcnow()
            )
            db.add(settings)
            db.commit()
            db.refresh(settings)
        
        return {
            "pricePerHousehold": settings.price_per_household_pence / 100,
            "platformFeePercentage": settings.platform_fee_percentage,
            "platformFee": settings.platform_fee_pence / 100,
            "processingFee": settings.processing_fee_pence / 100,
            "cancellationFee": settings.cancellation_fee_pence / 100,
            "disputeFee": settings.dispute_fee_pence / 100,
            "refundProcessingFee": settings.refund_processing_fee_pence / 100,
            "lateFee": settings.late_fee_pence / 100,
            "lastUpdated": settings.last_updated.isoformat()
        }
    except Exception as e:
        logger.error(f"Error fetching cost settings: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch cost settings: {str(e)}"
        )


@router.put(
    "/cost-settings",
    summary="Update cost settings",
    description="Update global cost settings"
)
async def update_cost_settings(
    settings_data: Dict[str, Any] = Body(...),
    current_user: User = Depends(require_admin_role()),
    db: Session = Depends(get_session)
):
    """Update cost settings."""
    try:
        settings = db.exec(select(CostSettings).limit(1)).first()
        
        if not settings:
            settings = CostSettings()
            db.add(settings)
        
        if "pricePerHousehold" in settings_data and settings_data["pricePerHousehold"] is not None:
            try:
                settings.price_per_household_pence = int(float(settings_data["pricePerHousehold"]) * 100)
            except (ValueError, TypeError) as e:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid pricePerHousehold value: {str(e)}"
                )
        if "platformFeePercentage" in settings_data and settings_data["platformFeePercentage"] is not None:
            try:
                settings.platform_fee_percentage = float(settings_data["platformFeePercentage"])
            except (ValueError, TypeError) as e:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid platformFeePercentage value: {str(e)}"
                )
        if "platformFee" in settings_data and settings_data["platformFee"] is not None:
            try:
                settings.platform_fee_pence = int(float(settings_data["platformFee"]) * 100)
            except (ValueError, TypeError):
                pass  # Optional field, skip if invalid
        if "processingFee" in settings_data and settings_data["processingFee"] is not None:
            try:
                settings.processing_fee_pence = int(float(settings_data["processingFee"]) * 100)
            except (ValueError, TypeError):
                pass  # Optional field, skip if invalid
        if "cancellationFee" in settings_data and settings_data["cancellationFee"] is not None:
            try:
                settings.cancellation_fee_pence = int(float(settings_data["cancellationFee"]) * 100)
            except (ValueError, TypeError) as e:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid cancellationFee value: {str(e)}"
                )
        if "disputeFee" in settings_data and settings_data["disputeFee"] is not None:
            try:
                settings.dispute_fee_pence = int(float(settings_data["disputeFee"]) * 100)
            except (ValueError, TypeError):
                pass  # Optional field, skip if invalid
        if "refundProcessingFee" in settings_data and settings_data["refundProcessingFee"] is not None:
            try:
                settings.refund_processing_fee_pence = int(float(settings_data["refundProcessingFee"]) * 100)
            except (ValueError, TypeError):
                pass  # Optional field, skip if invalid
        if "lateFee" in settings_data and settings_data["lateFee"] is not None:
            try:
                settings.late_fee_pence = int(float(settings_data["lateFee"]) * 100)
            except (ValueError, TypeError) as e:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid lateFee value: {str(e)}"
                )
        
        settings.last_updated = datetime.utcnow()
        
        db.add(settings)
        db.commit()
        db.refresh(settings)
        
        return {
            "pricePerHousehold": settings.price_per_household_pence / 100,
            "platformFeePercentage": settings.platform_fee_percentage,
            "platformFee": settings.platform_fee_pence / 100,
            "processingFee": settings.processing_fee_pence / 100,
            "cancellationFee": settings.cancellation_fee_pence / 100,
            "disputeFee": settings.dispute_fee_pence / 100,
            "refundProcessingFee": settings.refund_processing_fee_pence / 100,
            "lateFee": settings.late_fee_pence / 100,
            "lastUpdated": settings.last_updated.isoformat()
        }
    except ValueError as e:
        logger.error(f"Invalid value in cost settings update: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid value provided: {str(e)}"
        )
    except Exception as e:
        logger.error(f"Error updating cost settings: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update cost settings: {str(e)}"
        )


# ====================================================================
# System Settings Endpoints
# ====================================================================

@router.get(
    "/system-settings",
    summary="Get system settings",
    description="Fetch platform system settings"
)
async def get_system_settings(
    current_user: User = Depends(require_admin_role()),
    db: Session = Depends(get_session)
):
    """Get system settings."""
    try:
        settings = db.exec(select(SystemSettings).limit(1)).first()
        
        if not settings:
            settings = SystemSettings()
            db.add(settings)
            db.commit()
            db.refresh(settings)
        
        return {
            "autoAssign": settings.auto_assign_enabled,
            "broadcastEnabled": settings.broadcast_enabled,
            "maintenanceMode": settings.maintenance_mode,
            "requireCheckins": settings.require_checkins,
            "emailAlerts": settings.email_alerts,
            "smsAlerts": settings.sms_alerts,
            "pushNotifications": settings.push_notifications,
            "lastUpdated": settings.last_updated.isoformat()
        }
    except Exception as e:
        logger.error(f"Error fetching system settings: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch system settings: {str(e)}"
        )


@router.put(
    "/system-settings",
    summary="Update system settings",
    description="Update platform system settings"
)
async def update_system_settings(
    settings_data: Dict[str, Any] = Body(...),
    current_user: User = Depends(require_admin_role()),
    db: Session = Depends(get_session)
):
    """Update system settings."""
    try:
        settings = db.exec(select(SystemSettings).limit(1)).first()
        
        if not settings:
            settings = SystemSettings()
            db.add(settings)
        
        if "autoAssign" in settings_data:
            settings.auto_assign_enabled = settings_data["autoAssign"]
        if "broadcastEnabled" in settings_data:
            settings.broadcast_enabled = settings_data["broadcastEnabled"]
        if "maintenanceMode" in settings_data:
            settings.maintenance_mode = settings_data["maintenanceMode"]
        if "requireCheckins" in settings_data:
            settings.require_checkins = settings_data["requireCheckins"]
        if "emailAlerts" in settings_data:
            settings.email_alerts = settings_data["emailAlerts"]
        if "smsAlerts" in settings_data:
            settings.sms_alerts = settings_data["smsAlerts"]
        if "pushNotifications" in settings_data:
            settings.push_notifications = settings_data["pushNotifications"]
        
        settings.last_updated = datetime.utcnow()
        
        db.add(settings)
        db.commit()
        db.refresh(settings)
        
        return {
            "autoAssign": settings.auto_assign_enabled,
            "broadcastEnabled": settings.broadcast_enabled,
            "maintenanceMode": settings.maintenance_mode,
            "requireCheckins": settings.require_checkins,
            "emailAlerts": settings.email_alerts,
            "smsAlerts": settings.sms_alerts,
            "pushNotifications": settings.push_notifications,
            "lastUpdated": settings.last_updated.isoformat()
        }
    except Exception as e:
        logger.error(f"Error updating system settings: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update system settings: {str(e)}"
        )
