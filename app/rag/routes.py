"""
API эндпоинты для работы с RAG (Retrieval-Augmented Generation) системой
"""

import asyncio
import logging
import os
import traceback
from typing import Any, Dict, Optional

from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException
from pydantic import BaseModel, Field

from rag.rag_system import RAGSystem, initialize_rag_system, rag_system, _initialization_lock
from rag.schemas import SearchQuery, SimpleRAGResponse

from main.logger import logger

# Создаем роутер для RAG API
rag_router = APIRouter(prefix="/api/v1/rag", tags=["RAG System"])


@rag_router.post("/answer", response_model=SimpleRAGResponse, summary="Генерация ответа на вопрос")
async def generate_answer(
    query_data: SearchQuery = Body(
        example={
            "query": "Расскажи о чат-ботах EORA"
        }
    )
):
    """
    Генерирует ответ на вопрос пользователя на основе релевантных документов.
    Возвращает только текст ответа со встроенными markdown ссылками.
    """
    try:
        # Получаем RAG систему (инициализируем если нужно)
        global rag_system
        
        if rag_system is None:
            rag_system = await initialize_rag_system()
        
        # Проверяем доступность системы и генерируем ответ
        if rag_system:

            result = await rag_system.generate_answer(query_data.query)

        else:

            result = {
                "answer": "RAG система недоступна"
            }
        
        return SimpleRAGResponse(**result)
        
    except Exception as e:
        logger.error(f"Ошибка генерации ответа: {e}")

        raise HTTPException(status_code=500, detail=f"Ошибка генерации ответа: {str(e)}")
