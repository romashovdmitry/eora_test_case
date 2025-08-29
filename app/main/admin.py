"""
This module is used to register models in the admin panel using SQLAdmin.
"""
# Python imports
import re

# FastAPI imports
from starlette.exceptions import HTTPException
from starlette.exceptions import HTTPException
from starlette.requests import Request

# sqlalchemy, sqladmin imports
from sqladmin import Admin, ModelView

# Third-party imports
from markupsafe import Markup

# import models
from main.models import LogEntry
from parser.models.source_content import SourceContent
from parser.models.source_urls import SourceURL


def configure_admin(admin: Admin):

    class LogsAdmin(ModelView, model=LogEntry):
        column_list = [
            LogEntry.level,
            LogEntry.timestamp,
            LogEntry.message,
        ]
        column_default_sort = ('timestamp', True)
        category = "Logs"
        name = "Логи"
        name_plural = "Логи"

    class SourceURLAdmin(ModelView, model=SourceURL):
        column_list = [
            SourceURL.id,
            SourceURL.title,
            SourceURL.url,
            SourceURL.description,
            SourceURL.created_at,
            SourceURL.updated_at,
        ]
        column_details_list = [
            SourceURL.id,
            SourceURL.title,
            SourceURL.url,
            SourceURL.description,
            SourceURL.created_at,
            SourceURL.updated_at,
        ]
        column_searchable_list = [
            SourceURL.title,
            SourceURL.url,
            SourceURL.description,
        ]
        column_sortable_list = [
            SourceURL.id,
            SourceURL.title,
            SourceURL.created_at,
            SourceURL.updated_at,
        ]
        column_default_sort = ('created_at', True)  # Сортировка по дате создания (новые сверху)
        form_columns = [
            SourceURL.title,
            SourceURL.url,
            SourceURL.description,
        ]
        column_labels = {
            SourceURL.id: "ID",
            SourceURL.title: "Название",
            SourceURL.url: "URL",
            SourceURL.description: "Описание",
            SourceURL.created_at: "Дата создания",
            SourceURL.updated_at: "Дата обновления",
        }        

        category = "Парсер"
        name = "Ссылка"
        name_plural = "Ссылки для парсинга"
        icon = "fa-solid fa-link"
        page_size = 20
        page_size_options = [10, 20, 50, 100]

        def format_url_column(self, request: Request, obj: SourceURL, column: str) -> str:
            """Форматирование отображения URL в виде ссылки"""

            if column == "url" and obj.url:
                display_url = obj.url[:50] + "..." if len(obj.url) > 50 else obj.url

                return Markup(f'<a href="{obj.url}" target="_blank" title="{obj.url}">{display_url}</a>')

            return str(getattr(obj, column, ""))

        async def insert_model(self, request: Request, data: dict) -> None:
            """Валидация при создании новой записи"""
            await self._validate_url(data)

            return await super().insert_model(request, data)

        async def update_model(self, request: Request, pk: str, data: dict) -> None:
            """Валидация при обновлении записи"""
            await self._validate_url(data)

            return await super().update_model(request, pk, data)

        async def _validate_url(self, data: dict) -> None:
            """Валидация URL"""
            url = data.get('url', '')

            if url:
                # Простая проверка URL
                url_pattern = r'^https?:\/\/(?:[-\w.])+(?:\:[0-9]+)?(?:\/(?:[\w\/_.])*(?:\?(?:[\w&=%.])*)?(?:\#(?:[\w.])*)?)?$'

                if not re.match(url_pattern, url):

                    raise HTTPException(status_code=400, detail="Некорректный URL. URL должен начинаться с http:// или https://")

                if len(url) > 2048:

                    raise HTTPException(status_code=400, detail="URL слишком длинный (максимум 2048 символов)")

    class SourceContentAdmin(ModelView, model=SourceContent):
        column_list = [
            SourceContent.id,
            SourceContent.source_url_id,
            SourceContent.parsed_at,
        ]
        column_details_list = [
            SourceContent.id,
            SourceContent.source_url_id,
            SourceContent.content,
            SourceContent.parsed_at,
        ]
        column_searchable_list = [
            SourceContent.content,
        ]
        column_sortable_list = [
            SourceContent.id,
            SourceContent.source_url_id,
            SourceContent.parsed_at,
        ]
        column_default_sort = ('parsed_at', True)  # Сортировка по дате парсинга (новые сверху)
        form_columns = [
            SourceContent.source_url_id,
            SourceContent.content,
        ]
        column_labels = {
            SourceContent.id: "ID",
            SourceContent.source_url_id: "ID ссылки",
            SourceContent.content: "Контент",
            SourceContent.parsed_at: "Дата парсинга",
        }

        category = "Парсер"
        name = "Контент"
        name_plural = "Спарсенный контент"
        icon = "fa-solid fa-file-text"
        page_size = 20
        page_size_options = [10, 20, 50, 100]

        def format_content_column(self, request: Request, obj: SourceContent, column: str) -> str:
            """Форматирование отображения контента (превью)"""
            if column == "content" and obj.content:
                # Показываем только первые 100 символов
                preview = obj.content[:100] + "..." if len(obj.content) > 100 else obj.content

                return Markup(f'<div title="{len(obj.content)} символов">{preview}</div>')

            return str(getattr(obj, column, ""))

    admin.add_view(LogsAdmin)
    admin.add_view(SourceURLAdmin)
    admin.add_view(SourceContentAdmin)
