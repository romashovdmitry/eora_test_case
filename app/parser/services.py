import os
import asyncio
import logging
import re
from typing import Optional, Dict, Any

from bs4 import BeautifulSoup
import trafilatura
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type, before_sleep_log
import httpx

# Configure logging
from main.logger import logger


class HTTPClientFactory:
    """
    Фабрика для создания унифицированных HTTP-клиентов с общими настройками
    """
    
    @staticmethod
    def create_client(
        timeout: int = 10,
        follow_redirects: bool = True,
        user_agent: str = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        additional_headers: Optional[Dict[str, str]] = None
    ) -> httpx.AsyncClient:
        """
        Создает HTTP-клиент
        
        Args:
            timeout: Таймаут запроса в секундах
            follow_redirects: Следовать ли редиректам
            user_agent: User-Agent заголовок
            additional_headers: Дополнительные заголовки
            
        Returns:
            httpx.AsyncClient: Настроенный HTTP-клиент
        """
        headers = {
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
        }
        
        if additional_headers:
            headers.update(additional_headers)
        
        return httpx.AsyncClient(
            timeout=httpx.Timeout(timeout),
            headers=headers,
            follow_redirects=follow_redirects
        )
    
    @staticmethod
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((httpx.ConnectError, httpx.TimeoutException, httpx.RequestError, httpx.HTTPStatusError)),
        before_sleep=before_sleep_log(logger, logging.WARNING)
    )
    async def make_request(
        url: str,
        method: str = "GET",
        timeout: int = 10,
        follow_redirects: bool = True,
        user_agent: str = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        additional_headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> httpx.Response:
        """
        Выполняет HTTP-запрос с унифицированными настройками
        
        Args:
            url: URL для запроса
            method: HTTP метод
            timeout: Таймаут запроса
            follow_redirects: Следовать ли редиректам
            user_agent: User-Agent заголовок
            additional_headers: Дополнительные заголовки
            **kwargs: Дополнительные параметры для httpx
            
        Returns:
            httpx.Response: Ответ сервера
            
        Raises:
            httpx.HTTPError: При ошибках HTTP
        """
        async with HTTPClientFactory.create_client(
            timeout=timeout,
            follow_redirects=follow_redirects,
            user_agent=user_agent,
            additional_headers=additional_headers
        ) as client:
            response = await client.request(method, url, **kwargs)
            response.raise_for_status()
            return response


class URLContentExtractor:
    """
    Класс для извлечения текстового контента с веб-страниц
    с поддержкой retry и обработки ошибок.
    Использует trafilatura как основной инструмент извлечения.
    """
    
    def __init__(
        self,
        max_retries: int = 3,
        timeout: int = 10,
        retry_delay: float = 1.0,
        user_agent: str = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        use_fallback: bool = True,
        min_text_length: int = 10
    ):
        """
        Инициализация экстрактора
        
        Args:
            max_retries: Максимальное количество попыток
            timeout: Таймаут запроса в секундах
            retry_delay: Задержка между попытками в секундах
            user_agent: User-Agent заголовок
            use_fallback: Использовать ли BeautifulSoup как fallback
            min_text_length: Минимальная длина текста для считывания успешным
        """
        self.max_retries = max_retries
        self.timeout = timeout
        self.retry_delay = retry_delay
        self.user_agent = user_agent
        self.use_fallback = use_fallback
        self.min_text_length = min_text_length
        
        # Настройка trafilatura
        self._setup_trafilatura_config()
    
    def _setup_trafilatura_config(self):
        """Настройка конфигурации trafilatura"""
        try:
            # Создаем кастомную конфигурацию для trafilatura
            self.trafilatura_config = trafilatura.settings.use_config()
            
            # Настройки для лучшего извлечения текста
            self.trafilatura_config.set('DEFAULT', 'EXTRACTION_TIMEOUT', '30')
            self.trafilatura_config.set('DEFAULT', 'MIN_EXTRACTED_SIZE', str(self.min_text_length))
            self.trafilatura_config.set('DEFAULT', 'MIN_OUTPUT_SIZE', str(self.min_text_length))
            
            # Убираем лог конфигурации trafilatura
            
        except Exception as e:
            # Убираем лог ошибки настройки trafilatura
            self.trafilatura_config = None
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((httpx.ConnectError, httpx.TimeoutException, httpx.RequestError, httpx.HTTPStatusError)),
        before_sleep=before_sleep_log(logger, logging.WARNING)
    )
    async def get_url_text(self, url: str) -> str:
        """
        Fetch and clean text from a webpage with retry logic.
        
        Args:
            url: URL для парсинга
            
        Returns:
            str: Извлеченный текст или пустая строка при ошибке
            
        Raises:
            URLContentExtractionError: При критических ошибках
        """
        # Убираем противоречивый лог - он создает путаницу с основным парсингом
        # print("🚀 Парсинг начался")
        
        try:
            response = await HTTPClientFactory.make_request(
                url=url,
                timeout=self.timeout,
                user_agent=self.user_agent,
                follow_redirects=True
            )
            
            # Извлекаем текст с помощью trafilatura
            text = await self._extract_text_from_html(response.text, url)
            
            if text:
                return text
            else:
                return ""
                    
        except httpx.HTTPStatusError as e:
            error_msg = f"HTTP ошибка {e.response.status_code} для {url}"
            logger.warning(error_msg)
            
            # Не повторяем для некоторых кодов ошибок
            if e.response.status_code in [404, 403, 401, 410]:
                raise URLContentExtractionError(f"Не удается получить доступ к {url}: {error_msg}")
            # Для других HTTP ошибок позволяем retry декоратору повторить
            raise
            
        except (httpx.ConnectError, httpx.TimeoutException, httpx.RequestError) as e:
            # Не логируем здесь - retry декоратор будет логировать финальную ошибку
            # Позволяем retry декоратору повторить для сетевых ошибок
            raise
            
        except Exception as e:
            error_msg = f"Неожиданная ошибка при обращении к {url}: {str(e)}"
            logger.error(error_msg)
            # Для неожиданных ошибок также позволяем повторить
            raise
    
    async def _extract_text_from_html(self, html: str, url: str) -> Optional[str]:
        """
        Извлекает текст из HTML с помощью trafilatura
        
        Args:
            html: HTML контент
            url: URL источника (для логирования)
            
        Returns:
            str: Извлеченный текст или None
        """
        try:
            # Используем trafilatura с расширенными настройками
            text = trafilatura.extract(
                html,
                include_comments=False,
                include_links=False,
                include_images=False,
                include_tables=True,
                include_formatting=False,
                favor_precision=True,
                favor_recall=False,
                deduplicate=True,
                config=trafilatura.settings.use_config()
            )
            
            if text and len(text.strip()) > self.min_text_length:  # Проверяем что текст не пустой
                # Очищаем и нормализуем текст
                text = self._clean_text(text)
                return text
            else:
                if self.use_fallback:
                    return await self._fallback_text_extraction(html, url)
                else:
                    return None
                
        except Exception as e:
            logger.error(f"Ошибка trafilatura для {url}: {str(e)}")
            if self.use_fallback:
                return await self._fallback_text_extraction(html, url)
            else:
                return None
    
    async def _fallback_text_extraction(self, html: str, url: str) -> Optional[str]:
        """
        Альтернативный метод извлечения текста с помощью BeautifulSoup
        
        Args:
            html: HTML контент
            url: URL источника
            
        Returns:
            str: Извлеченный текст или None
        """
        try:
            soup = BeautifulSoup(html, 'html.parser')
            
            # Удаляем скрипты и стили
            for script in soup(["script", "style", "nav", "footer", "header"]):
                script.decompose()
            
            # Извлекаем текст
            text = soup.get_text()
            text = self._clean_text(text)
            
            if text:
                return text
            else:
                return None
                
        except Exception as e:
            logger.error(f"Ошибка альтернативного извлечения для {url}: {str(e)}")
            return self._basic_text_extraction(html, url)
    
    def _basic_text_extraction(self, html: str, url: str) -> Optional[str]:
        """
        Базовое извлечение текста через регулярные выражения (когда BS4 недоступен)
        
        Args:
            html: HTML контент
            url: URL источника
            
        Returns:
            str: Извлеченный текст или None
        """
        try:
            # Удаляем скрипты и стили
            html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
            html = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.DOTALL | re.IGNORECASE)
            
            # Удаляем HTML теги
            text = re.sub(r'<[^>]+>', '', html)
            
            # Декодируем HTML сущности
            import html as html_module
            text = html_module.unescape(text)
            
            # Очищаем текст
            text = self._clean_text(text)
            
            if text and len(text.strip()) > self.min_text_length:
                return text
            else:
                return None
                
        except Exception as e:
            return None
    
    def _clean_text(self, text: str) -> str:
        """
        Очищает и нормализует извлеченный текст
        
        Args:
            text: Сырой текст
            
        Returns:
            str: Очищенный текст
        """
        # Удаляем лишние пробелы и переносы строк
        lines = [line.strip() for line in text.splitlines()]
        lines = [line for line in lines if line]  # Удаляем пустые строки
        
        # Объединяем строки
        cleaned_text = '\n'.join(lines)
        
        # Удаляем множественные пробелы
        cleaned_text = re.sub(r'\s+', ' ', cleaned_text)
        
        return cleaned_text.strip()
    
    async def extract_content(self, url: str) -> dict:
        """
        Главный метод для извлечения контента с URL
        
        Args:
            url: URL для парсинга
            
        Returns:
            dict: Результат парсинга с ключами success, content, title, description, metadata, error
        """
        try:
            detailed_result = await self.extract_detailed_content(url)
            
            if 'error' in detailed_result:

                return {
                    'success': False,
                    'url': url,
                    'error': detailed_result['error'],
                    'content': '',
                    'title': '',
                    'description': '',
                    'metadata': {}
                }
            
            # Формируем успешный результат
            content = detailed_result.get('text', '') or ''
            title = detailed_result.get('title', '') or ''
            description = detailed_result.get('description', '') or ''
            
            # Собираем метаданные
            metadata = {
                'author': detailed_result.get('author'),
                'date': detailed_result.get('date'),
                'language': detailed_result.get('language'),
                'comments': detailed_result.get('comments'),
                'extraction_method': 'trafilatura',
                'content_length': len(content),
                'has_comments': bool(detailed_result.get('comments')),
                'extraction_timestamp': str(asyncio.get_event_loop().time())
            }
            
            success = bool(content and len(content.strip()) > self.min_text_length)
            
            result = {
                'success': success,
                'url': url,
                'content': content,
                'title': title,
                'description': description,
                'metadata': metadata
            }
            
            if success:
                pass  # Убираем детальный лог извлечения

            else:
                result['error'] = f"Извлечено недостаточно контента (минимум {self.min_text_length} символов)"
            
            return result
            
        except (httpx.ConnectError, httpx.TimeoutException, httpx.RequestError) as e:
            error_msg = f"{type(e).__name__}: {str(e)}"
            return {
                'success': False,
                'url': url,
                'error': error_msg,
                'content': '',
                'title': '',
                'description': '',
                'metadata': {'error': error_msg, 'error_type': 'network'}
            }
            
        except Exception as e:
            error_msg = f"Ошибка извлечения контента: {str(e)}"
            return {
                'success': False,
                'url': url,
                'error': error_msg,
                'content': '',
                'title': '',
                'description': '',
                'metadata': {'error': error_msg, 'error_type': 'extraction'}
            }

    async def extract_detailed_content(self, url: str) -> dict:
        """
        Извлекает детальную информацию о контенте с помощью trafilatura
        
        Args:
            url: URL для анализа
            
        Returns:
            dict: Детальная информация о контенте
        """
        try:
            # Дополнительная очистка URL на всякий случай
            clean_url = url.strip()
            clean_url = ''.join(char for char in clean_url if char.isprintable())
            
            if clean_url != url:
                url = clean_url
            
            # Используем унифицированный метод запроса с retry-логикой
            request_result = await unified_url_request(
                url=url,
                timeout=self.timeout,
                max_retries=self.max_retries
            )
            
            if not request_result['success']:

                return {
                    'url': url, 
                    'error': request_result['error'],
                    'error_type': request_result.get('error_type', 'Unknown'),
                    'attempts': request_result.get('attempt', 0)
                }
            
            response = request_result['response']
            html = response.text
            final_url = request_result['final_url']
            was_redirected = request_result['was_redirected']
            
            # Убираем INFO лог редиректа - он дублируется в консоли
            # if was_redirected:
            #     logger.info(f"Парсинг с редиректом: {url} -> {final_url}")
            
            # Проверяем, что получили HTML контент
            if not html or len(html.strip()) < 100:
                return {'url': url, 'error': f'Получен слишком короткий контент: {len(html)} символов'}
            
            # Проверяем, что это действительно HTML
            if not any(tag in html.lower() for tag in ['<html', '<body', '<div', '<p']):
                return {'url': url, 'error': 'Получен не HTML контент'}
            
            # Извлекаем различные элементы контента
            result = {
                'url': url,
                'title': None,
                'text': None,
                'language': None,
                'date': None,
                'author': None,
                'description': None,
                'comments': None
            }
            
            try:
                # Основной текст
                result['text'] = trafilatura.extract(
                    html, 
                    config=self.trafilatura_config,
                    include_formatting=False,
                    include_links=False,
                    include_images=False,
                    include_tables=True
                )
                
                # Метаданные
                try:
                    metadata = trafilatura.extract_metadata(html)

                    if metadata:
                        result['title'] = metadata.title
                        result['author'] = metadata.author
                        result['date'] = str(metadata.date) if metadata.date else None
                        result['description'] = metadata.description
                        result['language'] = metadata.language

                except Exception as meta_error:
                    title_match = re.search(r'<title[^>]*>(.*?)</title>', html, re.IGNORECASE | re.DOTALL)

                    if title_match:
                        result['title'] = title_match.group(1).strip()
                
                try:
                    # trafilatura не имеет функции extract_comments
                    # Используем стандартную функцию extract с параметром include_comments
                    comments_html = trafilatura.extract(
                        html, 
                        config=self.trafilatura_config,
                        include_comments=True,
                        include_formatting=False,
                        include_links=False
                    )
                    # Если включить комментарии, они будут в основном тексте
                    # Для отдельного извлечения комментариев нужно использовать другой подход
                    result['comments'] = None  # Пока отключаем комментарии

                except Exception as comments_error:
                    result['comments'] = None
            
            except Exception as e:
                # Убираем лог ошибки trafilatura
                # Fallback к базовому извлечению
                result['text'] = self._basic_text_extraction(html, url)
                
                # Попытка извлечь заголовок через регулярные выражения
                title_match = re.search(r'<title[^>]*>(.*?)</title>', html, re.IGNORECASE | re.DOTALL)

                if title_match:
                    result['title'] = title_match.group(1).strip()
            
            return result
            
        except Exception as e:
            error_msg = f"{type(e).__name__}: {str(e)}"
            return {'url': url, 'error': error_msg, 'success': False}


class URLContentExtractionError(Exception):
    """Кастомное исключение для ошибок извлечения контента"""
    pass


# Создаем глобальный экземпляр экстрактора
url_extractor = URLContentExtractor()


# Обратная совместимость - оставляем старую функцию
async def get_url_text(url: str) -> str:
    """
    Функция обратной совместимости
    
    Args:
        url: URL для парсинга
        
    Returns:
        str: Извлеченный текст или пустая строка при ошибке
    """
    try:
        return await url_extractor.get_url_text(url)
    except URLContentExtractionError:
        return ""


# Импорты для parse_initial_urls
import traceback
from sqlalchemy import select
from main.database_connection import AsyncSessionLocal
from parser.models.source_urls import SourceURL
from parser.models.source_content import SourceContent
from parser.constants import INITIAL_URLS


async def check_redirect(url: str, timeout: int = 10) -> tuple[str, bool]:
    """
    Проверяет, происходит ли редирект для данного URL
    
    Args:
        url: URL для проверки
        timeout: Таймаут запроса в секундах
        
    Returns:
        tuple: (final_url, was_redirected)
    """
    try:
        response = await HTTPClientFactory.make_request(
            url=url,
            timeout=timeout,
            follow_redirects=True
        )
        final_url = str(response.url)
        was_redirected = final_url != url
        
        # Убираем INFO лог редиректа - он дублируется в консоли
        # if was_redirected:
        #     logger.info(f"Обнаружен редирект: {url} -> {final_url}")
        
        return final_url, was_redirected
    except Exception as e:
        error_type = type(e).__name__
        logger.warning(f"Ошибка проверки редиректа для {url}: {error_type}: {e}")
        return url, False


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception_type((httpx.ConnectError, httpx.TimeoutException, httpx.RequestError, httpx.HTTPStatusError)),
    before_sleep=before_sleep_log(logger, logging.WARNING)
)
async def unified_url_request(url: str, timeout: int = 10, max_retries: int = 3) -> dict:
    """
    Унифицированный метод для выполнения HTTP-запросов с retry-логикой
    
    Args:
        url: URL для запроса
        timeout: Таймаут запроса
        max_retries: Максимальное количество попыток (используется для совместимости, фактически контролируется декоратором)
        
    Returns:
        dict: Результат запроса с информацией об успехе/ошибке
    """
    try:
        response = await HTTPClientFactory.make_request(
            url=url,
            timeout=timeout,
            follow_redirects=True
        )
        
        return {
            'success': True,
            'response': response,
            'final_url': str(response.url),
            'was_redirected': str(response.url) != url,
            'status_code': response.status_code,
            'attempt': 1  # Декоратор управляет попытками
        }
        
    except httpx.HTTPStatusError as e:
        error_msg = f"HTTP {e.response.status_code}"
        logger.warning(f"HTTP ошибка для {url}: {error_msg}")
        
        # Не повторяем для некоторых кодов ошибок
        if e.response.status_code in [404, 403, 401, 410]:
            return {
                'success': False,
                'error': error_msg,
                'error_type': 'HTTPStatusError',
                'final_attempt': True,
                'attempt': 1
            }
        # Для других HTTP ошибок позволяем retry декоратору повторить
        raise
        
    except (httpx.ConnectError, httpx.TimeoutException, httpx.RequestError) as e:
        # Логируем попытку для отладки retry
        logger.debug(f"Сетевая ошибка в unified_url_request для {url}: {type(e).__name__}: {str(e)}")
        # Позволяем retry декоратору повторить для сетевых ошибок
        raise
        
    except Exception as e:
        error_type = type(e).__name__
        error_msg = f"{error_type}: {str(e)}"
        logger.error(f"Неожиданная ошибка для {url}: {error_msg}")
        # Для неожиданных ошибок также позволяем повторить
        raise


async def parse_initial_urls():
    """
    Инициализирует базу данных начальными URL-адресами кейсов EORA.
    
    Функция проверяет список предопределенных URL и добавляет в базу данных
    только те, которых там еще нет. Также проверяет наличие спарсенного контента
    и перезапускает парсинг для URL без контента. Сам парсинг контента запускается 
    автоматически через события SQLAlchemy после создания записи SourceURL в отдельном фоновом потоке.
    
    Обновления:
    - Обрабатывает редиректы 301 и сохраняет финальный URL
    - Предотвращает дублирование парсинга для редиректов
    
    Процесс:
    1. Проверяет существование каждого URL в базе данных
    2. Для новых URL проверяет редиректы
    3. Для существующих URL проверяет наличие связанного контента
    4. Создает записи SourceURL для новых URL с автогенерированными названиями
    5. Запускает повторный парсинг для URL без контента
    6. Сохраняет записи в БД через транзакции
    7. Фактический парсинг HTML-контента происходит асинхронно через события модели
        
    Raises:
        Exception: При ошибках работы с базой данных (с автоматическим rollback)
    """
    print("🚀 Парсинг начался", flush=True)
    
    try:
        async with AsyncSessionLocal() as session:
            
            # Проверяем, какие URL уже есть в базе и их контент
            existing_urls = []
            new_urls = []
            urls_without_content = []
            
            for i, url in enumerate(INITIAL_URLS, 1):
                # Проверяем редирект для URL с тем же таймаутом, что используется для парсинга
                final_url, was_redirected = await check_redirect(url, timeout=10)
                
                # Проверяем, есть ли уже такой URL в базе (оригинальный или финальный)
                result = await session.execute(
                    select(SourceURL).where(
                        (SourceURL.url == url) | 
                        (SourceURL.redirected_url == final_url) |
                        (SourceURL.url == final_url)
                    )
                )
                existing_records = result.all()
                
                # Если найдено несколько записей, берем первую
                existing = existing_records[0][0] if existing_records else None
                
                if existing:
                    existing_urls.append(url)
                    
                    # Обновляем информацию о редиректе, если она изменилась
                    if was_redirected and existing.redirected_url != final_url:
                        existing.redirected_url = final_url
                        await session.commit()
                        print(f"   🔄 Обновлен редирект для {url} -> {final_url}", flush=True)
                    
                    # Проверяем, есть ли связанный контент
                    content_result = await session.execute(
                        select(SourceContent).where(SourceContent.source_url_id == existing.id)
                    )
                    existing_content = content_result.scalar_one_or_none()
                    
                    if not existing_content:
                        urls_without_content.append({
                            'url': url,
                            'source_url': existing,
                            'reason': 'no_content'
                        })

                else:
                    new_urls.append({
                        'original_url': url,
                        'final_url': final_url,
                        'was_redirected': was_redirected
                    })
            
            if new_urls:                
                for i, url_info in enumerate(new_urls, 1):
                    try:
                        original_url = url_info['original_url']
                        final_url = url_info['final_url']
                        was_redirected = url_info['was_redirected']
                        
                        # Генерируем название на основе оригинального URL
                        title = original_url.split('/')[-1].replace('-', ' ').title()

                        if not title:
                            title = f"EORA Case {i}"
                        
                        # Создаем новую запись
                        new_source = SourceURL(
                            url=original_url,
                            title=title,
                            description=f"Автоматически добавленный кейс EORA: {title}",
                            redirected_url=final_url if was_redirected else None
                        )
                        
                        session.add(new_source)
                        await session.commit()
                        await session.refresh(new_source)

                        if was_redirected:
                            print(f"   ✅ Добавлен с редиректом: {original_url} -> {final_url}", flush=True)
                        else:
                            print(f"   ✅ Добавлен: {original_url}", flush=True)

                    except Exception as e:
                        await session.rollback()
                        print(f"   ❌ Ошибка добавления {original_url}: {e}", flush=True)
                        continue
            
            # Обрабатываем URL без контента (перезапускаем парсинг)
            if urls_without_content:
                
                for url_info in urls_without_content:

                    try:
                        source_url = url_info['source_url']
                        source_url.description = f"Перезапуск парсинга: {source_url.description}"
                        
                        await session.commit()
                        await session.refresh(source_url)
                        
                        
                    except Exception as e:
                        await session.rollback()
                        print(f"   ❌ Ошибка перезапуска {url_info['url']}: {e}", flush=True)
                        continue
            
            # Итоговое сообщение
            if not new_urls and not urls_without_content:
                print(
                    "\n✅ Все URL уже существуют и имеют контент. Парсинг не требуется.",
                    flush=True
                )
            else:
                actions = []
                if new_urls:
                    actions.append(f"добавлено {len(new_urls)} новых URL")
                if urls_without_content:
                    actions.append(f"перезапущен парсинг для {len(urls_without_content)} URL")

                print(f"\n✅ Завершено: {', '.join(actions)}", flush=True)

    except Exception as e:
        print(f"❌ Критическая ошибка в parse_initial_urls: {e}", flush=True)
        traceback.print_exc()
        # Пробрасываем исключение чтобы lifespan мог его обработать
        raise
    
    print("\n🏁 ПАРСИНГ НАЧАЛЬНЫХ URL ЗАВЕРШЕН", flush=True)