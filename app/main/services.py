"""
This module contains utility functions and classes that have a broad scope and
may be used across multiple modules in the FastAPI application.
"""
# Python imports
import os

# FastAPI imports
from starlette.requests import Request

# SQLAlchemy, sqladmin imports
from sqladmin.authentication import AuthenticationBackend

ADMIN_USERNAME = os.getenv("SUPERUSER_USERNAME")
ADMIN_PASSWORD = os.getenv("SUPERUSER_PASSWORD")


# https://aminalaee.dev/sqladmin/authentication/
class AdminAuth(AuthenticationBackend):
    """
    Admin authentication backend for SQLAdmin.
    """
    async def login(self, request: Request) -> bool:
        form = await request.form()
        username, password = form["username"], form["password"]

        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:

            request.session.update({"token": "..."})

            return True

        return False

    async def logout(self, request) -> bool:
        # Usually you'd want to just clear the session
        request.session.clear()

        return True

    async def authenticate(self, request: Request) -> bool:
        token = request.session.get("token")

        if not token:

            return False

        return True




