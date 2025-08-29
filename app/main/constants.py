# Python imports
import os

SECRET_KEY = os.getenv("SECRET_KEY")

POSTGRES_USER = os.getenv("POSTGRES_USER", "")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "")
POSTGRES_DB_HOST = os.getenv("POSTGRES_DB_HOST", "")
POSTGRES_DB_PORT = os.getenv("POSTGRES_DB_PORT", "")
DB_NAME = os.getenv("DB_NAME", "")

TIMEOUT = 120
INTERVAL = 10