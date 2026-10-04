import uvicorn

from .config import API_HOST, API_PORT


if __name__ == "__main__":
    uvicorn.run(
        "app.api:app",
        host=API_HOST,
        port=API_PORT,
        log_level="info",
    )
