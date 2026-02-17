"""
API endpoints for file uploads (S3 presigned URLs)
"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from typing import Optional
import boto3
from botocore.exceptions import ClientError
import uuid
from datetime import datetime

from app.models import User
from app.api.deps import require_client_role, get_current_user
from app.config import settings

router = APIRouter()


# Initialize S3 client
s3_client = boto3.client(
    's3',
    region_name=settings.aws_region,
    aws_access_key_id=settings.aws_access_key_id,
    aws_secret_access_key=settings.aws_secret_access_key
)


class PresignedUrlRequest(BaseModel):
    """Request schema for presigned URL generation"""
    filename: str
    content_type: str


class PresignedUrlResponse(BaseModel):
    """Response schema containing presigned URL and final S3 URL"""
    upload_url: str
    file_url: str
    expires_in: int = 300  # 5 minutes


@router.post(
    "/presigned-url",
    response_model=PresignedUrlResponse,
    summary="Get presigned URL for S3 upload",
    description="Generate a presigned URL for uploading images directly to S3"
)
async def get_presigned_upload_url(
    request: PresignedUrlRequest,
    current_user: User = Depends(get_current_user),
):
    """
    Generate a presigned URL for uploading files to S3.
    
    The presigned URL allows the frontend to upload files directly to S3
    without going through the backend, improving performance and scalability.
    """
    try:
        # Generate unique filename with UUID to avoid conflicts
        file_extension = request.filename.split('.')[-1] if '.' in request.filename else ''
        unique_filename = f"leaflets/{current_user.id}/{uuid.uuid4()}.{file_extension}"
        
        # Generate presigned URL for PUT upload
        presigned_url = s3_client.generate_presigned_url(
            'put_object',
            Params={
                'Bucket': settings.s3_leaflet_bucket,
                'Key': unique_filename,
                'ContentType': request.content_type,
            },
            ExpiresIn=300  # 5 minutes
        )
        
        # Construct the final S3 URL where the file will be accessible
        file_url = f"https://{settings.s3_leaflet_bucket}.s3.{settings.aws_region}.amazonaws.com/{unique_filename}"
        
        return PresignedUrlResponse(
            upload_url=presigned_url,
            file_url=file_url,
            expires_in=300
        )
        
    except ClientError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate presigned URL: {str(e)}"
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Unexpected error: {str(e)}"
        )
