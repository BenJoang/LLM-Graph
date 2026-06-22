from fastapi import FastAPI

from src.api.routes.chat import router as chat_router
from src.api.routes.health import router as health_router



app = FastAPI(title="Local LLM API")

app.include_router(health_router)
app.include_router(chat_router)
