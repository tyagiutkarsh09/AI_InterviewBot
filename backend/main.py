import os
import logging
from contextlib import asynccontextmanager
from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from src.lib.settings import get_settings
from src.routes.health import router as health_router
from src.routes.interview import router as interview_router
from src.routes.voice_ws import router as voice_ws_router
from src.routes.voice_api import router as voice_api_router

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logger.info("Starting AI Interview Bot API (env=%s)", settings.environment)
    if not settings.anthropic_api_key:
        logger.warning("ANTHROPIC_API_KEY is not set — LLM calls will fail")
    yield
    logger.info("Shutting down")


settings = get_settings()

app = FastAPI(
    title="AI Interview Bot",
    version="1.0.0",
    description="Conducts AI-powered job interviews with LLM evaluation",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router)
app.include_router(interview_router, prefix="/api/v1")
app.include_router(voice_ws_router)
app.include_router(voice_api_router, prefix="/api/v1")
