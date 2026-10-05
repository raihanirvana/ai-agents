"""Importing the API does not open a database or mint credentials."""
from .config import API_PORT, CORS_ORIGINS
from .http.application import create_app
from .http.security import Settings

app = create_app(settings=Settings(port=API_PORT, origins=tuple(CORS_ORIGINS)))
