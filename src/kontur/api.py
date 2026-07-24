from __future__ import annotations

import hmac
import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response, status

from kontur.config import Settings
from kontur.db import Database
from kontur.models import EventCreate, EventList, EventStatus, EventView, StatusView
from kontur.service import KonturService

LOGGER = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    database = Database(settings.database_path)
    service = KonturService(database, settings.telegram_allowed_user_ids)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        database.initialize()
        yield

    app = FastAPI(title="Kontur API", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.service = service

    def get_service(request: Request) -> KonturService:
        return request.app.state.service

    def authenticate_producer(
        request: Request,
        producer: str = Header(alias="X-Kontur-Producer"),
        api_key: str = Header(alias="X-Kontur-API-Key"),
    ) -> str:
        expected = request.app.state.settings.api_keys.get(producer)
        if not expected or not hmac.compare_digest(api_key, expected):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="unauthorized")
        return producer

    @app.get("/health/live")
    def live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready")
    def ready(service: KonturService = Depends(get_service)) -> dict[str, str]:
        service.status()
        return {"status": "ok"}

    @app.get("/api/v1/status", response_model=StatusView)
    def system_status(service: KonturService = Depends(get_service)) -> StatusView:
        return service.status()

    @app.post("/api/v1/events", response_model=EventView)
    def create_event(
        event: EventCreate,
        response: Response,
        producer: str = Depends(authenticate_producer),
        service: KonturService = Depends(get_service),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> EventView:
        if producer != event.producer:
            raise HTTPException(status_code=403, detail="producer mismatch")
        if idempotency_key and idempotency_key != event.deduplication_key:
            raise HTTPException(status_code=400, detail="idempotency key mismatch")
        saved, created = service.register_event(event)
        response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
        return saved

    @app.get("/api/v1/events", response_model=EventList)
    def list_events(
        service: KonturService = Depends(get_service),
        event_status: EventStatus | None = Query(default=None, alias="status"),
        event_type: str | None = Query(default=None, alias="type"),
        limit: int = Query(default=50, ge=1, le=200),
    ) -> EventList:
        return service.events(status=event_status, event_type=event_type, limit=limit)

    @app.get("/api/v1/events/{event_id}", response_model=EventView)
    def get_event(
        event_id: str,
        service: KonturService = Depends(get_service),
    ) -> EventView:
        event = service.event(event_id)
        if event is None:
            raise HTTPException(status_code=404, detail="event not found")
        return event

    def transition_event(
        event_id: str,
        target: EventStatus,
        service: KonturService,
    ) -> EventView:
        try:
            event = service.transition(event_id, target, actor_id="api-user")
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        if event is None:
            raise HTTPException(status_code=404, detail="event not found")
        return event

    @app.post("/api/v1/events/{event_id}/seen", response_model=EventView)
    def mark_seen(
        event_id: str,
        service: KonturService = Depends(get_service),
    ) -> EventView:
        return transition_event(event_id, EventStatus.SEEN, service)

    @app.post("/api/v1/events/{event_id}/resolve", response_model=EventView)
    def resolve(
        event_id: str,
        service: KonturService = Depends(get_service),
    ) -> EventView:
        return transition_event(event_id, EventStatus.RESOLVED, service)

    return app


app = create_app()


def main() -> None:
    settings = Settings.from_env()
    logging.basicConfig(level=settings.log_level)
    uvicorn.run(
        "kontur.api:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=False,
    )


if __name__ == "__main__":
    main()
