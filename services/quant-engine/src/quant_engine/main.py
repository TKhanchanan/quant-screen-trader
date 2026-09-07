"""ASGI application exposed for Uvicorn and the Electron launcher."""

from quant_engine.app import create_app

app = create_app()
