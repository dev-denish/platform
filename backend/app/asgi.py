"""ASGI entrypoint for production servers (gunicorn/uvicorn).

Kept separate from app.main so that importing create_app() in tests never triggers
app construction / settings loading as an import side effect. Servers target
`app.asgi:app`.
"""
from app.main import create_app

app = create_app()
