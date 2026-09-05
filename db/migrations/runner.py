import asyncio
from db.database import engine, Base
from db.models.schema import *
from packages.observability.logger import get_logger

logger = get_logger("migrations")

async def run_migrations():
    logger.info("Running database migrations...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database migrations completed successfully.")

if __name__ == "__main__":
    asyncio.run(run_migrations())
