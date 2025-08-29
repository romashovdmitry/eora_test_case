"""
Конфигурация и фикстуры для тестов FastAPI приложения
"""

import asyncio
import os
import sys
import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from unittest.mock import AsyncMock, MagicMock, patch

# Импорты проекта
from main.app import app
from main.database_connection import AsyncSessionLocal, get_async_session
from main.base_model import Base
from parser.models.source_urls import SourceURL
from parser.models.source_content import SourceContent
from rag.rag_system import RAGSystem


# =============================================================================
# ПРОВЕРКА БАЗЫ ДАННЫХ ПЕРЕД ЗАПУСКОМ ТЕСТОВ
# =============================================================================

def pytest_configure(config):
    """
    Вызывается при инициализации pytest.
    Проверяем подключение к БД перед запуском любых тестов.
    """
    print("\n" + "="*60)
    print("🧪 ИНИЦИАЛИЗАЦИЯ ТЕСТОВОЙ СРЕДЫ EORA")
    print("="*60)
    
    # Проверяем подключение к БД
    db_status = _check_database_connection()
    
    if not db_status:
        print("\n" + "❌" * 20)
        print("🚫 ТЕСТЫ ОТМЕНЕНЫ: Не удалось подключиться к тестовой БД")
        print("💡 Проверьте конфигурацию базы данных и повторите попытку")
        print("❌" * 20)
        pytest.exit("База данных недоступна", returncode=1)
    
    print("✅ Тестовая БД готова к работе!")
    print("🚀 Запуск тестов...\n")


def _check_database_connection() -> bool:
    """
    Синхронная проверка подключения к тестовой БД
    """
    try:
        # Добавляем родительскую директорию в путь для импорта
        import sys
        import os
        parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if parent_dir not in sys.path:
            sys.path.insert(0, parent_dir)
        
        # Импортируем настройки тестовой БД
        from .test_db_connection import get_test_database_config
        
        # Запускаем асинхронную проверку
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        try:
            result = loop.run_until_complete(_async_check_database())
            return result
        finally:
            loop.close()
            
    except Exception as e:
        print(f"❌ Ошибка проверки БД: {e}")
        import traceback
        traceback.print_exc()
        return False


async def _async_check_database() -> bool:
    """
    Асинхронная проверка подключения к тестовой БД
    """
    try:
        # Добавляем родительскую директорию в путь для импорта
        import sys
        import os
        parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if parent_dir not in sys.path:
            sys.path.insert(0, parent_dir)
            
        from .test_db_connection import get_test_database_config, cleanup_test_database
        
        print("🔧 Проверка подключения к SQLite тестовой БД...")
        
        # Создаем тестовую конфигурацию
        config = await get_test_database_config(use_memory=True)
        
        # Тестируем подключение
        success = await config.test_connection()
        
        if success:
            print("✅ SQLite тестовая БД работает корректно")
        
        # Очищаем тестовые ресурсы
        await cleanup_test_database()
        
        return success
        
    except Exception as e:
        print(f"❌ Ошибка подключения к тестовой БД: {e}")
        import traceback
        traceback.print_exc()
        return False


def pytest_sessionstart(session):
    """Вызывается в начале тестовой сессии"""
    print("📋 Конфигурация тестовой сессии:")
    
    # Настройка путей для импорта
    import sys
    import os
    parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if parent_dir not in sys.path:
        sys.path.insert(0, parent_dir)
        
    from .test_db_connection import get_test_database_url
    test_url = get_test_database_url(use_memory=True)
    print(f"   🗃️  Тестовая БД: {test_url}")
    print(f"   🐍 Python: {sys.version.split()[0]}")
    print(f"   📦 Pytest: {pytest.__version__}")


def pytest_sessionfinish(session, exitstatus):
    """Вызывается в конце тестовой сессии"""
    if exitstatus == 0:
        print("\n🎉 Все тесты завершены успешно!")
    else:
        print(f"\n⚠️ Тесты завершены с кодом: {exitstatus}")
    
    print("🧹 Очистка тестовых ресурсов...")


# =============================================================================
# ТЕСТОВЫЕ КОНСТАНТЫ И НАСТРОЙКИ
# =============================================================================

# Настройка путей для импорта
import sys
import os
parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

# Импортируем настройки тестовой БД
from .test_db_connection import get_test_database_config, cleanup_test_database, get_test_database_url

# Используем SQLite для тестов (быстро и изолированно)
TEST_DATABASE_URL = get_test_database_url(use_memory=True)

# Переопределяем настройки для тестов
os.environ["TESTING"] = "true"


# =============================================================================
# ФИКСТУРЫ ДЛЯ НАСТРОЙКИ ТЕСТОВОЙ СРЕДЫ
# =============================================================================

@pytest.fixture(scope="session")
def event_loop():
    """
    Создает event loop для всей сессии тестирования
    """
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="session")
async def test_engine():
    """
    Создает тестовый асинхронный engine для SQLite БД
    """
    config = None
    
    try:
        print(f"🔧 Инициализация тестовой SQLite БД...")
        
        # Используем нашу конфигурацию SQLite
        config = await get_test_database_config(use_memory=True)
        engine = config.engine
        
        # Дополнительная проверка работоспособности
        success = await config.test_connection()
        if not success:
            raise RuntimeError("Тестовое подключение к SQLite не прошло")
        
        print("✅ Тестовая SQLite БД готова!")
        yield engine
        
    except Exception as e:
        error_msg = f"❌ Критическая ошибка инициализации тестовой БД: {e}"
        print(error_msg)
        
        # Показываем детали ошибки
        import traceback
        print("📋 Детали ошибки:")
        traceback.print_exc()
        
        # Прерываем все тесты
        pytest.exit(error_msg, returncode=1)
        
    finally:
        # Очищаем после тестов
        if config:
            try:
                await cleanup_test_database()
                print("🧹 Тестовая SQLite БД очищена!")
            except Exception as cleanup_error:
                print(f"⚠️ Ошибка очистки БД: {cleanup_error}")


@pytest_asyncio.fixture
async def test_db_session(test_engine):
    """
    Создает тестовую сессию БД с автоматическим rollback
    """
    # Создаем connection в транзакции
    connection = await test_engine.connect()
    transaction = await connection.begin()
    
    # Создаем сессию на основе connection
    SessionLocal = sessionmaker(
        bind=connection,
        class_=AsyncSession,
        expire_on_commit=False
    )
    
    session = SessionLocal()
    
    try:
        yield session
    finally:
        await session.close()
        await transaction.rollback()
        await connection.close()


@pytest_asyncio.fixture
async def test_client(test_db_session):
    """
    Создает тестовый HTTP клиент с переопределением зависимостей
    """
    # Переопределяем зависимость БД для тестов
    async def override_get_async_session():
        yield test_db_session
    
    app.dependency_overrides[get_async_session] = override_get_async_session
    
    # Отключаем lifespan события для тестов (парсинг начальных URL)
    app.router.lifespan_context = None
    
    # Используем правильный API для httpx AsyncClient
    from httpx import ASGITransport
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client
    
    # Очищаем переопределения
    app.dependency_overrides.clear()


# =============================================================================
# ФИКСТУРЫ ДЛЯ ТЕСТОВЫХ ДАННЫХ
# =============================================================================

@pytest_asyncio.fixture
async def sample_source_url(test_db_session):
    """
    Создает тестовую запись SourceURL
    """
    source_url = SourceURL(
        url="https://example.com/test-page",
        title="Test Page",
        description="Test description"
    )
    
    test_db_session.add(source_url)
    await test_db_session.commit()
    await test_db_session.refresh(source_url)
    
    return source_url


@pytest_asyncio.fixture
async def sample_source_content(test_db_session, sample_source_url):
    """
    Создает тестовую запись SourceContent
    """
    content = SourceContent(
        source_url_id=sample_source_url.id,
        content="Test content about EORA chatbots and AI solutions",
        title="Test Content Title",
        description="Test content description",
        extraction_metadata={"test": "metadata"}
    )
    
    test_db_session.add(content)
    await test_db_session.commit()
    await test_db_session.refresh(content)
    
    return content


@pytest_asyncio.fixture
async def mock_rag_system():
    """
    Создает мок RAG системы для изоляции тестов
    """
    # Патчим функцию инициализации в модуле rag_system
    with patch('rag.rag_system.initialize_rag_system') as mock_init_rag:
        mock_rag = AsyncMock(spec=RAGSystem)
        
        # Настраиваем мок для стандартных ответов
        mock_rag.generate_answer.return_value = {
            "answer": "Test answer about EORA chatbots",
            "sources": []
        }
        
        mock_rag.get_statistics.return_value = {
            "total_chunks": 10,
            "total_sources": 2,
            "embedding_model": "test-model"
        }
        
        mock_init_rag.return_value = mock_rag
        yield mock_rag


@pytest_asyncio.fixture
async def mock_rag_system_for_api():
    """
    Специальный мок для API тестов - патчит в rag.rag_system модуле
    """
    with patch('rag.rag_system.initialize_rag_system') as mock_init_rag:
        mock_rag = AsyncMock(spec=RAGSystem)
        
        # Настраиваем мок для стандартных ответов
        mock_rag.generate_answer.return_value = {
            "answer": "Test answer about EORA chatbots",
            "sources": []
        }
        
        mock_rag.get_statistics.return_value = {
            "total_chunks": 10,
            "total_sources": 2,
            "embedding_model": "test-model"
        }
        
        mock_init_rag.return_value = mock_rag
        # Также мокаем глобальную переменную как None
        with patch('rag.rag_system.rag_system', None):
            yield mock_rag


@pytest_asyncio.fixture
async def mock_rag_system_error():
    """
    Мок RAG системы для тестирования ошибок
    """
    with patch('rag.rag_system.initialize_rag_system') as mock_init_rag:
        mock_rag = AsyncMock(spec=RAGSystem)
        
        # Настраиваем мок для генерации ошибок
        mock_rag.generate_answer.side_effect = Exception("Test error")
        mock_rag.get_statistics.side_effect = Exception("Test error")
        
        mock_init_rag.return_value = mock_rag
        yield mock_rag


# =============================================================================
# ПАРАМЕТРИЗОВАННЫЕ ДАННЫЕ ДЛЯ ТЕСТОВ
# =============================================================================

@pytest.fixture(params=[
    "Что умеет чат-бот EORA?",
    "Как внедрить AI решения?",
    "Расскажи о проектах компании",
])
def valid_queries(request):
    """Параметризованные валидные запросы"""
    return request.param


@pytest.fixture(params=[
    "",  # Пустой запрос
    "a",  # Слишком короткий
    "Вечера или прохладно в 17:99?",  # Некорректное время
    "aaaaaaaaaaaaaaaa",  # Спам
])
def invalid_queries(request):
    """Параметризованные невалидные запросы"""
    return request.param


# =============================================================================
# УТИЛИТЫ ДЛЯ ТЕСТОВ
# =============================================================================

class TestDataFactory:
    """
    Фабрика для создания тестовых данных
    """
    
    @staticmethod
    async def create_source_url(session: AsyncSession, **kwargs) -> SourceURL:
        """Создает тестовый SourceURL"""
        defaults = {
            "url": "https://example.com/test",
            "title": "Test URL",
            "description": "Test description"
        }
        defaults.update(kwargs)
        
        source_url = SourceURL(**defaults)
        session.add(source_url)
        await session.commit()
        await session.refresh(source_url)
        return source_url
    
    @staticmethod
    async def create_source_content(
        session: AsyncSession, 
        source_url_id: int, 
        **kwargs
    ) -> SourceContent:
        """Создает тестовый SourceContent"""
        defaults = {
            "content": "Test content about EORA",
            "title": "Test Content",
            "description": "Test description",
            "extraction_metadata": {}
        }
        defaults.update(kwargs)
        
        content = SourceContent(source_url_id=source_url_id, **defaults)
        session.add(content)
        await session.commit()
        await session.refresh(content)
        return content


# =============================================================================
# ХЕЛПЕРЫ ДЛЯ ПРОВЕРОК
# =============================================================================

def assert_valid_response(response_data: dict, expected_keys: list = None):
    """
    Проверяет базовую структуру ответа API
    """
    if expected_keys is None:
        expected_keys = ["answer"]
    
    assert isinstance(response_data, dict)
    for key in expected_keys:
        assert key in response_data
        assert response_data[key] is not None


def assert_error_response(response_data: dict, expected_status: str = None):
    """
    Проверяет структуру ошибочного ответа
    """
    assert isinstance(response_data, dict)
    assert "detail" in response_data or "message" in response_data
    
    if expected_status:
        assert response_data.get("status") == expected_status
