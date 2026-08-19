from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.routes.chat import router as chat_router
from src.api.routes.gui import router as gui_router
from src.api.routes.health import router as health_router
from src.api.routes.tool_agent import router as tool_agent_router



app = FastAPI(title="Local LLM API")

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=(
        r"^(https?://(127\.0\.0\.1|localhost)(:\d+)?|file://|null)$"
    ),
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router)
app.include_router(chat_router)
app.include_router(tool_agent_router)
app.include_router(gui_router)
