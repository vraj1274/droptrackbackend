import asyncio
import os
import logging
from app.main import app
from app.config import settings

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("worker")

async def run_worker():
    """
    Dedicated worker process to run the APScheduler.
    This replaces running the scheduler inside Gunicorn workers to avoid duplicates.
    """
    logger.info("🚀 Starting dedicated background worker...")
    
    # Imports inside function to avoid circular imports if any, though unlikely here
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        from app.tasks.cleanup import run_cleanup_job
    except ImportError:
        logger.error("❌ APScheduler or tasks not found. Worker cannot start.")
        return

    # Initialize scheduler
    scheduler = AsyncIOScheduler()
    
    # We use the FastAPI lifespan to ensure database and other resources are initialized
    async with app.router.lifespan_context(app):
        try:
            # Configure jobs
            # Run cleanup daily at 2 AM UTC
            scheduler.add_job(
                run_cleanup_job,
                trigger="cron",
                hour=2,
                minute=0,
                id="cleanup_pending_users",
                replace_existing=True
            )
            
            scheduler.start()
            logger.info("✅ Background cleanup scheduler started (daily at 2 AM UTC)")
            
            logger.info("✅ Background worker is active and scheduler is running.")
            
            # Keep the loop alive
            while True:
                await asyncio.sleep(3600)
                
        except (KeyboardInterrupt, SystemExit):
            logger.info("🛑 Background worker shutting down...")
            if scheduler.running:
                scheduler.shutdown()
        except Exception as e:
            logger.error(f"❌ Worker error: {e}")
            if scheduler.running:
                scheduler.shutdown()

if __name__ == "__main__":
    try:
        asyncio.run(run_worker())
    except Exception as e:
        logger.error(f"❌ Worker failed: {e}")
