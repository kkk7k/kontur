from pathlib import Path

from fastapi.testclient import TestClient

from kontur.api import create_app
from kontur.config import Settings


def settings(tmp_path: Path) -> Settings:
    return Settings(
        environment="test",
        database_path=tmp_path / "kontur.sqlite",
        api_host="127.0.0.1",
        api_port=8090,
        api_keys={"night-agent": "test-secret"},
        telegram_bot_token="",
        telegram_allowed_user_ids=frozenset({123}),
        telegram_vacancy_user_ids=frozenset(),
        telegram_discovery_mode=False,
        timezone="Europe/Moscow",
        log_level="INFO",
        poll_interval_seconds=0.01,
        night_agent_outbox=tmp_path / "outbox",
        artifact_allowed_roots=(tmp_path,),
        artifact_max_bytes=1024,
    )


def payload() -> dict[str, object]:
    return {
        "id": "evt_api_001",
        "occurred_at": "2026-07-24T03:12:00+03:00",
        "producer": "night-agent",
        "type": "run_completed",
        "severity": "warning",
        "title": "Done",
        "requires_action": True,
        "deduplication_key": "night:api:001",
    }


def headers() -> dict[str, str]:
    return {
        "X-Kontur-Producer": "night-agent",
        "X-Kontur-API-Key": "test-secret",
        "Idempotency-Key": "night:api:001",
    }


def test_api_auth_and_idempotency(tmp_path: Path) -> None:
    with TestClient(create_app(settings(tmp_path))) as client:
        unauthorized = client.post("/api/v1/events", json=payload())
        assert unauthorized.status_code == 422

        first = client.post("/api/v1/events", json=payload(), headers=headers())
        second = client.post("/api/v1/events", json=payload(), headers=headers())

        assert first.status_code == 201
        assert second.status_code == 200
        assert second.json()["id"] == first.json()["id"]
        assert client.get("/api/v1/status").json()["pending_inbox"] == 1
        assert len(client.get("/api/v1/agents").json()) == 5
        assert len(client.get("/api/v1/automations").json()) == 8


def test_api_rejects_producer_mismatch(tmp_path: Path) -> None:
    body = payload()
    body["producer"] = "other"
    with TestClient(create_app(settings(tmp_path))) as client:
        response = client.post("/api/v1/events", json=body, headers=headers())
    assert response.status_code == 403


def test_generic_vacancy_ingest_is_authenticated_and_idempotent(
    tmp_path: Path,
) -> None:
    vacancy = {
        "source": "telegram",
        "external_id": "golang-jobs:42",
        "title": "Senior Go Developer",
        "url": "https://t.me/golang_jobs/42",
        "company": "Example",
        "published_at": "2026-07-24T12:00:00+03:00",
        "matched_by": ["Senior в названии"],
    }
    with TestClient(create_app(settings(tmp_path))) as client:
        unauthorized = client.post("/api/v1/vacancies", json=vacancy)
        first = client.post("/api/v1/vacancies", json=vacancy, headers=headers())
        second = client.post("/api/v1/vacancies", json=vacancy, headers=headers())

    assert unauthorized.status_code == 422
    assert first.status_code == 201
    assert first.json() == {"created": True}
    assert second.status_code == 200
    assert second.json() == {"created": False}


def test_reputation_signal_intake_is_idempotent(tmp_path: Path) -> None:
    signal = {
        "source": "forum",
        "external_id": "question-42",
        "url": "https://example.com/questions/42",
        "title": "Kafka retry without duplicate processing",
        "summary": "Repeated user question",
        "observed_at": "2026-07-24T12:00:00+03:00",
        "tags": ["kafka", "retry"],
        "frequency": 3,
    }
    with TestClient(create_app(settings(tmp_path))) as client:
        first = client.post(
            "/api/v1/reputation/signals",
            json=signal,
            headers=headers(),
        )
        second = client.post(
            "/api/v1/reputation/signals",
            json=signal,
            headers=headers(),
        )

    assert first.status_code == 201
    assert first.json() == {"created": True}
    assert second.status_code == 200
    assert second.json() == {"created": False}
