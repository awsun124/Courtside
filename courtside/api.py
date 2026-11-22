from contextlib import asynccontextmanager, suppress
import asyncio
import os
import sqlite3
from datetime import date as CalendarDate
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .database import database_setting
from .service import NewsService, PROJECT_ROOT, RefreshError
from .schedule import run_schedule
from .storage import read_briefings

DataMode = Literal["live", "sample"]


class RefreshRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: DataMode = "live"
    days: int = Field(default=3, ge=1, le=7)
    max_requests: int = Field(default=3, ge=1, le=5)


def create_app(database=None, env_file=None, settings_file=None, collector=None, clock=None, investigation_file=None, sources_file=None, scheduled=None, predictions_folder=None):
    public_read_only = os.environ.get("COURTSIDE_PUBLIC_READ_ONLY", "false").lower() == "true"
    database_path = database or database_setting()
    origins = [origin.strip() for origin in os.environ.get("COURTSIDE_ALLOWED_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",") if origin.strip()]
    external_schedule = os.environ.get("COURTSIDE_EXTERNAL_SCHEDULE", "false").lower() == "true"
    automatic = scheduled if scheduled is not None else collector is None and clock is None and not external_schedule
    prediction_path = Path(predictions_folder or os.environ.get('COURTSIDE_PREDICTIONS_DIR') or
                           PROJECT_ROOT / 'artifacts/predictions/daily')
    prediction_files=predictions_folder is not None or bool(os.environ.get('COURTSIDE_PREDICTIONS_DIR'))

    @asynccontextmanager
    async def lifespan(app):
        app.state.news = NewsService(database_path,
                                     env_file, settings_file, collector, clock, investigation_file, sources_file)
        app.state.news.daily_schedule = automatic or external_schedule
        task = asyncio.create_task(run_schedule(app.state.news)) if automatic else None
        try:
            yield
        finally:
            if task:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task

    app = FastAPI(title="Court Side", version="0.1.0", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=origins,
                       allow_methods=["GET", "POST"], allow_headers=["Content-Type"])

    @app.middleware("http")
    async def protect_public_writes(request: Request, call_next):
        if public_read_only and request.method not in {"GET", "HEAD", "OPTIONS"}:
            return JSONResponse(status_code=403, content={"detail": "This demo serves saved results only. Collection runs on the server's daily schedule."})
        return await call_next(request)

    @app.exception_handler(sqlite3.Error)
    async def database_error(request: Request, error):
        return JSONResponse(status_code=503, content={"detail": "The database is unavailable. Try again later."})

    @app.get("/health")
    def health(request: Request):
        read_briefings(request.app.state.news.database, "live", limit=1)
        return {"status": "ok"}

    @app.get("/api/stats")
    def stats(request: Request):
        from .stats import read_stats, season_for
        service = request.app.state.news
        return read_stats(service.database, season_for(service.clock()), service.clock())

    @app.get('/api/predictions')
    def predictions(request: Request, day: CalendarDate | None = Query(default=None, alias='date')):
        from .predictions.history import EASTERN
        from .predictions.service import read_predictions
        now = request.app.state.news.clock()
        selected = day or now.astimezone(EASTERN).date()
        try:
            if not prediction_files:
                from .predictions.repository import read_day,performance
                result=read_day(request.app.state.news.database,selected.isoformat(),now)
                result['performance']=performance(request.app.state.news.database,now)
                return result
            return read_predictions(prediction_path, selected.isoformat(), now)
        except (ValueError, KeyError, TypeError, OSError, AttributeError, OverflowError,sqlite3.Error):
            raise HTTPException(503, 'Saved predictions are unavailable. Try again later.') from None

    @app.get("/api/briefings/latest")
    def latest(request: Request, mode: DataMode = "live"):
        result = request.app.state.news.latest(mode)
        result["status"]["public_read_only"] = public_read_only
        return result

    @app.get("/api/briefings")
    def history(request: Request, mode: DataMode = "live", limit: int = Query(default=10, ge=1, le=50)):
        briefings = read_briefings(request.app.state.news.database, mode, limit)
        return {"data_mode": mode, "briefings": [dict(briefing_id=briefing["briefing_id"], as_of=briefing["as_of"],
                experimental=briefing["experimental"], story_count=len(briefing["stories"]),
                candidate_count=len(briefing["candidates"])) for briefing in briefings]}

    @app.get("/api/briefings/{briefing_id}")
    def saved_briefing(briefing_id: int, request: Request, mode: DataMode = "live"):
        saved = read_briefings(request.app.state.news.database, mode, limit=1, briefing_id=briefing_id)
        if not saved:
            raise HTTPException(404, "Saved briefing not found")
        return saved[0]

    @app.get("/api/stories/{story_id}")
    def story(story_id: str, request: Request, mode: DataMode = "live", briefing_id: int | None = Query(default=None, ge=1)):
        saved = request.app.state.news.story(story_id, mode, briefing_id)
        if saved is None:
            raise HTTPException(404, "Story not found in this briefing")
        return saved

    @app.post("/api/refresh")
    def refresh(body: RefreshRequest, request: Request):
        if automatic and body.mode == "live":
            raise HTTPException(403, "News updates automatically at 6 AM Eastern. Reload to read the saved briefing.")
        try:
            return request.app.state.news.refresh(body.mode, body.days, body.max_requests)
        except RefreshError as error:
            headers = {"Retry-After": str(error.retry_after)} if error.retry_after else None
            raise HTTPException(error.status_code, str(error), headers=headers) from None

    return app


app = create_app()
