import os
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlmodel import Session

from app.api.auth import router as auth_router
from app.api.calendar import router as calendar_router
from app.api.cases import router as cases_router
from app.api.inbox import router as inbox_router
from app.api.reminders import router as reminders_router
from app.api.rules import router as rules_router
from app.api.users import router as users_router
from app.api.webhooks import router as webhooks_router
from app.config import get_settings
from app.core.seed import bootstrap_database
from app.database import engine, get_session
from app.services.reminder_scheduler import evaluate_due_reminders

settings = get_settings()


def _scheduled_reminder_job() -> None:
    with Session(engine) as session:
        evaluate_due_reminders(session)


@asynccontextmanager
async def lifespan(app: FastAPI):
    runtime_settings = get_settings()
    if runtime_settings.app_env.strip().lower() != "development" and runtime_settings.is_default_jwt_secret():
        raise RuntimeError(
            "Refusing to start in non-development environment with default or weak JWT_SECRET_KEY."
        )
    runtime_settings.cases_root_path.mkdir(parents=True, exist_ok=True)
    with Session(engine) as session:
        bootstrap_database(session)

    scheduler: AsyncIOScheduler | None = None
    if runtime_settings.enable_reminder_scheduler and not os.environ.get("PYTEST_CURRENT_TEST"):
        scheduler = AsyncIOScheduler(timezone=runtime_settings.timezone)
        scheduler.add_job(
            _scheduled_reminder_job,
            trigger="interval",
            seconds=max(15, runtime_settings.reminder_poll_interval_seconds),
            id="lawteam_reminder_sweep",
            replace_existing=True,
            max_instances=1,
        )
        scheduler.start()

    try:
        yield
    finally:
        if scheduler is not None and scheduler.running:
            scheduler.shutdown(wait=False)


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(users_router)
app.include_router(rules_router)
app.include_router(cases_router)
app.include_router(calendar_router)
app.include_router(webhooks_router)
app.include_router(inbox_router)
app.include_router(reminders_router)


@app.get("/api/health")
def health_check(session: Session = Depends(get_session)) -> dict[str, str]:
    session.exec(text("SELECT 1"))
    return {
        "status": "ok",
        "database": "ok",
        "env": settings.app_env,
        "timezone": settings.timezone,
        "llm_provider": settings.llm_provider,
        "court_auto_fetch_mode": settings.court_auto_fetch_mode,
    }
