#!/usr/bin/env python3
"""
Настройка тестовой базы данных (SQLite) для изоляции тестов
"""

import asyncio
import os
import sys
import tempfile
from pathlib import Path

# Добавляем путь к приложению (поднимаемся на уровень выше из tests/)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import text

# Импортируем модели для создания таблиц
from main.base_model import Base


class TestDatabaseConfig:
    """Конфигурация тестовой базы данных"""
    
    def __init__(self, use_memory=True):
        """
        Args:
            use_memory: Если True, использует in-memory SQLite (быстрее)
                       Если False, создает временный файл (сохраняется между тестами)
        """
        self.use_memory = use_memory
        self.engine = None
        self.session_factory = None
        
        if use_memory:
            # In-memory SQLite (самый быстрый, данные исчезают при закрытии)
            self.database_url = "sqlite+aiosqlite:///:memory:"
            self.temp_file = None
        else:
            # Файловая SQLite в временной директории
            self.temp_file = tempfile.NamedTemporaryFile(
                suffix='.db', 
                delete=False,
                prefix='eora_test_'
            )
            self.database_url = f"sqlite+aiosqlite:///{self.temp_file.name}"
    
    async def create_engine(self):
        """Создает асинхронный движок SQLite"""
        print(f"🔧 Создание тестового движка SQLite: {self.database_url}")
        
        self.engine = create_async_engine(
            self.database_url,
            echo=False,  # Отключаем SQL логи в тестах
            future=True,
            # SQLite специфичные настройки
            connect_args={
                "check_same_thread": False,  # Для SQLite + asyncio
            } if not self.use_memory else {}
        )
        
        # Создаем фабрику сессий
        self.session_factory = sessionmaker(
            bind=self.engine,
            class_=AsyncSession,
            expire_on_commit=False
        )
        
        return self.engine
    
    async def create_tables(self):
        """Создает все таблицы в тестовой БД"""
        if not self.engine:
            await self.create_engine()
            
        print("🏗️ Создание тестовых таблиц...")
        
        async with self.engine.begin() as conn:
            # Создаем все таблицы из моделей
            await conn.run_sync(Base.metadata.create_all)
            
        print("✅ Тестовые таблицы созданы!")
    
    async def test_connection(self):
        """Тестирует подключение к БД"""
        try:
            async with self.engine.begin() as conn:
                result = await conn.execute(text("SELECT 1 as test"))
                row = result.fetchone()
                
            print(f"✅ Подключение к SQLite успешно! Результат: {row}")
            return True
            
        except Exception as e:
            print(f"❌ Ошибка подключения к SQLite: {e}")
            return False
    
    def get_session(self):
        """Возвращает новую асинхронную сессию"""
        if not self.session_factory:
            raise RuntimeError("Engine не инициализирован. Вызовите create_engine() сначала.")
            
        return self.session_factory()
    
    async def cleanup(self):
        """Очищает ресурсы"""
        if self.engine:
            # Удаляем все таблицы
            async with self.engine.begin() as conn:
                await conn.run_sync(Base.metadata.drop_all)
            
            await self.engine.dispose()
            print("🧹 Тестовая БД очищена!")
        
        # Удаляем временный файл если использовался
        if self.temp_file and os.path.exists(self.temp_file.name):
            os.unlink(self.temp_file.name)
            print(f"🗑️ Удален временный файл: {self.temp_file.name}")


# Глобальная конфигурация для тестов
_test_db_config = None


async def get_test_database_config(use_memory=True) -> TestDatabaseConfig:
    """
    Получает или создает конфигурацию тестовой БД
    
    Args:
        use_memory: Использовать in-memory SQLite (рекомендуется для тестов)
    
    Returns:
        TestDatabaseConfig: Конфигурация тестовой БД
    """
    global _test_db_config
    
    if _test_db_config is None:
        _test_db_config = TestDatabaseConfig(use_memory=use_memory)
        await _test_db_config.create_engine()
        await _test_db_config.create_tables()
        
        # Проверяем работоспособность
        success = await _test_db_config.test_connection()
        if not success:
            raise RuntimeError("Не удалось создать тестовую БД SQLite")
    
    return _test_db_config


async def cleanup_test_database():
    """Очищает тестовую БД (вызывать в конце тестов)"""
    global _test_db_config
    
    if _test_db_config:
        await _test_db_config.cleanup()
        _test_db_config = None


# Функция для использования в conftest.py
def get_test_database_url(use_memory=True) -> str:
    """Возвращает URL тестовой БД для использования в conftest"""
    if use_memory:
        return "sqlite+aiosqlite:///:memory:"
    else:
        temp_file = tempfile.NamedTemporaryFile(
            suffix='.db', 
            delete=False,
            prefix='eora_test_'
        )
        return f"sqlite+aiosqlite:///{temp_file.name}"


if __name__ == "__main__":
    print("Тесты базы данных доступны через pytest")
