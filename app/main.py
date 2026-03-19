"""FastAPI 애플리케이션 진입점."""

from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import settings
from app.database import engine
from app.models import Base


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """앱 시작/종료 시 리소스 관리."""
    # 개발용: DB 테이블 자동 생성
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # 업로드 디렉토리 보장
    Path(settings.UPLOAD_DIR).mkdir(parents=True, exist_ok=True)

    yield

    await engine.dispose()


app = FastAPI(
    title="toctoc — 영수증 정리왕",
    version="0.1.0",
    lifespan=lifespan,
)

# ── CORS ─────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8000", "http://127.0.0.1:8000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Static / Templates ───────────────────────────────
app.mount("/static", StaticFiles(directory="static"), name="static")

templates = Jinja2Templates(directory="app/templates")

# ── Routers ──────────────────────────────────────────
# A4가 구현 후 주석 해제
# from app.routers import receipts, stats
# app.include_router(receipts.router)
# app.include_router(stats.router)


@app.get("/health")
async def health_check() -> dict[str, str]:
    """헬스체크 엔드포인트."""
    return {"status": "ok"}
