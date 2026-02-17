"""
Database configuration and session management for DropTrack.
Uses SQLModel with PostgreSQL and PostGIS support.
"""

from sqlmodel import SQLModel, create_engine, Session
from typing import Generator
import logging

from app.config import settings

# Import all models to ensure they're registered with SQLModel metadata
# This ensures all tables are created when create_db_and_tables() is called
from app.models import (
    User, Client, Dropper, DropJob, JobArea, JobAssignment,
    Transaction, PaymentMethod, DropPoint, DropZone, DroperLocation
)

logger = logging.getLogger(__name__)

# Database engine configuration for PostgreSQL
# SECURITY FIX 3: Reduced database pooling to safe production limits
engine_kwargs = {
    "pool_pre_ping": True,      # Validate connections before use
    "pool_size": 10,            # Reduced from 20 to 10 for production safety
    "max_overflow": 20,         # Reduced from 40 to 20 to prevent connection exhaustion
    "pool_timeout": 30,         # Added: Timeout for getting connection from pool (30 seconds)
    "pool_recycle": 1800,       # Recycle connections every 30 minutes
    "connect_args": {
        "connect_timeout": 5,   # Fail fast if DB down
        "options": "-c statement_timeout=30000"  # Added: 30 second query timeout
    },
}

# Determine if we are connecting to a remote database (Production/EC2)
is_remote_db = (
    "localhost" not in settings.database_url and 
    "127.0.0.1" not in settings.database_url and
    settings.database_url != ""
)

if is_remote_db or settings.environment.lower() == "production":
    # Production / Remote DB Settings
    engine_kwargs["echo"] = False
    # Ensure SSL is used for remote connections (EC2/RDS)
    # 'prefer' allows fallback if server doesn't support SSL, 
    # 'require' would be stricter but might block if certs aren't perfect yet.
    engine_kwargs.setdefault("connect_args", {}).update({"sslmode": "prefer"})
    logger.info("🔧 Configuring database engine for REMOTE/PRODUCTION mode (SSL: prefer)")
else:
    # Local Development Settings
    engine_kwargs["echo"] = settings.debug # Log SQL only if debug is on
    logger.info("🔧 Configuring database engine for LOCAL/DEV mode")

# Validate database URL is PostgreSQL
if not settings.database_url.startswith("postgresql"):
    raise ValueError(
        f"Invalid database URL. PostgreSQL required. Got: {settings.database_url}\n"
        "Expected format:postgresql://dropverify:~p)F33uC6P=4@127.0.0.1:5432/droptrackpwa"
    )

# Create database engine
engine = create_engine(settings.database_url, **engine_kwargs)


def create_db_and_tables():
    """
    Create database tables based on SQLModel metadata.
    Should be called during application startup.
    In production, this is handled by Alembic migrations.
    """
    import os
    if os.getenv("ENVIRONMENT") == "production":
        logger.info("ℹ️  Skipping create_all() in production. Schema is managed by Alembic.")
        return
        
    try:
        SQLModel.metadata.create_all(engine)
        # Suppressed: logger.info("Database tables created successfully")
    except Exception as e:
        logger.error(f"Error creating database tables: {e}")
        raise


def get_session() -> Generator[Session, None, None]:
    """
    Database session dependency for FastAPI.
    Provides a database session that automatically closes after use.
    Ensures proper commit of transactions.
    
    IMPORTANT: Services should call session.commit() explicitly.
    This function will NOT auto-commit to avoid double commits.
    Only commits if services don't explicitly commit.
    
    Usage in FastAPI endpoints:
        def endpoint(session: Session = Depends(get_session)):
            # Use session for database operations
            # Services should call session.commit() explicitly
    """
    session = Session(engine, autocommit=False, expire_on_commit=False)
    committed = False
    try:
        yield session
        # Only auto-commit if session is still active and has pending changes
        # Services should call commit() explicitly, but we'll commit if they didn't
        if session.is_active:
            try:
                # Check for actual changes that need committing
                has_new = len(session.new) > 0
                has_deleted = len(session.deleted) > 0
                has_dirty = len(session.dirty) > 0
                
                # Only auto-commit if there are real changes
                # Suppress debug logs for dirty-only changes (they're often from SQLModel relationship loading)
                if has_new or has_deleted:
                    # Only log if there are actual new/deleted records (not just dirty)
                    session.commit()
                    committed = True
                elif has_dirty:
                    # Dirty changes are often from relationship loading, commit silently
                    session.commit()
                    committed = True
            except Exception as commit_error:
                # If commit fails, rollback and re-raise
                logger.error(f"Failed to commit database changes: {commit_error}")
                session.rollback()
                raise
    except Exception as e:
        # Only log actual database errors, not authentication/authorization errors
        error_str = str(e)
        if ("Token validation" not in error_str and 
            "JWKS" not in error_str and
            "Access denied" not in error_str and
            "Required roles" not in error_str):
            logger.error(f"Database session error: {e}")
        if session.is_active:
            try:
                session.rollback()
            except Exception as rollback_error:
                logger.error(f"Failed to rollback transaction: {rollback_error}")
        raise
    finally:
        session.close()


def init_db():
    """
    Initialize database with PostGIS extension and create tables.
    This function should be called during application startup.
    """
    try:
        # First, verify we can connect to the database
        with Session(engine) as session:
            # Simple connection test
            from sqlalchemy import text
            session.exec(text("SELECT 1"))
            # Suppressed: logger.info("Database connection verified")
        
        # PostGIS extension check - silently skip (optional for geospatial operations)
        # Users can install PostGIS later if needed for geospatial features
        # Commented out to keep output clean:
        # if settings.database_url.startswith("postgresql"):
        #     try:
        #         with Session(engine) as session:
        #             result = session.exec(text("SELECT PostGIS_Version();"))
        #             version = result.first()
        #             logger.info(f"PostGIS extension is available: {version}")
        #     except Exception:
        #         pass  # PostGIS is optional
        
        # Create all tables
        create_db_and_tables()
        # Suppressed: logger.info("Database schema initialized successfully")
        
    except Exception as e:
        logger.error(f"Database initialization failed: {e}")
        # Provide helpful error message
        if "password authentication failed" in str(e):
            logger.error("❌ PostgreSQL authentication failed!")
            logger.error(f"   Check DATABASE_URL in .env file")
            logger.error(f"   Format: postgresql://username:password@host:port/database")
            logger.error(f"   Current: {settings.database_url.split('@')[0] if '@' in settings.database_url else 'configured'}@...")
        elif "connection to server" in str(e).lower():
            logger.error("❌ Cannot connect to PostgreSQL server!")
            logger.error("   Ensure PostgreSQL is running:")
            logger.error("   Windows: Check services or run 'net start postgresql-x64-XX'")
            logger.error("   Linux/Mac: sudo systemctl start postgresql")
        raise