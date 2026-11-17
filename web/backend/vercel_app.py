"""Vercel ASGI entrypoint; service workers start only in the app lifespan."""
from web.backend.app import create_app

app = create_app()
