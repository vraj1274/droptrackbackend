"""
Amazon Cognito JWT validation service for DropTrack platform.
Handles JWT token validation, JWKS fetching, and user claim extraction.
"""

import json
import logging
from typing import Dict, Optional, Any
from datetime import datetime, timedelta
import httpx
from jose import jwt, JWTError
from jose.exceptions import JWKError
from app.config import settings

logger = logging.getLogger(__name__)


class CognitoJWTError(Exception):
    """Custom exception for Cognito JWT validation errors."""


class CognitoService:
    """
    Service for validating Amazon Cognito JWT tokens.
    Implements JWKS caching and signature verification.
    """

    def __init__(self):
        self.jwks_cache: Optional[Dict[str, Any]] = None
        self.jwks_cache_expiry: Optional[datetime] = None
        self.cache_duration = timedelta(hours=1)  # Cache JWKS for 1 hour

    async def get_jwks(self) -> Dict[str, Any]:
        """
        Fetch JWKS from Cognito endpoint with caching.

        Returns:
            Dict containing JWKS data

        Raises:
            CognitoJWTError: If JWKS cannot be fetched
        """
        # Check if cache is valid
        if (self.jwks_cache and self.jwks_cache_expiry and
                datetime.utcnow() < self.jwks_cache_expiry):
            return self.jwks_cache

        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    settings.cognito_jwks_url,
                    timeout=10.0
                )
                response.raise_for_status()

                jwks_data = response.json()

                # Cache the JWKS data
                self.jwks_cache = jwks_data
                self.jwks_cache_expiry = datetime.utcnow() + self.cache_duration

                return jwks_data

        except httpx.RequestError as e:
            raise CognitoJWTError(f"Failed to fetch JWKS: {str(e)}") from e
        except httpx.HTTPStatusError as e:
            # Suppress 404 errors - Cognito pool doesn't exist yet (development mode)
            if e.response.status_code == 404:
                # Don't log - expected when Cognito isn't configured
                pass
            raise CognitoJWTError(
                f"JWKS endpoint returned error: {e.response.status_code}"
            ) from e
        except json.JSONDecodeError as e:
            raise CognitoJWTError(
                f"Invalid JSON in JWKS response: {str(e)}"
            ) from e
        except Exception as e:
            raise CognitoJWTError(f"Failed to fetch JWKS: {str(e)}") from e

    def get_signing_key(self, jwks: Dict[str, Any], kid: str) -> Dict[str, Any]:
        """
        Extract the signing key from JWKS for the given key ID.

        Args:
            jwks: JWKS data from Cognito
            kid: Key ID from JWT header

        Returns:
            Dict containing the signing key

        Raises:
            CognitoJWTError: If signing key cannot be found
        """
        keys = jwks.get("keys", [])

        for key in keys:
            if key.get("kid") == kid:
                return key

        raise CognitoJWTError(f"Unable to find signing key with kid: {kid}")

    async def validate_token(self, token: str) -> Dict[str, Any]:
        """
        Validate JWT token from Amazon Cognito.

        Args:
            token: JWT token string

        Returns:
            Dict containing validated token claims

        Raises:
            CognitoJWTError: If token validation fails
        """
        try:
            # Decode token header to get key ID
            unverified_header = jwt.get_unverified_header(token)
            kid = unverified_header.get("kid")

            if not kid:
                raise CognitoJWTError("Token header missing 'kid' field")

            # Get JWKS and find signing key
            jwks = await self.get_jwks()
            signing_key = self.get_signing_key(jwks, kid)

            # SECURITY FIX 6: Always verify audience and issuer (removed debug bypass)
            # Production safety check: Fail if DEBUG is enabled in production
            import os
            env = os.getenv("ENVIRONMENT", "development").lower()
            if env == "production" and settings.debug:
                raise CognitoJWTError(
                    "CRITICAL SECURITY ERROR: DEBUG mode is enabled in production! "
                    "JWT validation is compromised. Set DEBUG=false immediately."
                )
            
            # Validate token signature and claims
            # SECURITY FIX 6: Always verify audience and issuer regardless of debug mode
            verify_options = {
                "verify_signature": True,
                "verify_aud": True,   # FIXED: Always verify audience
                "verify_iss": True,   # FIXED: Always verify issuer
                "verify_exp": True,   # Always verify expiration
                "verify_nbf": False,  # Don't verify nbf (not before)
                "verify_iat": False,  # Don't verify iat (issued at) strictly
            }

            # Build issuer URL (always required now)
            issuer_url = (
                f"https://cognito-idp.{settings.cognito_region}."
                f"amazonaws.com/{settings.cognito_user_pool_id}"
            )

            try:
                # SECURITY FIX 6: Always pass audience and issuer (no debug bypass)
                claims = jwt.decode(
                    token,
                    signing_key,
                    algorithms=["RS256"],
                    audience=settings.cognito_app_client_id,  # FIXED: Always verify
                    issuer=issuer_url,                        # FIXED: Always verify
                    options=verify_options
                )
            except JWTError as e:
                error_str = str(e).lower()

                # SECURITY FIX 6: Removed debug bypass for audience/issuer validation
                # Check for specific error types
                if "expired" in error_str or "expiration" in error_str:
                    raise CognitoJWTError("Token has expired") from e
                elif "audience" in error_str or "aud" in error_str:
                    # FIXED: No debug bypass - always fail on audience mismatch
                    raise CognitoJWTError("Invalid token audience") from e
                elif "issuer" in error_str or "iss" in error_str:
                    # FIXED: No debug bypass - always fail on issuer mismatch
                    raise CognitoJWTError("Invalid token issuer") from e
                else:
                    # Re-raise other JWT errors
                    raise CognitoJWTError(f"JWT validation failed: {str(e)}") from e

            # Validate token type (accept access or id tokens)
            token_use = claims.get("token_use")
            
            # [HARD DEBUG] Log raw token data
            logger.info(
                "🔍 [JWT DEBUG] Token Validated:\n"
                "   Token Use: %s\n"
                "   Claims Keys: %s\n"
                "   Custom Role: %s\n"
                "   User Role: %s",
                token_use,
                list(claims.keys()),
                claims.get("custom:role", "MISSING"),
                claims.get("custom:user_role", "MISSING")
            )

            # SECURITY FIX 6: Removed debug bypass for token_use validation
            if token_use not in ("access", "id"):
                raise CognitoJWTError(
                    f"Invalid token type: {token_use}. "
                    f"Expected 'access' or 'id'"
                )
            
            # WARNING: Access tokens commonly miss custom attributes!
            if token_use == "access" and not claims.get("custom:role"):
                 logger.warning(
                     "⚠️  [JWT WARNING] Using Access Token for authentication, but 'custom:role' is missing. "
                     "User role assignment may FAIL. Frontend should use ID Token."
                 )

            return claims

        except JWTError as e:
            raise CognitoJWTError(f"JWT validation failed: {str(e)}") from e
        except JWKError as e:
            raise CognitoJWTError(f"JWK error: {str(e)}") from e
        except Exception as e:
            raise CognitoJWTError(
                f"Unexpected error during token validation: {str(e)}"
            ) from e

    def extract_user_claims(self, claims: Dict[str, Any]) -> Dict[str, Any]:
        """
        Extract user information from validated JWT claims.
        
        RELAXED MODE (for Lazy Sync):
        - Allows missing 'custom:role' (will be defaulted in user_service)
        - Logs warning if role is missing
        - Returns claims with role=None if missing (user_service will handle)

        Args:
            claims: Validated JWT claims

        Returns:
            Dict containing user information, role may be None if missing

        Raises:
            CognitoJWTError: If required claims (sub, email) are missing
        """
        try:
            # Extract required claims
            cognito_sub = claims.get("sub")
            if not cognito_sub:
                raise CognitoJWTError("Missing 'sub' claim in token")

            email = claims.get("email")
            if not email:
                raise CognitoJWTError("Missing 'email' claim in token")

            # Normalize email
            email_normalized = email.strip().lower()

            # Name is optional - use email prefix if not available
            name = claims.get("name") or claims.get("username") or email_normalized.split("@")[0]

            # ---------------------------------------------------------
            # ROLE EXTRACTION (RELAXED for Lazy Sync)
            # ---------------------------------------------------------
            # Priority 1: custom:role (New Standard)
            user_role = claims.get("custom:role")
            
            # Priority 2: custom:user_role (Legacy Fallback)
            if not user_role:
                user_role = claims.get("custom:user_role")

            # Check if role is missing - LOG WARNING but don't fail
            if not user_role:
                # Log detailed error for debugging
                available_claims = list(claims.keys())
                custom_claims = [k for k in available_claims if k.startswith("custom:")]
                
                logger.warning(
                    "⚠️  [LAZY_SYNC] No role found in JWT token for %s. "
                    "Available claims: %s, Custom claims: %s. "
                    "User service will apply default role and patch Cognito.",
                    email_normalized, available_claims, custom_claims
                )
                # Return None for role - user_service will handle defaulting
                user_role = None
            else:
                # Normalize role value
                if user_role == "superadmin":
                    user_role = "admin"

                # Validate against allowlist
                valid_roles = ["client", "dropper", "admin"]
                if user_role not in valid_roles:
                    logger.warning(
                        "⚠️  Invalid user role: %s. Must be one of %s. "
                        "User service will apply default role.",
                        user_role, valid_roles
                    )
                    user_role = None  # Let user_service handle
                else:
                    # Log successful role extraction
                    if settings.debug:
                        logger.info(
                            "✅ Role extracted: %s for %s",
                            user_role, email_normalized
                        )

            return {
                "cognito_sub": cognito_sub,
                "email": email_normalized,
                "name": name,
                "role": user_role,        # May be None
                "custom:role": user_role, # May be None
                "custom:user_role": user_role, # May be None
                "token_issued_at": datetime.fromtimestamp(claims.get("iat", 0)),
                "token_expires_at": datetime.fromtimestamp(claims.get("exp", 0)),
                "client_id": claims.get("client_id"),
                "username": claims.get("username"),
            }

        except KeyError as e:
            raise CognitoJWTError(f"Missing required claim: {str(e)}") from e
        except Exception as e:
             if isinstance(e, CognitoJWTError):
                 raise
             raise CognitoJWTError(
                f"Error extracting user claims: {str(e)}"
            ) from e

    async def validate_and_extract_user(self, token: str) -> Dict[str, Any]:
        """
        Validate JWT token and extract user information in one step.

        Args:
            token: JWT token string

        Returns:
            Dict containing user information from validated token

        Raises:
            CognitoJWTError: If token validation or claim extraction fails
        """
        claims = await self.validate_token(token)
        return self.extract_user_claims(claims)

    def clear_cache(self):
        """Clear the JWKS cache. Useful for testing or forced refresh."""
        self.jwks_cache = None
        self.jwks_cache_expiry = None


# Global service instance
cognito_service = CognitoService()

