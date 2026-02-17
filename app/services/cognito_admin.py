"""
Cognito Admin Service for managing user attributes
Handles role recovery and attribute patching
"""
import logging
import boto3
from typing import Optional
from app.config import settings

logger = logging.getLogger(__name__)


class CognitoAdminService:
    """Service for Cognito admin operations"""
    
    def __init__(self):
        """Initialize Cognito admin client"""
        self.client = boto3.client(
            'cognito-idp',
            region_name=settings.cognito_region,
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=settings.aws_secret_access_key
        )
        self.user_pool_id = settings.cognito_user_pool_id
    
    def update_user_role(self, cognito_sub: str, role: str) -> bool:
        """
        Update user's custom:role attribute in Cognito
        
        Args:
            cognito_sub: User's Cognito sub (username)
            role: Role to set (client, dropper, admin)
            
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            logger.info(
                f"🔧 [COGNITO_ADMIN] Updating custom:role for user {cognito_sub[:20]}... to '{role}'"
            )
            
            self.client.admin_update_user_attributes(
                UserPoolId=self.user_pool_id,
                Username=cognito_sub,
                UserAttributes=[
                    {
                        'Name': 'custom:role',
                        'Value': role.lower()
                    }
                ]
            )
            
            logger.info(
                f"✅ [COGNITO_ADMIN] Successfully updated custom:role to '{role}' for user {cognito_sub[:20]}..."
            )
            return True
            
        except Exception as e:
            logger.error(
                f"❌ [COGNITO_ADMIN] Failed to update custom:role for user {cognito_sub[:20]}...: {str(e)}"
            )
            return False
    
    def get_user_attributes(self, cognito_sub: str) -> Optional[dict]:
        """
        Get user attributes from Cognito
        
        Args:
            cognito_sub: User's Cognito sub (username)
            
        Returns:
            dict: User attributes or None if failed
        """
        try:
            response = self.client.admin_get_user(
                UserPoolId=self.user_pool_id,
                Username=cognito_sub
            )
            
            # Convert attributes list to dict
            attributes = {}
            for attr in response.get('UserAttributes', []):
                attributes[attr['Name']] = attr['Value']
            
            return attributes
            
        except Exception as e:
            logger.error(
                f"❌ [COGNITO_ADMIN] Failed to get user attributes for {cognito_sub[:20]}...: {str(e)}"
            )
            return None


# Singleton instance
_cognito_admin_service = None


def get_cognito_admin_service() -> CognitoAdminService:
    """Get or create Cognito admin service instance"""
    global _cognito_admin_service
    if _cognito_admin_service is None:
        _cognito_admin_service = CognitoAdminService()
    return _cognito_admin_service
