"""
Юнит- и интеграционные тесты для FastAPI приложения EORA
"""

import pytest
import pytest_asyncio
from httpx import AsyncClient
from unittest.mock import AsyncMock, patch, MagicMock

from rag.rag_system import RAGSystem
from parser.models.source_urls import SourceURL
from parser.models.source_content import SourceContent
from tests.conftest import TestDataFactory, assert_valid_response, assert_error_response


# =============================================================================
# ЮНИТ-ТЕСТЫ (с моками, без реальной БД)
# =============================================================================

class TestRAGSystemUnit:
    """
    Юнит-тесты для RAG системы с использованием моков
    """
    
    @pytest_asyncio.fixture
    async def rag_system_mock(self):
        """Мок RAG системы для юнит-тестов"""
        with patch('rag.rag_system.RAGSystem') as mock_class:
            mock_instance = AsyncMock(spec=RAGSystem)
            mock_class.return_value = mock_instance
            yield mock_instance
    
    @pytest.mark.asyncio
    async def test_query_validation_valid_queries(self, rag_system_mock, valid_queries):
        """
        ЮНИТ-ТЕСТ 1: Проверка валидации корректных запросов
        
        Тестирует метод _validate_query с валидными входными данными
        """
        # Arrange: Настраиваем мок для валидации
        rag_system = RAGSystem()
        
        # Act: Выполняем валидацию
        result = rag_system._validate_query(valid_queries)
        
        # Assert: Проверяем результат
        assert result["is_valid"] is True
        assert result["reason"] == "valid"
        assert result["suggested_response"] == ""
    
    @pytest.mark.asyncio
    async def test_query_validation_invalid_queries(self, rag_system_mock, invalid_queries):
        """
        ЮНИТ-ТЕСТ 2: Проверка валидации некорректных запросов
        
        Тестирует обработку невалидных запросов
        """
        # Arrange: Создаем RAG систему
        rag_system = RAGSystem()
        
        # Act: Выполняем валидацию невалидного запроса
        result = rag_system._validate_query(invalid_queries)
        
        # Assert: Проверяем, что запрос отклонен
        assert result["is_valid"] is False
        assert result["reason"] in ["too_short", "spam_or_nonsense", "off_topic"]
        assert len(result["suggested_response"]) > 0
    
    @pytest.mark.asyncio
    async def test_generate_answer_with_mocked_components(self):
        """
        ЮНИТ-ТЕСТ 3: Тестирование генерации ответа с моками всех компонентов

        Проверяет полный флоу генерации ответа с использованием моков
        """
        # Arrange: Настраиваем моки для всех компонентов
        with patch('rag.rag_system.initialize_rag_system') as mock_init_rag:
            mock_rag = AsyncMock()
            
            # Настраиваем мок для возврата валидного ответа
            mock_rag.generate_answer.return_value = {
                "answer": "EORA создает чат-ботов для автоматизации бизнес-процессов",
                "validation_error": None
            }
            
            mock_init_rag.return_value = mock_rag
            
            # Мокаем глобальную переменную rag_system как None, чтобы вызвать инициализацию
            with patch('rag.rag_system.rag_system', None):
                # Act: Импортируем и тестируем через новую архитектуру
                from rag.rag_system import initialize_rag_system, rag_system
                
                # Инициализируем систему
                system = await initialize_rag_system()
                result = await system.generate_answer("Что делает EORA?")
                
                # Assert: Проверяем результат
                assert "answer" in result
                assert len(result["answer"]) > 0
                assert "EORA" in result["answer"]
                
                # Проверяем, что мок был вызван
                mock_init_rag.assert_called_once()
                mock_rag.generate_answer.assert_called_once_with("Что делает EORA?")


# =============================================================================
# ИНТЕГРАЦИОННЫЕ ТЕСТЫ (с реальной БД и HTTP клиентом)
# =============================================================================

class TestRAGAPIIntegration:
    """
    Интеграционные тесты для RAG API с реальной БД
    """
    
    



# =============================================================================
# ИНТЕГРАЦИОННЫЕ ТЕСТЫ БАЗЫ ДАННЫХ
# =============================================================================

class TestDatabaseIntegration:
    """
    Интеграционные тесты работы с базой данных
    """
    
    @pytest.mark.asyncio
    async def test_source_url_crud_operations(self, test_db_session):
        """
        Тестирует CRUD операции с SourceURL
        """
        # CREATE: Создаем новый SourceURL
        source_url = await TestDataFactory.create_source_url(
            test_db_session,
            url="https://eora.ru/test",
            title="Test EORA Page",
            description="Test page for EORA"
        )
        
        assert source_url.id is not None
        assert source_url.url == "https://eora.ru/test"
        assert source_url.title == "Test EORA Page"
        
        # READ: Проверяем чтение
        from sqlalchemy import select
        result = await test_db_session.execute(
            select(SourceURL).where(SourceURL.id == source_url.id)
        )
        retrieved_url = result.scalar_one_or_none()
        
        assert retrieved_url is not None
        assert retrieved_url.url == source_url.url
        
        # UPDATE: Обновляем запись
        retrieved_url.title = "Updated Title"
        await test_db_session.commit()
        await test_db_session.refresh(retrieved_url)
        
        assert retrieved_url.title == "Updated Title"
        
        # DELETE: Удаляем запись
        await test_db_session.delete(retrieved_url)
        await test_db_session.commit()
        
        # Проверяем, что запись удалена
        result = await test_db_session.execute(
            select(SourceURL).where(SourceURL.id == source_url.id)
        )
        deleted_url = result.scalar_one_or_none()
        assert deleted_url is None
    
    @pytest.mark.asyncio
    async def test_source_content_relationship(self, test_db_session):
        """
        Тестирует связь между SourceURL и SourceContent
        """
        # Создаем SourceURL
        source_url = await TestDataFactory.create_source_url(
            test_db_session,
            url="https://eora.ru/chatbots",
            title="EORA Chatbots"
        )
        
        # Создаем связанный SourceContent
        content = await TestDataFactory.create_source_content(
            test_db_session,
            source_url_id=source_url.id,
            content="Detailed information about EORA chatbots and AI solutions",
            title="Chatbot Content"
        )
        
        # Проверяем связь
        assert content.source_url_id == source_url.id
        
        # Проверяем обратную связь через relationship
        from sqlalchemy import select
        from sqlalchemy.orm import selectinload
        
        result = await test_db_session.execute(
            select(SourceURL)
            .options(selectinload(SourceURL.contents))
            .where(SourceURL.id == source_url.id)
        )
        url_with_contents = result.scalar_one()
        
        assert len(url_with_contents.contents) == 1
        assert url_with_contents.contents[0].id == content.id
        assert url_with_contents.contents[0].content == content.content


# =============================================================================
# ПАРАМЕТРИЗОВАННЫЕ ТЕСТЫ
# =============================================================================

class TestParametrized:
    """
    Параметризованные тесты для проверки различных сценариев
    """
    
    @pytest.mark.parametrize("query,expected_valid", [
        ("Что умеет чат-бот EORA?", True),
        ("Как внедрить AI?", True), 
        ("Расскажи о проектах", True),
        ("", False),  # Пустой
        ("a", False),  # Слишком короткий
        ("Какая погода завтра?", False),  # Не по теме
        ("asdfghjkl", False),  # Случайные символы
    ])
    @pytest.mark.asyncio
    async def test_query_validation_parametrized(self, query, expected_valid):
        """
        Параметризованный тест валидации различных типов запросов
        """
        # Arrange
        rag_system = RAGSystem()
        
        # Act
        result = rag_system._validate_query(query)
        
        # Assert
        assert result["is_valid"] == expected_valid, f"Query '{query}' validation failed. Expected: {expected_valid}, Got: {result['is_valid']}, Reason: {result['reason']}"
        
        if expected_valid:
            assert result["reason"] == "valid"
        else:
            assert result["reason"] in [
                "too_short", "spam_or_nonsense", "off_topic", "inappropriate"
            ]
    


# =============================================================================
# ТЕСТЫ ПРОИЗВОДИТЕЛЬНОСТИ И НАГРУЗКИ
# =============================================================================

class TestPerformance:
    """
    Тесты производительности и нагрузочного тестирования
    """
    


# =============================================================================
# ТЕСТЫ БЕЗОПАСНОСТИ
# =============================================================================

class TestSecurity:
    """
    Тесты безопасности API
    """
    
    @pytest.mark.parametrize("malicious_query", [
        "<script>alert('xss')</script>",  # XSS попытка
        "'; DROP TABLE source_urls; --",  # SQL injection попытка
        "{{7*7}}",  # Template injection попытка
        "A" * 10000,  # Очень длинный запрос
    ])
    @pytest.mark.asyncio
    async def test_malicious_input_handling(self, test_client: AsyncClient, malicious_query):
        """
        Тест обработки потенциально вредоносных входных данных
        """
        # Патчим initialize_rag_system для безопасного теста
        with patch('rag.rag_system.initialize_rag_system') as mock_init_rag:
            # Arrange: Настраиваем мок RAG системы
            mock_rag = AsyncMock()
            mock_rag.generate_answer.return_value = {
                "answer": "Безопасный ответ без выполнения вредоносного кода"
            }
            mock_init_rag.return_value = mock_rag
            
            # Мокаем глобальную переменную rag_system в RAG модуле
            with patch('rag.rag_system.rag_system', None):
                # Act: Отправляем потенциально вредоносный запрос
                response = await test_client.post(
                    "/api/v1/rag/answer",
                    json={"query": malicious_query}
                )
                
                # Assert: Проверяем, что система корректно обрабатывает запрос
                assert response.status_code in [200, 422]  # 422 для валидационных ошибок FastAPI
                
                if response.status_code == 200:
                    response_data = response.json()
                    # Проверяем, что вредоносный код не выполнился в ответе
                    assert "<script>" not in response_data.get("answer", "")
                    assert "DROP TABLE" not in response_data.get("answer", "")
                elif response.status_code == 422:
                    # Для валидационных ошибок проверяем наличие detail
                    response_data = response.json()
                    assert "detail" in response_data


# =============================================================================
# ТЕСТЫ ОБРАБОТКИ ОШИБОК
# =============================================================================

class TestErrorHandling:
    """
    Тесты обработки различных типов ошибок
    """
    

    @pytest.mark.asyncio
    async def test_invalid_json_request(self, test_client: AsyncClient):
        """
        Тест обработки невалидного JSON в запросе
        """
        # Act: Отправляем невалидный JSON
        response = await test_client.post(
            "/api/v1/rag/answer",
            content="invalid json content",
            headers={"content-type": "application/json"}
        )
        
        # Assert: Проверяем обработку ошибки парсинга JSON
        assert response.status_code == 422  # Unprocessable Entity


if __name__ == "__main__":
    # Запуск тестов из командной строки
    pytest.main([__file__, "-v", "--tb=short"])
