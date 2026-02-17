"""
Configuration management for DropVerify FastAPI backend.
Handles environment variables and application settings.
"""

from typing import List, Optional
from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings with environment variable support."""
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False
    )
    
    # Environment
    environment: str = "development"
    
    # Database Configuration - MUST be set via environment variable in production
    database_url: str = ""  # Required - no default for production safety
    
    # Amazon Cognito Configuration - MUST be set via environment variable in production
    cognito_region: str = "ap-southeast-2"
    cognito_user_pool_id: str = ""  # Required - no default for production safety
    cognito_app_client_id: str = ""  # Required - no default for production safety
    cognito_client_secret: Optional[str] = None  # Optional - only needed for client secret enabled apps
    cognito_jwks_url: Optional[str] = None
    
    # Stripe Configuration - MUST be set via environment variable in production
    stripe_secret_key: str = ""  # Required - no default for production safety
    stripe_webhook_secret: str = ""  # Required - no default for production safety
    stripe_publishable_key: Optional[str] = None
    
    @field_validator("stripe_secret_key")
    @classmethod
    def validate_stripe_secret_key(cls, v: str) -> str:
        """
        Validate Stripe secret key is not a placeholder.
        
        SECURITY FIX 2: Hardened Stripe key validation
        - Enforces format validation (must start with sk_test_ or sk_live_)
        - Enforces minimum length (20 characters)
        - Raises ValueError in production for invalid keys
        """
        import logging
        import os
        logger = logging.getLogger(__name__)
        
        # Check if production environment
        env = os.getenv("ENVIRONMENT", "development").lower()
        is_production = env == "production"
        
        placeholder_values = [
            "sk_test_default",
            "sk_live_default", 
            "sk_test_your_key",
            "sk_live_your_key",
            "sk_test_",
            "sk_live_",
            ""
        ]
        
        # Check for placeholder or empty values
        if v in placeholder_values or not v or v.strip() == "":
            error_msg = (
                "❌ CRITICAL: Stripe secret key is not configured!\n"
                "   Current value: '{}'\n"
                "   Please set STRIPE_SECRET_KEY in your .env file.\n"
                "   Get your key from: https://dashboard.stripe.com/apikeys\n"
                "   For test mode, use a key starting with 'sk_test_'\n"
                "   For production, use a key starting with 'sk_live_'".format(v)
            )
            logger.error(error_msg)
            if is_production:
                raise ValueError("Stripe secret key is not configured for production")
            return v
        
        # Validate format (must start with sk_test_ or sk_live_)
        if not (v.startswith("sk_test_") or v.startswith("sk_live_")):
            error_msg = (
                "⚠️  Stripe secret key format is invalid.\n"
                "   Expected format: 'sk_test_...' or 'sk_live_...'\n"
                "   Current value starts with: '{}'".format(v[:10] if len(v) > 10 else v)
            )
            logger.error(error_msg)
            if is_production:
                raise ValueError("Stripe secret key format is invalid")
            else:
                logger.warning(error_msg)
        
        # Validate minimum length (Stripe keys are typically 32+ characters)
        if len(v) < 20:
            error_msg = (
                "⚠️  Stripe secret key is too short (minimum 20 characters).\n"
                "   Current length: {}".format(len(v))
            )
            logger.error(error_msg)
            if is_production:
                raise ValueError("Stripe secret key is too short")
            else:
                logger.warning(error_msg)
        
        return v
    
    # Application Configuration
    debug: bool = False  # Default to False for production safety
    show_docs: bool = True  # Enable API documentation (Swagger/ReDoc)
    cors_origins: str = ""  # Must be set via environment variable
    api_v1_prefix: str = "/api/v1"
    
    # Security - MUST be set via environment variable in production
    secret_key: str = ""  # Required - no default for production safety
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    
    # Server Configuration
    host: str = "127.0.0.1"
    port: int = 8000
    workers: int = 4
    
    # Superadmin Configuration (comma separated list)
    superadmin_emails: str = "vraj.suthar+admin@thelinetech.uk,info@thelinetech.uk"
    
    # AWS Credentials for Cognito Admin API
    aws_access_key_id: Optional[str] = None
    aws_secret_access_key: Optional[str] = None
    aws_region: str = "ap-southeast-2"
    
    # S3 Configuration for file uploads
    s3_leaflet_bucket: str = "droptrack-leaflets"

    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra='ignore',  # Ignore extra environment variables (like frontend vars)
    )
    
    # Store parsed list for internal use
    _cors_origins_list: List[str] = []
    
    @field_validator("cognito_jwks_url")
    @classmethod
    def build_cognito_jwks_url(cls, v, info):
        """Build JWKS URL from Cognito configuration if not provided."""
        if v is None:
            values = info.data
            region = values.get("cognito_region")
            user_pool_id = values.get("cognito_user_pool_id")
            if region and user_pool_id:
                return f"https://cognito-idp.{region}.amazonaws.com/{user_pool_id}/.well-known/jwks.json"
        return v
    
    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, v):
        """Parse CORS origins from string or list, always return string for storage."""
        # Only use localhost defaults in development
        import os
        env = os.getenv("ENVIRONMENT", "development").lower()
        is_dev = env == "development" or os.getenv("DEBUG", "").lower() == "true"
        
        if v is None or (isinstance(v, str) and not v.strip()):
            if is_dev:
                return "http://localhost:5173,http://localhost:3000"
            return ""  # Empty in production - must be explicitly set
        if isinstance(v, list):
            return ",".join(v)
        if isinstance(v, str):
            # Try to parse as JSON first (for arrays in .env)
            import json
            try:
                parsed = json.loads(v)
                if isinstance(parsed, list):
                    return ",".join(parsed)
            except (json.JSONDecodeError, ValueError):
                pass
            # Return as-is if it's a comma-separated string
            return v
        if is_dev:
            return "http://localhost:5173,http://localhost:3000"
        return ""
    
    @model_validator(mode="after")
    def parse_cors_origins_list(self):
        """Convert cors_origins string to list for internal use."""
        import os
        env = os.getenv("ENVIRONMENT", "development").lower()
        is_dev = env == "development" or os.getenv("DEBUG", "").lower() == "true"
        
        if isinstance(self.cors_origins, str) and self.cors_origins.strip():
            self._cors_origins_list = [
                # Strip whitespace AND trailing slashes (CORS origins must not have trailing slash)
                origin.strip().rstrip('/') 
                for origin in self.cors_origins.split(",") 
                if origin.strip()
            ]
        else:
            self._cors_origins_list = []
        
        # Only add localhost defaults in development
        if not self._cors_origins_list and is_dev:
            self._cors_origins_list = [
                "http://localhost:5173",
                "http://localhost:3000",
            ]
        return self
    
    @property
    def cors_origins_list(self) -> List[str]:
        """Get CORS origins as a list."""
        return self._cors_origins_list
    
    @property
    def superadmin_emails_list(self) -> List[str]:
        """Get superadmin emails as a list."""
        if isinstance(self.superadmin_emails, str):
            return [email.strip().lower() for email in self.superadmin_emails.split(",") if email.strip()]
        return []
    
    def validate_production_settings(self) -> None:
        """Validate that production settings are secure. Raises ValueError if critical issues found."""
        import os
        import logging
        logger = logging.getLogger(__name__)
        
        env_var = os.getenv("ENVIRONMENT", "development").lower()
        
        # If explicitly set to development, force non-production mode
        if env_var == "development":
            is_production = False
        else:
            # Otherwise, auto-detect based on env var OR database URL (remote DB implies production)
            is_production = (
                env_var == "production" or
                (not any(origin in str(self.database_url) for origin in ["localhost", "127.0.0.1"]) 
                 and self.database_url and "localhost" not in self.database_url)
            )
        
        errors = []
        warnings = []
        
        if is_production:
            # CRITICAL: Debug mode must be disabled in production
            if self.debug:
                errors.append("DEBUG must be False in production. Set DEBUG=false in environment variables.")
            
            # CRITICAL: Secret key must be changed
            if not self.secret_key or self.secret_key == "your-secret-key-change-in-production":
                errors.append("SECRET_KEY must be set to a strong random value in production.")
            
            # CRITICAL: Database URL must be set
            if not self.database_url:
                errors.append("DATABASE_URL must be set in production.")
            # Skip localhost check as DB is hosted on same instance for this deployment
            # elif any(origin in self.database_url for origin in ["localhost", "127.0.0.1"]):
            #     errors.append("DATABASE_URL must not point to localhost in production.")
            
            # CRITICAL: Cognito configuration must be production-ready
            if not self.cognito_user_pool_id:
                errors.append("COGNITO_USER_POOL_ID must be set in production.")
            elif "test" in self.cognito_user_pool_id.lower() or "_test" in self.cognito_user_pool_id:
                errors.append("COGNITO_USER_POOL_ID must not be a test configuration in production.")
            
            if not self.cognito_app_client_id:
                errors.append("COGNITO_APP_CLIENT_ID must be set in production.")
            elif "test" in self.cognito_app_client_id.lower() or "example" in self.cognito_app_client_id.lower():
                errors.append("COGNITO_APP_CLIENT_ID must not be a test/example value in production.")
            
            # CRITICAL: Stripe keys must be configured
            placeholder_stripe_keys = [
                "sk_test_default",
                "sk_live_default",
                "sk_test_your_key",
                "sk_live_your_key",
                ""
            ]
            if not self.stripe_secret_key or self.stripe_secret_key in placeholder_stripe_keys:
                errors.append("STRIPE_SECRET_KEY must be set to a valid Stripe key in production.")
            
            if not self.stripe_webhook_secret or self.stripe_webhook_secret in ["whsec_test_default", "whsec_your_secret", ""]:
                errors.append("STRIPE_WEBHOOK_SECRET must be set in production.")
            
            # CRITICAL: CORS origins must be configured (no localhost in production)
            if not self.cors_origins or not self.cors_origins.strip():
                errors.append("CORS_ORIGINS must be set in production. Cannot be empty.")
            elif any("localhost" in origin.lower() or "127.0.0.1" in origin.lower() 
                     for origin in self.cors_origins.split(",") if origin.strip()):
                warnings.append("CORS_ORIGINS contains localhost origins. This should be removed in production.")
            
            # WARNINGS (non-blocking but important)
            if self.secret_key and len(self.secret_key) < 32:
                warnings.append("SECRET_KEY should be at least 32 characters long for security.")
        
        else:
            # Development mode validations (warnings only)
            if not self.database_url:
                warnings.append("DATABASE_URL not set. Using default may not work.")
            if not self.secret_key or self.secret_key == "your-secret-key-change-in-production":
                warnings.append("SECRET_KEY not set. Using default is acceptable for development only.")
        
        # Log warnings
        for warning in warnings:
            logger.warning(f"⚠️  WARNING: {warning}")
        
        # Raise errors (fail fast in production)
        if errors:
            error_msg = "❌ CRITICAL PRODUCTION CONFIGURATION ERRORS:\n" + "\n".join(f"   - {e}" for e in errors)
            logger.error(error_msg)
            if is_production:
                raise ValueError(error_msg)

    def validate_cognito_configuration(self) -> None:
        """Validate Cognito configuration is correct. Logs warnings/errors."""
        import logging
        logger = logging.getLogger(__name__)
        
        errors = []
        warnings = []
        
        # Check if required Cognito settings are set
        if not self.cognito_user_pool_id:
            errors.append("COGNITO_USER_POOL_ID is not set")
        elif len(self.cognito_user_pool_id) < 10:
            warnings.append(f"COGNITO_USER_POOL_ID appears invalid: {self.cognito_user_pool_id[:10]}...")
        
        if not self.cognito_app_client_id:
            errors.append("COGNITO_APP_CLIENT_ID is not set")
        elif len(self.cognito_app_client_id) < 10:
            warnings.append(f"COGNITO_APP_CLIENT_ID appears invalid: {self.cognito_app_client_id[:10]}...")
        
        # Check JWKS URL can be constructed
        if not self.cognito_jwks_url:
            if self.cognito_region and self.cognito_user_pool_id:
                expected_jwks = (
                    f"https://cognito-idp.{self.cognito_region}.amazonaws.com/"
                    f"{self.cognito_user_pool_id}/.well-known/jwks.json"
                )
                logger.info("JWKS URL will be: %s", expected_jwks)
            else:
                warnings.append(
                    "Cannot construct JWKS URL - missing region or user pool ID"
                )
        
        # Log warnings
        for warning in warnings:
            logger.warning("⚠️  Cognito Config Warning: %s", warning)
        
        # Log errors (but don't fail in development)
        for error in errors:
            if self.environment.lower() == "production":
                logger.error("❌ CRITICAL Cognito Config Error: %s", error)
            else:
                logger.warning("⚠️  Cognito Config Error: %s", error)


# Global settings instance
settings = Settings()