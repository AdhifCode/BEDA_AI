import asyncio
from dotenv import load_dotenv

load_dotenv()
from db.migrations.runner import run_migrations
from db.seed.seeder import seed_data

async def main():
    await run_migrations()
    await seed_data()

if __name__ == "__main__":
    asyncio.run(main())
