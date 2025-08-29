"""
Модель ответа RAG системы
"""

from pydantic import BaseModel, Field


class SimpleRAGResponse(BaseModel):
    """Упрощенная модель ответа RAG системы с встроенными ссылками"""
    answer: str = Field(..., description="Сгенерированный ответ со встроенными ссылками")
