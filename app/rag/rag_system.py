"""
RAG (Retrieval-Augmented Generation) система для поиска и генерации ответов
на основе текстов из базы данных с использованием эмбеддингов и ИИ.
"""

import asyncio
import logging
import os
import pickle
import re
import time
import traceback
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import aiohttp
import faiss
import numpy as np
from sentence_transformers import SentenceTransformer
from sqlalchemy import select
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type, before_sleep_log
from sqlalchemy.ext.asyncio import AsyncSession
from transformers import pipeline

from main.database_connection import AsyncSessionLocal
from parser.models.source_content import SourceContent
from parser.models.source_urls import SourceURL

# Configure logging
logging.basicConfig(level=logging.INFO)
from main.logger import logger

from rag.constants import (
    TOP_K, LAMBDA_PARAM, MIN_SCORE,
    SYSTEM_PROMPT_TEMPLATE, PLACEHOLDER_QUERY, PLACEHOLDER_SOURCES, PLACEHOLDER_CONTEXT,
    HF_MODEL_NAME, HF_MAX_LENGTH, HF_TEMPERATURE, HF_TOP_P
)

class TextChunk:
    """Класс для представления чанка текста с метаданными"""
    
    def __init__(
        self, 
        content: str, 
        source_id: int, 
        source_url: str, 
        source_title: str,
        chunk_index: int = 0,
        start_pos: int = 0,
        end_pos: int = 0,
        redirected_url: str = None
    ):
        self.content = content
        self.source_id = source_id
        self.source_url = source_url
        self.source_title = source_title
        self.chunk_index = chunk_index
        self.start_pos = start_pos
        self.end_pos = end_pos
        self.redirected_url = redirected_url
    
    def __repr__(self):
        return f"TextChunk(source_id={self.source_id}, chunk_index={self.chunk_index}, length={len(self.content)})"


class RAGSystem:
    """
    Система поиска и генерации ответов (RAG) на основе эмбеддингов
    """
    
    _instance = None  # Синглтон паттерн
    _initialization_lock = None  # Асинхронный мьютекс для класса
    
    def __new__(cls, *args, **kwargs):
        """Паттерн синглтон - создаем только один экземпляр"""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False

        return cls._instance
    
    def __init__(
        self,
        embedding_model_name: str = "paraphrase-MiniLM-L3-v2",  # Изменили на более лёгкую модель
        chunk_size: int = 1000,
        chunk_overlap: int = 200,
        faiss_index_path: str = "faiss_index.pkl",
        chunks_path: str = "text_chunks.pkl"
    ):
        """
        Инициализация RAG системы
        
        Args:
            embedding_model_name: Название модели для создания эмбеддингов
            chunk_size: Размер чанка в символах
            chunk_overlap: Перекрытие между чанками в символах
            faiss_index_path: Путь для сохранения FAISS индекса
            chunks_path: Путь для сохранения метаданных чанков
        """
        # Защита от повторной инициализации
        if hasattr(self, '_initialized') and self._initialized:
            return
        
        self.embedding_model_name = embedding_model_name
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.faiss_index_path = faiss_index_path
        self.chunks_path = chunks_path
        
        # Инициализация компонентов - ЛЕНИВАЯ ЗАГРУЗКА
        self.embedding_model = None
        self.faiss_index = None
        self.text_chunks: List[TextChunk] = []
        self.text_generator = None  # Локальная модель для генерации текста
        self.summarizer = None      # Локальная модель для суммаризации
        self._is_initialized = False
        self._components_initialized = False
        
        # Hugging Face API токен и настройки
        self.huggingface_token = os.getenv("HUGGINGFACE_TOKEN")
        self.hf_model_name = "facebook/bart-large-cnn"  # Модель для суммаризации
        self.hf_generation_model = "gpt2"  # Базовая модель для генерации (всегда доступна)

        # Параметры для локальной генерации  
        self.use_local_generation = False  # Отключаем суммаризацию
        self.generation_model_name = "microsoft/DialoGPT-medium"  # Или другая русскоязычная модель        # Асинхронный мьютекс для этого экземпляра

        if RAGSystem._initialization_lock is None:
            RAGSystem._initialization_lock = asyncio.Lock()
        
        self._initialized = True  # Помечаем что __init__ выполнен
    
    async def _ensure_initialized(self):
        """Инициализация компонентов"""
        # Используем мьютекс класса для защиты от параллельных инициализаций
        async with RAGSystem._initialization_lock:
            # Повторная проверка после получения блокировки
            if self._components_initialized:

                return
            
            await self._initialize_components()
            
            # Загружаем и обрабатываем данные из базы, если индекс не готов
            if not self.faiss_index or len(self.text_chunks) == 0:
                await self._load_and_process_data_protected()
            
            self._components_initialized = True
            self._is_initialized = True
    
    async def _load_and_process_data_protected(self):
        """
        Загрузка и обработка данных из базы данных
        
        Выполняет полный цикл подготовки данных для RAG системы:
        1. Загружает контент из базы данных (SourceContent + SourceURL)
        2. Фильтрует записи с пустым или слишком коротким содержимым (< 50 символов)
        3. Разбивает тексты на чанки с перекрытием используя _split_text_into_chunks
        4. Создает векторные эмбеддинги для всех чанков
        5. Строит FAISS индекс для быстрого поиска по сходству
        
        Note:
            - Использует JOIN для получения данных из SourceContent и SourceURL
            - Пропускает записи с content = None, пустой строкой или длиной < 50 символов
            - Обрабатывает ошибки и логирует их без прерывания работы системы
            - Результат сохраняется в self.text_chunks и self.faiss_index
            
        Raises:
            Exception: Логирует ошибки, но не прерывает выполнение программы
        """
        try:
            async with AsyncSessionLocal() as session:
                # Получаем все тексты из базы данных
                
                result = await session.execute(
                    select(SourceContent, SourceURL)
                    .join(SourceURL, SourceContent.source_url_id == SourceURL.id)
                    .where(SourceContent.content.isnot(None))
                    .where(SourceContent.content != "")
                )
                
                content_records = result.all()
                
                if not content_records:
                    return
                
                # Создаем чанки из всех текстов
                all_chunks = []
                
                for i, (content_record, url_record) in enumerate(content_records, 1):

                    if len(content_record.content.strip()) < 50:
                        continue
                    chunks = self._split_text_into_chunks(
                        text=content_record.content,
                        source_id=content_record.id,
                        source_url=url_record.url,
                        source_title=url_record.title,
                        redirected_url=url_record.redirected_url
                    )
                    all_chunks.extend(chunks)
                
                self.text_chunks = all_chunks
                
                # Создаем эмбеддинги
                await self._create_embeddings()
                
        except Exception as e:
            logger.error(f"Ошибка загрузки: {e}")
            traceback.print_exc()
    
    async def _initialize_components(self):
        """Инициализация всех компонентов системы"""
        try:
            # Инициализация модели эмбеддингов
            try:

                if self.embedding_model is None:
                    self.embedding_model = SentenceTransformer(self.embedding_model_name)

                else:
                    pass
            except Exception as e:
                logger.error(f"Ошибка загрузки модели эмбеддингов: {e}")

                return
            # Попытка загрузить существующий индекс
            await self._load_existing_index() 
           
        except Exception as e:
            logger.error(f"Ошибка инициализации: {e}")
    
    async def _initialize_local_models(self):
        """Инициализация локальных моделей для генерации текста"""
        try:
            
            # Используем более легкую модель для суммаризации
            if self.summarizer is None:
                # Используем модель суммаризации вместо генерации - она лучше подходит для нашей задачи
                self.summarizer = pipeline(
                    "summarization",
                    model="RussianNLP/FRED-T5-Summarizer",
#                    model="facebook/bart-large-cnn",  # Хорошая модель для суммаризации
                    device="cuda"  # CPU
                )
            
        except Exception as e:
            logger.error(f"Ошибка локальных моделей: {e}", flush=True)
            self.summarizer = None
    
    async def _load_existing_index(self):
        """Загружает существующий FAISS индекс и чанки"""
        try:
            
            if os.path.exists(self.faiss_index_path) and os.path.exists(self.chunks_path):
                
                # Загружаем FAISS индекс
                with open(self.faiss_index_path, 'rb') as f:
                    index_data = pickle.load(f)
                    self.faiss_index = index_data
                
                # Загружаем чанки
                with open(self.chunks_path, 'rb') as f:
                    self.text_chunks = pickle.load(f)

                pass
            else:
                pass
                
        except Exception as e:
            logger.warning(f"Ошибка загрузки существующего индекса: {e}")
            self.faiss_index = None
            self.text_chunks = []
    
    def _save_index(self):
        """Сохраняет FAISS индекс и чанки на диск"""
        try:
            if self.faiss_index is not None and self.text_chunks:                
                # Сохраняем FAISS индекс

                with open(self.faiss_index_path, 'wb') as f:
                    pickle.dump(self.faiss_index, f)
                
                with open(self.chunks_path, 'wb') as f:
                    pickle.dump(self.text_chunks, f)
                
                # Проверяем размеры файлов
                faiss_size = os.path.getsize(self.faiss_index_path) / 1024 / 1024
                chunks_size = os.path.getsize(self.chunks_path) / 1024 / 1024
                
                return

        except Exception as e:
            logger.error(f"Ошибка сохранения: {e}")

            return

    def _split_text_into_chunks(self, text: str, source_id: int, source_url: str, source_title: str, redirected_url: str = None) -> List[TextChunk]:
        """
        Разделяет текст на чанки с перекрытием
        
        Args:
            text: Исходный текст
            source_id: ID источника
            source_url: URL источника
            source_title: Заголовок источника
            redirected_url: URL после редиректа (если есть)
            
        Returns:
            List[TextChunk]: Список чанков
        """
        chunks = []
        
        # Очищаем текст
        text = text.strip()
        if not text:
            return chunks
        
        # Разделяем текст на чанки
        start = 0
        chunk_index = 0
        
        while start < len(text):
            
            # Вычисляем конец чанка
            end = start + self.chunk_size
            
            # Если это не последний чанк, пытаемся найти хорошее место для разрыва
            if end < len(text):
                
                search_range = min(100, len(text) - end)
                
                found_break = False
                # Ищем ближайший разрыв предложения или абзаца
                for i in range(search_range):
                    char_pos = end + i

                    if char_pos < len(text):
                        char = text[char_pos]

                        if char in '.!?\n':
                            end = char_pos + 1
                            found_break = True

                            break
                
                if not found_break:
                    pass

            else:
                end = len(text)
            
            # Извлекаем текст чанка
            chunk_text = text[start:end].strip()
            
            if chunk_text:
                chunk = TextChunk(
                    content=chunk_text,
                    source_id=source_id,
                    source_url=source_url,
                    source_title=source_title,
                    chunk_index=chunk_index,
                    start_pos=start,
                    end_pos=end,
                    redirected_url=redirected_url
                )
                chunks.append(chunk)
                chunk_index += 1
            
            # Следующий чанк начинается с перекрытием
            new_start = end - self.chunk_overlap
            
            # Если мы достигли конца текста, завершаем цикл
            if end >= len(text):
                break
            
            if new_start < 0:
                start = 0

            else:
                start = new_start
        
        return chunks
        
    async def load_and_process_database_texts(self):
        """
        Загружает все тексты из базы данных и создает эмбеддинги
        """
        # Проверка, что система уже обработала данные
        if len(self.text_chunks) > 0 and self.faiss_index is not None:
            return
        
        # Убеждаемся что компоненты инициализированы (БЕЗ рекурсии)
        if not self._components_initialized:
            await self._ensure_initialized()
            return  # _ensure_initialized уже вызовет загрузку данных
        
        if not self.embedding_model:
            return
        
        # Вызываем защищенную версию без рекурсии
        await self._load_and_process_data_protected()
    
    async def _create_embeddings(self):
        """Создает эмбеддинги для всех чанков и строит FAISS индекс"""
        if not self.text_chunks:

            return
        
        try:
            # Извлекаем тексты из чанков
            texts = [chunk.content for chunk in self.text_chunks]
            # Создаем эмбеддинги батчами для экономии памяти
            batch_size = 32
            all_embeddings = []
            total_batches = (len(texts) + batch_size - 1) // batch_size

            for i in range(0, len(texts), batch_size):
                batch_texts = texts[i:i + batch_size]
                batch_num = i//batch_size + 1                
                embeddings = self.embedding_model.encode(
                    batch_texts,
                    batch_size=len(batch_texts),
                    show_progress_bar=True
                )
                all_embeddings.append(embeddings)
            embeddings_matrix = np.vstack(all_embeddings)
            dimension = embeddings_matrix.shape[1]
            # Используем IndexFlatIP для косинусного сходства
            self.faiss_index = faiss.IndexFlatIP(dimension)
            faiss.normalize_L2(embeddings_matrix)
            self.faiss_index.add(embeddings_matrix.astype('float32'))
            # Сохраняем индекс
            self._save_index()
            
        except Exception as e:
            logger.error(f"Ошибка создания эмбеддингов: {e}")
            traceback.print_exc()
    
    async def search_relevant_chunks(self, query: str) -> List[Tuple[TextChunk, float]]:
        """
        Поиск наиболее релевантных текстовых чанков для заданного запроса.
        
        Функция выполняет семантический поиск по базе знаний, используя векторные 
        представления (эмбеддинги) для нахождения текстовых фрагментов, наиболее 
        релевантных пользовательскому запросу. Применяет алгоритм Maximum Marginal 
        Relevance (MMR) для обеспечения разнообразия результатов и избежания дублирования.
        
        Процесс поиска:
        1. Создание эмбеддинга (числового представления текста) для пользовательского запроса
        2. Поиск похожих векторов в FAISS индексе
        3. Фильтрация результатов по минимальному порогу релевантности
        4. Применение MMR для выбора разнообразных результатов (если найдено > TOP_K)
        5. Возврат отсортированного списка чанков с оценками релевантности
        
        Args:
            query (str): Пользовательский запрос для поиска релевантного контента.
                        Должен быть непустой строкой на естественном языке.
            
        Returns:
            List[Tuple[TextChunk, float]]: Список кортежей, где каждый кортеж содержит:
                - TextChunk: Объект текстового чанка с метаданными (контент, источник, URL)
                - float: Оценка релевантности (косинусное сходство) от 0.0 до 1.0
                
                Список отсортирован по убыванию релевантности и ограничен TOP_K элементами.
                Возвращает пустой список в случае ошибки или отсутствия релевантных результатов.
        
        Raises:
            Не выбрасывает исключения - все ошибки логируются и возвращается пустой список.
            
        Note:
            - Требует предварительной инициализации RAG системы (embedding_model, faiss_index, text_chunks)
            - Использует константу TOP_K (15) для ограничения количества результатов
            - Применяет L2 нормализацию к эмбеддингам для корректного вычисления косинусного сходства
            - MMR помогает избежать возврата множества похожих фрагментов из одного источника
        """
        # Убеждаемся что система инициализирована
        await self._ensure_initialized()
        
        if not self.embedding_model or not self.faiss_index or not self.text_chunks:
            logger.error("Отсутствует эмбеддинг, faiss индекс или тексты")

            return []
        
        try:
            # Создаем эмбеддинг для запроса
            query_embedding = self.embedding_model.encode([query])
            faiss.normalize_L2(query_embedding)
            search_k = TOP_K
            scores, indices = self.faiss_index.search(query_embedding.astype('float32'), search_k)
            
            # Фильтруем валидные результаты
            candidates = []

            for score, idx in zip(scores[0], indices[0]):

                if idx < len(self.text_chunks) and score >= MIN_SCORE:
                    chunk = self.text_chunks[idx]
                    candidates.append((chunk, float(score)))
            
            if len(candidates) <= TOP_K:
                results = candidates

            else:
                results = self._apply_mmr_selection(candidates, TOP_K)

            return results
            
        except Exception as e:
            logger.error(f"Ошибка поиска: {e}")
            return []
    
    def _apply_mmr_selection(self, candidates: List[Tuple[TextChunk, float]], k: int):
        """
        Применяет Maximum Marginal Relevance для выбора разнообразных результатов
        
        MMR (Maximal Marginal Relevance) — это алгоритм reranking'а результатов поиска,
        который учитывает не только релевантность документов к запросу, но и их разнообразие относительно друг друга.

        Args:
            candidates: Список кандидатов (chunk, score)
            k: Количество результатов для отбора
        """ 
       
        if len(candidates) <= k:

            return candidates
        
        # Получаем эмбеддинги всех кандидатов
        candidate_embeddings = []

        for chunk, _ in candidates:
            # Находим эмбеддинг этого чанка в индексе
            chunk_idx = self.text_chunks.index(chunk)
            # Извлекаем эмбеддинг из FAISS индекса
            embedding = self.faiss_index.reconstruct(chunk_idx)
            candidate_embeddings.append(embedding)
        
        selected = []
        remaining_indices = list(range(len(candidates)))
        
        # Выбираем первый самый релевантный
        best_idx = 0
        selected.append(candidates[best_idx])
        remaining_indices.remove(best_idx)
        selected_embeddings = [candidate_embeddings[best_idx]]
        
        # Выбираем остальные с учетом MMR
        while len(selected) < k and remaining_indices:
            best_mmr_score = -1
            best_idx = -1
            
            for idx in remaining_indices:
                chunk, relevance_score = candidates[idx]
                embedding = candidate_embeddings[idx]
                
                # Релевантность к запросу
                relevance = relevance_score
                
                # Максимальное сходство с уже выбранными
                max_similarity = 0

                for selected_emb in selected_embeddings:
                    similarity = np.dot(embedding, selected_emb) / (np.linalg.norm(embedding) * np.linalg.norm(selected_emb))
                    max_similarity = max(max_similarity, similarity)
                
                # MMR формула
                mmr_score = LAMBDA_PARAM * relevance - (1 - LAMBDA_PARAM) * max_similarity
                
                if mmr_score > best_mmr_score:
                    best_mmr_score = mmr_score
                    best_idx = idx
            
            if best_idx != -1:
                selected.append(candidates[best_idx])
                selected_embeddings.append(candidate_embeddings[best_idx])
                remaining_indices.remove(best_idx)
        
        return selected
    
    def _validate_query(self, query: str) -> Dict[str, Any]:
        """
        Валидирует запрос пользователя на корректность
        
        Args:
            query: Запрос пользователя
            
        Returns:
            Dict: {"is_valid": bool, "reason": str, "suggested_response": str}
        """
        # Очищаем запрос
        cleaned_query = query.strip()
        
        # 1. Проверка длины запроса
        if len(cleaned_query) < 3:

            return {
                "is_valid": False,
                "reason": "too_short",
                "suggested_response": "Пожалуйста, сформулируйте ваш вопрос более подробно."
            }
        
        if len(cleaned_query) > 1000:

            return {
                "is_valid": False,
                "reason": "too_long", 
                "suggested_response": "Ваш вопрос слишком длинный. Пожалуйста, сократите его до 1000 символов."
            }
        
        # 2. Проверка на спам и бессмыслицу
        if self._is_spam_or_nonsense(cleaned_query):

            return {
                "is_valid": False,
                "reason": "spam_or_nonsense",
                "suggested_response": "Я не понимаю ваш вопрос. Пожалуйста, задайте вопрос о чат-ботах EORA, их возможностях или проектах компании."
            }
        
        # 3. Проверка на нецензурную лексику или неподходящий контент
        if self._contains_inappropriate_content(cleaned_query):

            return {
                "is_valid": False,
                "reason": "inappropriate",
                "suggested_response": "Пожалуйста, формулируйте вопросы корректно. Я готов ответить на вопросы о чат-ботах и решениях EORA."
            }
        
        # 4. Проверка на тематическую релевантность (опционально)
        if not self._is_business_relevant(cleaned_query):

            return {
                "is_valid": False,
                "reason": "off_topic",
                "suggested_response": "Я специализируюсь на вопросах о чат-ботах и решениях EORA. Пожалуйста, задайте вопрос по этой теме."
            }
        
        return {"is_valid": True, "reason": "valid", "suggested_response": ""}
    
    def _is_spam_or_nonsense(self, query: str) -> bool:
        """Проверяет запрос на спам или бессмыслицу"""
        query_lower = query.lower()
        
        # Паттерны бессмыслицы
        nonsense_patterns = [
            # Некорректное время - исправляем паттерны
            r'\d{1,2}:[6-9]\d',  # 17:60+, 12:70+ и т.д.
            r'[2-9]\d:\d{2}',    # 25:30, 30:45 и т.д.
            r'\d{1,2}:9[0-9]',   # 17:90+, 12:99 и т.д.
            
            # Повторяющиеся символы
            r'(.)\1{10,}',      # aaaaaaaaaa...
            r'([а-яё]{1,3})\1{5,}',  # хахахахаха...
            
            # Случайные наборы символов
            r'^[a-zа-яё]{1,2}(\s[a-zа-яё]{1,2}){10,}',  # a b c d e f g...
            
            # Тестовые фразы
            r'тест\s*тест',
            r'проверка\s*проверка',
            r'asdf|qwerty|1234567890',
        ]
        
        for pattern in nonsense_patterns:
            if re.search(pattern, query_lower):
                return True
        
        # Проверка на отсутствие гласных (возможно, случайный набор согласных)
        consonants_only = re.sub(r'[аеёиоуыэюяaeiouy\s\d\W]', '', query_lower)
        if len(consonants_only) > 8 and len(consonants_only) > len(query_lower) * 0.7:
            return True
        
        # Проверка на слишком много цифр (возможно, случайный набор)
        digits_count = sum(1 for c in query if c.isdigit())
        if digits_count > len(query) * 0.5 and len(query) > 10:
            return True
            
        return False
    
    def _contains_inappropriate_content(self, query: str) -> bool:
        """Проверяет запрос на неподходящий контент"""
        query_lower = query.lower()
        
        # Базовые фильтры (можно расширить)
        inappropriate_words = [
            # Добавьте здесь слова для фильтрации если нужно
            # Пока оставляем пустым для демонстрации
            "тут разные не хорошие слова, которые я не знаю",
        ]
        
        for word in inappropriate_words:

            if word in query_lower:

                return True
                
        return False
    
    def _is_business_relevant(self, query: str) -> bool:
        """
        Проверяет, связан ли запрос с бизнес-тематикой EORA
        Возвращает True если запрос релевантен, False если нет
        """
        query_lower = query.lower()
        
        # Ключевые слова бизнес-тематики
        business_keywords = [
            # Основные термины
            'чат-бот', 'чатбот', 'бот', 'ассистент', 'помощник',
            'eora', 'эора', 'компания', 'решение', 'сервис', 'услуга',
            'автоматизация', 'клиент', 'поддержка', 'консультация',
            'проект', 'проекты', 'проектах', 'проектов',
            
            # Технические термины
            'ии', 'ai', 'искусственный интеллект', 'машинное обучение',
            'интеграция', 'api', 'разработка', 'внедрение', 'внедрить',
            
            # Бизнес-процессы
            'продажи', 'маркетинг', 'hr', 'кадры', 'персонал',
            'заказ', 'оплата', 'доставка', 'товар', 'услуга',
        ]
        
        # Проверяем наличие хотя бы одного ключевого слова
        for keyword in business_keywords:

            if keyword in query_lower:

                return True
        
        # Проверяем на специфические бизнес-паттерны (более точно)
        business_patterns = [
            r'\bчто\s+(умеет|делает|может)',  # "что умеет", "что делает"
            r'\bкак\s+(внедрить|использовать|работает)',  # "как внедрить", "как использовать"
            r'\bрасскажи\s+о\s+(проект|решени|возможност)',  # "расскажи о проектах"
        ]
        
        for pattern in business_patterns:

            if re.search(pattern, query_lower):

                return True
        
        # Если запрос очень короткий, считаем его потенциально релевантным
        if len(query.strip()) <= 15:

            return True
            
        return False

    async def generate_answer(self, query: str) -> Dict[str, Any]:
        """
        Генерирует ответ на запрос пользователя
        
        Args:
            query: Запрос пользователя
            
        Returns:
            Dict: Ответ с метаданными
        """
        
        # 1. ВАЛИДАЦИЯ ЗАПРОСА
        validation_result = self._validate_query(query)

        if not validation_result["is_valid"]:

            return {
                "answer": validation_result["suggested_response"],
                "validation_error": validation_result["reason"]
            }
        
        # 2. ПОИСК РЕЛЕВАНТНЫХ ЧАНКОВ
        relevant_chunks = await self.search_relevant_chunks(query)
        
        # 3. ПРОВЕРКА КАЧЕСТВА РЕЗУЛЬТАТОВ ПОИСКА
        if not relevant_chunks:

            return {
                "answer": "Информация по этому вопросу отсутствует в базе данных. Попробуйте переформулировать вопрос или задать вопрос о чат-ботах EORA."
            }
        
        # Проверяем средний уровень релевантности
        avg_relevance = sum(score for _, score in relevant_chunks) / len(relevant_chunks)
        min_relevance_threshold = 0.1  # Минимальный порог релевантности
        
        if avg_relevance < min_relevance_threshold:

            return {
                "answer": "Ваш вопрос не очень подходит к имеющейся информации о чат-ботах EORA. Попробуйте задать более конкретный вопрос о наших решениях, проектах или возможностях чат-ботов.",
                "low_relevance": True,
                "avg_relevance": avg_relevance
            }

        # 4. ПОДГОТОВКА КОНТЕКСТА
        context_parts = []
        sources = []
        source_dict = {}
        source_counter = 1
        
        # Группируем чанки по источникам
        for chunk, score in relevant_chunks:
            source_key = f"{chunk.source_url}#{chunk.source_title}"

            if source_key not in source_dict:
                source_dict[source_key] = {
                    "id": source_counter,
                    "title": chunk.source_title,
                    "url": chunk.source_url,
                    "fragments": []
                }
                source_counter += 1
            
            source_dict[source_key]["fragments"].append(chunk.content)
            
            # Добавляем в sources для API ответа
            sources.append({
                "id": chunk.source_id,
                "title": chunk.source_title,
                "url": chunk.source_url,
                "relevance_score": score,
                "chunk_index": chunk.chunk_index,
                "source_number": source_dict[source_key]["id"]
            })
        
        # Формируем контекст в формате EORABot
        for source_data in source_dict.values():
            context_parts.append(f"\n{source_data['id']} {source_data['title']}")

            for fragment in source_data['fragments']:
                context_parts.append(fragment)
        
        context = "\n".join(context_parts)
        
        # Генерируем ответ через внешний API
        # Используется ТОЛЬКО Hugging Face API с моделью SmolLM3
        if self.huggingface_token:
            answer = await self._generate_answer_with_api(query, relevant_chunks)

            if answer:

                return {
                    "answer": answer
                }

        return {
            "answer": "Извините, не удалось сгенерировать ответ на ваш вопрос."
        }
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=2, max=20),
        retry=retry_if_exception_type((aiohttp.ClientError, asyncio.TimeoutError)),
        before_sleep=before_sleep_log(logger, logging.WARNING)
    )
    async def _query_huggingface_api(self, prompt: str) -> Optional[str]:
        """
        Выполняет асинхронный запрос к Hugging Face Inference API для генерации текста.
        
        Функция использует предустановленные константы для всех параметров API:
        - Модель: HF_MODEL_NAME (SmolLM3-3B)
        - Длина ответа: HF_MAX_LENGTH (300 токенов)
        - Температура: HF_TEMPERATURE (0.6)
        - Top-p: HF_TOP_P (0.95)
        
        Args:
            prompt (str): Текст промпта для генерации ответа. Должен содержать
                         инструкции и контекст для модели.
        
        Returns:
            Optional[str]: Сгенерированный текст ответа или None в случае ошибки.
                          Возвращает содержимое message.content.
        
        Raises:
            Не выбрасывает исключения - все ошибки логируются и возвращается None.
        
        Note:
            - Требует наличия валидного self.huggingface_token
            - Использует константы из rag.constants для всех параметров
            - Использует таймаут 10 секунд для HTTP запросов
            - Автоматически повторяет запрос при статусе 503 (модель загружается)
            - Для SmolLM3 отключает extended thinking режим через "/no_think"
            - Логирует детальную диагностику ошибок включая превью токена
        """
        if not self.huggingface_token:

            return None
        
        # Диагностика токена (показываем только первые и последние символы)
        token_preview = f"{self.huggingface_token[:7]}...{self.huggingface_token[-4:]}" if len(self.huggingface_token) > 11 else self.huggingface_token

        api_url = f"https://api-inference.huggingface.co/models/{HF_MODEL_NAME}/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.huggingface_token}",
            "Content-Type": "application/json"
        }

        messages = [
            {"role": "system", "content": "/no_think"},  # Отключаем extended thinking
            {"role": "user", "content": prompt}
        ]
        
        payload = {
            "messages": messages,
            "parameters": {
                "max_new_tokens": HF_MAX_LENGTH,
                "temperature": HF_TEMPERATURE,
                "top_p": HF_TOP_P,
                "do_sample": True
            }
        }

        try:
            timeout = aiohttp.ClientTimeout(total=10)

            async with aiohttp.ClientSession(timeout=timeout) as session:

                async with session.post(api_url, headers=headers, json=payload) as response:
                    if response.status == 200:
                        result = await response.json()

                        if len(result["choices"]) > 0:
                            choice = result["choices"][0]
  
                            if "message" in choice and "content" in choice["message"]:
                                generated = choice["message"]["content"].strip()
  
                                return generated
  
                        return None
                        
                    else:
                        error_text = await response.text()
                        logger.error(f"HTTP {response.status}: {error_text}")
                        
                        # Более детальная диагностика ошибок
                        if response.status == 503 and "loading" in error_text.lower():
                            # Позволяем retry декоратору повторить для ошибки загрузки модели
                            raise aiohttp.ClientResponseError(
                                request_info=response.request_info,
                                history=response.history,
                                status=response.status,
                                message="Model loading error"
                            )
                        
                        return None

        except asyncio.TimeoutError:
            logger.error("Таймаут запроса к Hugging Face API")

            return None

        except Exception as e:
            logger.error(f"Ошибка запроса к HF API: {e}")

            return None

    
    def _format_huggingface_answer(self, generated_text: str, source_dict: Dict) -> str:
        """Форматирует ответ от Hugging Face с добавлением источников"""
        if not generated_text:
            return "У меня нет такой информации."
        
        # Очищаем и форматируем ответ
        answer = generated_text.strip()
        
        # ВАЖНО: Убираем экранированные символы JSON
        answer = answer.replace('\\"', '"')  # Убираем экранированные кавычки
        answer = answer.replace('\\n', '\n')  # Убираем экранированные переносы строк
        answer = answer.replace('\\t', '\t')  # Убираем экранированные табуляции
        answer = answer.replace('\\\\', '\\')  # Убираем двойные обратные слеши
        
        # Проверяем, не содержит ли ответ поврежденные символы или нечитаемый текст
        # Если слишком много неалфавитных символов, используем fallback
        total_chars = len(answer)
        if total_chars > 0:
            # Считаем количество нормальных символов (буквы, цифры, пробелы, знаки препинания)
            normal_chars = sum(1 for c in answer if c.isalnum() or c.isspace() or c in '.,!?-:;()[]')
            abnormal_ratio = (total_chars - normal_chars) / total_chars
            
            # Если больше 30% символов ненормальные, возвращаем fallback
            if abnormal_ratio > 0.3:

                return "Информация о чат-ботах EORA найдена в базе данных. " + \
                       f"[{', '.join([str(data['id']) for data in source_dict.values()])}]"
        
        # Убираем возможные артефакты генерации
        answer = re.sub(r'^(Ответ:|Answer:|Bot:)', '', answer).strip()
        answer = re.sub(r'\n+', ' ', answer)  # Заменяем переносы строк на пробелы
        answer = re.sub(r'\s+', ' ', answer)  # Убираем лишние пробелы
        
        # Если ответ слишком короткий или состоит только из символов, используем fallback
        if len(answer.strip()) < 10 or not any(c.isalpha() for c in answer):

            return "Информация о чат-ботах EORA найдена в базе данных. " + \
                   f"[{', '.join([str(data['id']) for data in source_dict.values()])}]"
        
        # Добавляем ссылки на источники
        if len(source_dict) == 1:
            source = list(source_dict.values())[0]
            answer += f" [{source['id']}]"

        elif len(source_dict) > 1:
            source_refs = [f"[{data['id']}]" for data in source_dict.values()]
            answer += f" {', '.join(source_refs)}"
        
        # Добавляем список источников если их больше одного
        if len(source_dict) > 1:
            answer += "\n\nИсточники:\n"

            for source_data in source_dict.values():
                answer += f"[{source_data['id']}] {source_data['title']}\n"
        
        return answer.strip()
    
    async def _generate_answer_with_api(self, query: str, relevant_chunks: List[Tuple[TextChunk, float]]) -> Optional[str]:
        """
        Генерирует ответ с помощью Hugging Face API
        Объединяет топ-5 чанков в один контекст и отправляет через API
        """
        try:
            
            if not relevant_chunks:
                return None
            
            # Шаг 1: Объединяем топ-5 чанков в один большой контекст
            
            combined_chunks = []
            source_info = []
            
            for i, (chunk, score) in enumerate(relevant_chunks[:5]):  # Берем топ-5 чанков
                
                # Ограничиваем размер каждого чанка
                chunk_text = re.sub(r'\s+', ' ', chunk.content)
                chunk_text = chunk_text[:800].strip()
                if len(chunk.content) > 800:
                    chunk_text += "..."
                
                combined_chunks.append(chunk_text)
                source_info.append({
                    "title": chunk.source_title,
                    "url": chunk.source_url,
                    "index": i + 1
                })
            
            # Объединяем все чанки в один большой контекст с привязкой к источникам
            combined_context_parts = []
            for i, chunk_text in enumerate(combined_chunks):
                source = source_info[i]
                # Форматируем каждый чанк с указанием источника
                chunk_with_source = f"Источник [{source['index']}] ({source['title']}): {chunk_text}"
                combined_context_parts.append(chunk_with_source)
            
            combined_context = "\n\n".join(combined_context_parts)
            
            # Формируем список источников для промпта
            sources_list = "\n".join([
                f"[{info['index']}] {info['title']}: {info['url']}"
                for info in source_info
            ])

            
            # Шаг 2: Формируем промпт с использованием шаблона из констант
            prompt = SYSTEM_PROMPT_TEMPLATE.replace(PLACEHOLDER_QUERY, query) \
                                          .replace(PLACEHOLDER_SOURCES, sources_list) \
                                          .replace(PLACEHOLDER_CONTEXT, combined_context)
            
            # Шаг 3: Отправляем в Hugging Face API
            
            # Проверяем наличие токена
            if not self.huggingface_token:
                return None
            
            generated_text = await self._query_huggingface_api(prompt)
            
            # Шаг 4: Возвращаем полученный ответ
            if generated_text and len(generated_text.strip()) > 10:
                # Очищаем ответ от артефактов
                answer = generated_text.strip()
                
                # ВАЖНО: Убираем экранированные символы JSON
                answer = answer.replace('\\"', '"')  # Убираем экранированные кавычки
                answer = answer.replace('\\n', ' ')  # Убираем экранированные переносы строк
                answer = answer.replace('\\t', ' ')  # Убираем экранированные табуляции  
                answer = answer.replace('\\\\', '\\')  # Убираем двойные обратные слеши
                
                # Убираем префиксы и нормализуем пробелы
                answer = re.sub(r'^(Ответ:|Answer:|Bot:)', '', answer).strip()
                answer = re.sub(r'\n+', ' ', answer)
                answer = re.sub(r'\s+', ' ', answer)
                
                # Ссылки уже встроены в ответ модели, не добавляем дополнительные
                return answer

            else:

                return None
            
        except Exception as e:
            logger.error(f"Ошибка Hugging Face API: {e}", flush=True)

            return None

    def _format_generated_answer(self, generated_text: str, relevant_chunks: List[Tuple[TextChunk, float]]) -> str:
        """Форматирует сгенерированный ответ с добавлением источников"""
        # Группируем источники
        sources_dict = {}
        source_counter = 1
        
        for chunk, score in relevant_chunks:
            # Используем redirected_url если он есть, иначе оригинальный url
            final_url = chunk.redirected_url if chunk.redirected_url else chunk.source_url
            source_key = f"{final_url}#{chunk.source_title}"

            if source_key not in sources_dict:
                sources_dict[source_key] = {
                    "id": source_counter,
                    "title": chunk.source_title,
                    "url": final_url
                }
                source_counter += 1
        
        # Форматируем ответ
        answer = generated_text.strip()
        
        # Добавляем ссылки на источники
        if len(sources_dict) == 1:
            source = list(sources_dict.values())[0]
            answer += f" [{source['id']}]"

        else:
            source_refs = [f"[{data['id']}]" for data in sources_dict.values()]
            answer += f" {', '.join(source_refs)}"
        
        # Добавляем список источников
        if len(sources_dict) > 1:
            answer += "\n\nИсточники:\n"

            for source_data in sources_dict.values():
                answer += f"[{source_data['id']}] {source_data['title']}\n"
        
        return answer
    
    async def rebuild_index(self):
        """Пересоздает весь индекс с нуля"""
        
        # Очищаем текущий индекс
        self.faiss_index = None
        self.text_chunks = []
        
        # Удаляем файлы индекса
        try:

            if os.path.exists(self.faiss_index_path):
                os.remove(self.faiss_index_path)

            if os.path.exists(self.chunks_path):
                os.remove(self.chunks_path)

        except Exception as e:
            print(f"⚠️ Ошибка удаления старых файлов: {e}", flush=True)

        await self.load_and_process_database_texts()

    def get_statistics(self) -> Dict[str, Any]:
        """Возвращает статистику системы"""
        return {
            "total_chunks": len(self.text_chunks),
            "index_ready": self.faiss_index is not None,
            "embedding_model": self.embedding_model_name,
            "chunk_size": self.chunk_size,
            "chunk_overlap": self.chunk_overlap,
            "huggingface_available": self.huggingface_token is not None,
            "hf_model": self.hf_generation_model,
            "use_summarization": False,  # Отключена
            "unique_sources": len(set(chunk.source_id for chunk in self.text_chunks))
        }

# Глобальный экземпляр RAG системы
rag_system = None
_initialization_lock = asyncio.Lock()  # Мьютекс для предотвращения параллельных инициализаций


async def initialize_rag_system() -> RAGSystem:
    """
    Инициализирует глобальную RAG систему с защитой от параллельных инициализаций
        
    Returns:
        RAGSystem: Инициализированная система
    """
    global rag_system
    
    # Если система уже инициализирована, возвращаем её
    if rag_system is not None:

        return rag_system
    
    # Блокируем параллельные инициализации
    async with _initialization_lock:
        # Повторная проверка после получения блокировки
        if rag_system is not None:

            return rag_system
        print("🔄 Инициализация RAG системы... Это может занять несколько минут при первом запуске.", flush=True)
        rag_system = RAGSystem()
        print("🔄 Инициализация RAG системы окончена ", flush=True)

    return rag_system



