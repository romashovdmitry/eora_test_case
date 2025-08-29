"""
Модель для запроса поиска
"""

from pydantic import BaseModel, Field


class SearchQuery(BaseModel):
    """Модель для запроса поиска"""
    query: str = Field(..., description="Текст запроса для поиска", min_length=1, max_length=1000)
