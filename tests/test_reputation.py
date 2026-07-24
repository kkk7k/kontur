from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path

from kontur.db import Database
from kontur.models import ReputationSignalCreate
from kontur.reputation import ReputationResearchAgent
from kontur.service import KonturService


def git(repository: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def commit(repository: Path, filename: str, message: str) -> None:
    path = repository / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(message, encoding="utf-8")
    git(repository, "add", filename)
    git(
        repository,
        "-c",
        "user.name=Kontur Test",
        "-c",
        "user.email=kontur@example.test",
        "commit",
        "-m",
        message,
    )


def setup(tmp_path: Path) -> tuple[Database, KonturService, Path]:
    repository = tmp_path / "compensation"
    repository.mkdir()
    git(repository, "init")
    commit(repository, "internal/retry.go", "add idempotent retry")
    commit(repository, "metrics/alerts.go", "add prometheus business alerts")
    database = Database(tmp_path / "kontur.sqlite")
    database.initialize()
    service = KonturService(database, frozenset({42}))
    return database, service, repository


def test_first_run_scans_history_and_second_uses_checkpoint(tmp_path: Path) -> None:
    database, service, repository = setup(tmp_path)
    agent = ReputationResearchAgent(database, service, (repository,))

    first = agent.run(datetime(2026, 7, 24, 12, tzinfo=UTC))
    second = agent.run(datetime(2026, 7, 31, 12, tzinfo=UTC))

    assert first.scanned_commits == 2
    assert first.created is True
    assert {topic.key for topic in first.topics} >= {
        "event_reliability",
        "observability",
    }
    assert second.scanned_commits == 0
    assert second.created is False
    assert database.reputation_checkpoint(str(repository)) == git(
        repository, "rev-parse", "HEAD"
    ).strip()


def test_new_commit_after_checkpoint_is_scanned(tmp_path: Path) -> None:
    database, service, repository = setup(tmp_path)
    agent = ReputationResearchAgent(database, service, (repository,))
    agent.run(datetime(2026, 7, 24, 12, tzinfo=UTC))
    commit(repository, "batch/parallel.go", "parallel batch worker")

    result = agent.run(datetime(2026, 7, 31, 12, tzinfo=UTC))

    assert result.scanned_commits == 1
    assert result.topics[0].key == "concurrency"


def test_audience_signal_combines_with_engineering_evidence(tmp_path: Path) -> None:
    database, service, repository = setup(tmp_path)
    signal = ReputationSignalCreate(
        source="forum",
        external_id="question-1",
        url="https://example.com/questions/1",
        title="How to retry Kafka events without duplicates?",
        summary="Repeated question about idempotent consumers",
        audience="Go backend developers",
        observed_at=datetime(2026, 7, 24, 10, tzinfo=UTC),
        tags=["kafka", "retry"],
        frequency=4,
    )
    assert database.create_reputation_signal(signal) is True
    assert database.create_reputation_signal(signal) is False

    result = ReputationResearchAgent(database, service, (repository,)).run(
        datetime(2026, 7, 24, 12, tzinfo=UTC)
    )

    reliability = next(
        topic for topic in result.topics if topic.key == "event_reliability"
    )
    assert reliability.source_type == "combined"
    assert reliability.signal_count == 4
    assert result.accepted_signals == 1


def test_untracked_files_are_not_read_or_classified(tmp_path: Path) -> None:
    database, service, repository = setup(tmp_path)
    (repository / "secret-production-dump.csv").write_text(
        "parallel migration password",
        encoding="utf-8",
    )

    result = ReputationResearchAgent(database, service, (repository,)).run(
        datetime(2026, 7, 24, 12, tzinfo=UTC)
    )

    assert "concurrency" not in {topic.key for topic in result.topics}
    assert "data_evolution" not in {topic.key for topic in result.topics}
