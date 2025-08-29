# Python imports
import asyncio
import logging

# Local imports
from main.database_connection import AsyncSessionLocal
from main.models import LogEntry


class DBLogHandler(logging.Handler):

    def __init__(self, db_session_factory):
        super().__init__()
        self.db_session_factory = db_session_factory

    def emit(self, record):
        asyncio.create_task(self._async_emit(record))

    async def _async_emit(self, record):
        message = self.format(record)
        log_entry = LogEntry(
            level=record.levelname,
            message=message,
        )

        async with self.db_session_factory() as session:

            try:
                session.add(log_entry)
                await session.commit()

            except Exception as e:
                print(f"Ошибка записи логов в БД: {e}", flush=True)


def setup_logger():
    logger = logging.getLogger("app_logger")
    logger.setLevel(logging.DEBUG)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.DEBUG)
    console_formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    console_handler.setFormatter(console_formatter)

    db_handler = DBLogHandler(AsyncSessionLocal)
    db_handler.setLevel(logging.INFO)
    db_formatter = logging.Formatter("%(levelname)s: %(message)s")
    db_handler.setFormatter(db_formatter)

    logger.addHandler(console_handler)
    logger.addHandler(db_handler)

    # Отключаем подробные HTTP логи от httpx
    httpx_logger = logging.getLogger("httpx")
    httpx_logger.setLevel(logging.WARNING)

    return logger


logger = setup_logger()
