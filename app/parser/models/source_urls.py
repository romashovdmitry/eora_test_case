# Python imports
import asyncio
import logging
import re
import threading
from datetime import datetime
from typing import List
import traceback

# sqlalchemy imports
from sqlalchemy import DateTime, Integer, String, Text, event
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates
from sqlalchemy.sql import func
from sqlalchemy.ext.asyncio import AsyncSession

# import models
from parser.models.source_content import SourceContent
from main.base_model import Base

# import custom foos, classes
from main.database_connection import AsyncSessionLocal
from main.logger import logger


class SourceURL(Base):
    """
    Модель для сохранения ссылок, которые необходимо спарсить
    с сайта EORA для получения контекста
    """
    __tablename__ = "source_urls"
    
    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True,
        autoincrement=True,
        comment="Primary key of the source URL"
    )
    url: Mapped[str] = mapped_column(
        String(2048),
        nullable=False,
        unique=True,
        comment="URL link that needs to be parsed"
    )
    title: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
        comment="Title/name for this URL"
    )
    description: Mapped[str] = mapped_column(
        Text,
        nullable=True,
        comment="Optional description for the URL"
    )
    redirected_url: Mapped[str] = mapped_column(
        String(2048),
        nullable=True,
        comment="Final URL after redirects (if different from original URL)"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        comment="When this record was created"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
        comment="When this record was last updated"
    )
    
    # Связь с таблицей source_content (один ко многим)
    contents: Mapped[List["SourceContent"]] = relationship(
        "SourceContent",
        back_populates="source_url",
        cascade="all, delete-orphan"
    )
    
    @validates('url')
    def validate_url(self, key, url):
        """Валидация URL при сохранении в базу данных"""
        if url:
            # Очищаем URL от непечатаемых символов и лишних пробелов
            original_url = url
            url = url.strip()
            # Удаляем табуляции, переносы строк и другие непечатаемые символы
            url = ''.join(char for char in url if char.isprintable())
            # Удаляем множественные пробелы внутри URL
            url = re.sub(r'\s+', '', url)
            # Проверяем, что строка начинается с http:// или https://
            url_pattern = r'^https?:\/\/(?:[-\w.])+(?:\:[0-9]+)?(?:\/(?:[\w\/_.\-?&=%#])*)?$'

            if not re.match(url_pattern, url):

                raise ValueError(f"Некорректный URL: {url}. URL должен начинаться с http:// или https://")
            
            # Проверяем длину
            if len(url) > 2048:

                raise ValueError(f"URL слишком длинный: {len(url)} символов. Максимум 2048 символов")
        
        return url
    
    def __str__(self) -> str:

        return f"{self.title} ({self.url})"

    def __repr__(self) -> str:

        return f"<SourceURL(id={self.id}, title='{self.title}', url='{self.url}')>"
    

    async def parse_and_save(self, session: AsyncSession):
            """Асинхронно парсит контент и сохраняет в БД."""

            # Импорты для parse_and_save
            from parser.services import URLContentExtractor

            try:

                async with asyncio.timeout(60):  # Таймаут 60 секунд

                    extractor = URLContentExtractor()
                    # Используем redirected_url если он есть, иначе оригинальный url
                    url_to_parse = self.redirected_url if self.redirected_url else self.url
                    result = await extractor.extract_content(url_to_parse)
                    
                    if result and result.get('success'):
                        # Добавляем информацию о редиректе в метаданные
                        metadata = result.get('metadata', {})
                        if self.redirected_url:
                            metadata['original_url'] = self.url
                            metadata['redirected_url'] = self.redirected_url
                            metadata['was_redirected'] = True
                        else:
                            metadata['was_redirected'] = False
                        
                        content_record = SourceContent(
                            source_url_id=self.id,
                            content=result.get('content', ''),
                            title=result.get('title', self.title),
                            description=result.get('description', ''),
                            extraction_metadata=metadata
                        )
                        session.add(content_record)
                        await session.commit()

                    else:
                        # Получаем детальную информацию об ошибке
                        if result:
                            error_msg = result.get('error', 'Unknown error')
                            error_type = result.get('metadata', {}).get('error_type', 'unknown')
                            if not error_msg or error_msg.strip() == '':
                                error_msg = f"Empty error message. Result keys: {list(result.keys())}"
                        else:
                            error_msg = 'No result returned from extractor'
                            error_type = 'unknown'
                        
                        # Различаем типы ошибок для более информативного логирования
                        if error_type == 'network':
                            # Для сетевых ошибок используем более мягкое сообщение
                            logger.warning(f"Network error during parsing: {url_to_parse} | Original: {self.url} | Error: {error_msg}")
                            print(f"   🌐 Сетевая ошибка: {self.title} | {error_msg}", flush=True)
                        else:
                            # Для других ошибок используем обычное логирование
                            full_error_msg = f"Failed parsing: {url_to_parse} | Original: {self.url} | Error: {error_msg}"
                            logger.warning(full_error_msg)
                            print(f"   ❌ Парсинг неудачен: {self.title} | {error_msg}", flush=True)

            except asyncio.TimeoutError:
                url_to_parse = self.redirected_url if self.redirected_url else self.url
                timeout_msg = f"Parsing timeout: {url_to_parse} | Original: {self.url} | Exceeded 60 seconds"
                logger.warning(timeout_msg)
                print(f"   ⏰ Таймаут парсинга: {self.title} | 60 секунд", flush=True)

            except Exception as e:
                url_to_parse = self.redirected_url if self.redirected_url else self.url
                error_details = f"Exception during parsing: {url_to_parse} | Original: {self.url} | {type(e).__name__}: {str(e)}"
                logger.error(error_details)
                print(f"   💥 Ошибка парсинга: {self.title} | {type(e).__name__}: {str(e)}", flush=True)
                await session.rollback()



@event.listens_for(SourceURL, 'after_insert')
def source_url_after_insert(mapper, connection, target):
    """Автоматически запускает парсинг после добавления URL."""

    async def run_parse():
        async with AsyncSessionLocal() as session:
            await target.parse_and_save(session)
    
    # Запускаем задачу в текущем event loop (FastAPI)
    asyncio.create_task(run_parse())





# # Event listener для отслеживания создания новых объектов
# @event.listens_for(SourceURL, 'after_insert')
# def source_url_after_insert(mapper, connection, target):
#     """
#     Событие после добавления нового SourceURL в базу данных
#     Автоматически запускает парсинг контента с добавленного URL
    
#     Args:
#         mapper: SQLAlchemy mapper
#         connection: Database connection
#         target: Созданный объект SourceURL
#     """
    
#     def run_async_tasks():
#         """Запускает асинхронные задачи в отдельном потоке"""

#         try:            
#             # Сохраняем данные объекта до создания нового event loop
#             url_data = {
#                 'id': target.id,
#                 'url': target.url, 
#                 'title': target.title
#             }
            
#             # Создаем новый event loop для этого потока
#             loop = asyncio.new_event_loop()
#             asyncio.set_event_loop(loop)

#             loop.run_until_complete(target._parse_content_with_data(url_data))
            
#             print(f"✅ ВСЕ ФОНОВЫЕ ЗАДАЧИ ЗАВЕРШЕНЫ УСПЕШНО!")
            
#         except Exception as e:
#             print(f"❌ КРИТИЧЕСКАЯ ОШИБКА в фоновых задачах: {e}")
#             traceback.print_exc()

#         finally:
#             loop.close()

#     # Запускаем асинхронные задачи в отдельном потоке
#     # чтобы не блокировать основную операцию с БД
#     thread = threading.Thread(target=run_async_tasks, daemon=True)
#     thread.start()
