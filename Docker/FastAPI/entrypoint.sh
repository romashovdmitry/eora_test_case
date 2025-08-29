#!/bin/sh

echo ""
echo "🔍 Checking for migrations directory..."

# Если папки migrations нет, создаем её и инициализируем Alembic
if [ ! -d "migrations" ]; then
    echo "❌ Migrations directory not found"
    echo "📦 Initializing Alembic..."
    alembic init migrations
    echo "✅ Alembic initialization complete"
else
    echo "✅ Migrations directory already exists"
    echo "📋 Contents of migrations directory:"
    ls -la migrations/
fi

echo ""
echo "🔧 Checking alembic.ini configuration..."
if [ -f "alembic.ini" ]; then
    echo "✅ alembic.ini found"
    echo "📍 Script location in alembic.ini:"
    grep "script_location" alembic.ini
else
    echo "❌ alembic.ini not found!"
fi

echo ""
echo "🔄 Generating new migration (if needed)..."

# Создаем новую миграцию, если есть изменения в моделях (опционально, но полезно)
if alembic revision --autogenerate -m "Auto migration"; then
    echo "✅ New migration created successfully"
else
    echo "ℹ️  No new migrations needed or migration failed (continuing anyway)"
fi

echo ""
echo "⬆️  Applying database migrations..."

# Применение миграций
if alembic upgrade head; then
    echo "✅ Database migrations applied successfully"
else
    echo "❌ Failed to apply migrations"
    exit 1
fi

echo ""
echo "🌐 Starting FastAPI server..."
echo ""

# Запуск приложения
uvicorn main.app:app --host 0.0.0.0 --port 8000 --reload --proxy-headers --forwarded-allow-ips "*" --reload-dir /app
