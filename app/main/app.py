# Python imports
import asyncio
import logging
from contextlib import asynccontextmanager

# FastAPI imports:
from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware

# SQLAlchemy imports
from sqladmin import Admin

# import constants
from main.constants import TIMEOUT, INTERVAL

# Local imports
from main.admin import configure_admin
from main.constants import SECRET_KEY
from main.database_connection import engine
from main.services import AdminAuth
from parser.services import parse_initial_urls
from rag.routes import rag_router as rag_api_router
from rag.rag_system import initialize_rag_system, rag_system
from main.logger import logger
# Отключаем подробное логирование HTTP запросов от httpx
logging.getLogger("httpx").setLevel(logging.WARNING)



@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan события для FastAPI
    """
    try:
        WAITED = 0
        print("🚀 FastAPI приложение запускается...", flush=True)    
        
        print("📊 Запускаю парсинг начальных URL", flush=True)
        await parse_initial_urls()
        
        print("📊 Запуск RAG-система...", flush=True)
        global rag_system

        if rag_system is None:
            rag_system = await initialize_rag_system()
        await rag_system._ensure_initialized()
        print("📊 Ожидание готовности RAG-системы...", flush=True)    

        while True:
            stats = rag_system.get_statistics() if rag_system else {}

            if stats.get("index_ready", False):
                break

            if WAITED >= TIMEOUT:
                raise RuntimeError("RAG-система не готова: превышено время ожидания.")
            
            print(f"⏳ RAG-система не готова, жду... {WAITED}/{TIMEOUT} сек.", flush=True)
            await asyncio.sleep(INTERVAL)
            WAITED += INTERVAL

        print('✅ RAG-система готова к работе', flush=True)
        print("✅ FastAPI приложение готово к работе!", flush=True)

        yield
        
    except Exception as e:
        print(f"❌ Критическая ошибка при запуске приложения: {e}", flush=True)
        logger.error(f"Lifespan startup error: {e}")
        # Поднимаем исключение чтобы FastAPI не запустился
        raise RuntimeError(f"Не удалось запустить приложение: {e}") from e


app = FastAPI(
    lifespan=lifespan,
    swagger_ui_parameters={"syntaxHighlight": False},
    title="EORA App Swagger REST API",
    description="Fast-API backend for EORA system",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(
    TrustedHostMiddleware, allowed_hosts=["*"]
)

admin = Admin(
    app,
    engine,
    authentication_backend=AdminAuth(secret_key=SECRET_KEY)
)

configure_admin(admin)

app.include_router(rag_api_router)
