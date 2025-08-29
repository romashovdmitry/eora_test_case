"""
Running server-database connection.
https://fastapi.tiangolo.com/tutorial/sql-databases/
"""
# SQLAlchemy imports
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from typing import AsyncGenerator

# Local imports
from main.constants import (
    DB_NAME,
    POSTGRES_DB_HOST,
    POSTGRES_DB_PORT,
    POSTGRES_PASSWORD,
    POSTGRES_USER
)

DATABASE_URL = (
    f"postgresql+asyncpg://{POSTGRES_USER}:{POSTGRES_PASSWORD}@{POSTGRES_DB_HOST}:{POSTGRES_DB_PORT}/{DB_NAME}"
)
engine = create_async_engine(DATABASE_URL)

AsyncSessionLocal = sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False
)

async def get_db() -> AsyncGenerator[AsyncSession, None]:

    async with AsyncSessionLocal() as session:

        yield session

# используется в тестах
async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session
