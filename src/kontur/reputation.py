from __future__ import annotations

import hashlib
import json
import math
import re
import subprocess
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from kontur.db import Database
from kontur.models import (
    EventCreate,
    ReputationRunResult,
    ReputationTopic,
    Severity,
)
from kontur.service import KonturService

ISSUE_KEY = re.compile(r"\b[A-Z][A-Z0-9]+-\d+\b")

TOPICS = {
    "event_reliability": {
        "terms": (
            "idempot",
            "retry",
            "reconnect",
            "duplicate",
            "dedup",
            "storno",
            "reverse",
            "rollback",
            "replay",
            "at-least-once",
            "exactly-once",
        ),
        "title": "Надёжная обработка событий и обратных операций",
        "career_weight": 4,
        "angle": (
            "Failure modes, идемпотентность и порядок событий вместо "
            "идеального happy path"
        ),
    },
    "observability": {
        "terms": (
            "metric",
            "alert",
            "grafana",
            "prometheus",
            "lag",
            "dashboard",
            "observability",
            "monitor",
        ),
        "title": "Наблюдаемость event-driven сервиса через бизнес-сигналы",
        "career_weight": 3,
        "angle": (
            "Как связать технические метрики, бизнес-воронку и диагностику "
            "потерь событий"
        ),
    },
    "concurrency": {
        "terms": (
            "parallel",
            "concurr",
            "race",
            "leader",
            "lock",
            "batch",
            "goroutine",
            "preload",
        ),
        "title": "Контролируемый concurrency в Go batch-процессах",
        "career_weight": 3,
        "angle": (
            "Параллелизм, ограничение ресурсов, повторяемость и наблюдаемость "
            "долгого процесса"
        ),
    },
    "data_evolution": {
        "terms": (
            "migration",
            "schema",
            "history",
            "contract",
            "clickhouse",
            "postgres",
        ),
        "title": "Эволюция данных без остановки связанных сервисов",
        "career_weight": 3,
        "angle": (
            "Контракты, историчность, rollout и rollback при изменении "
            "финансового контура"
        ),
    },
    "testing": {
        "terms": ("test", "mock", "fixture", "integration", "scenario", "case"),
        "title": "Тестирование неоднозначных сценариев распределённой системы",
        "career_weight": 1,
        "angle": (
            "Как строить таблицу сценариев для дублей, обратных операций и "
            "частичных отказов"
        ),
    },
}


@dataclass(frozen=True, slots=True)
class GitCommit:
    sha: str
    subject: str
    files: tuple[str, ...]


@dataclass(slots=True)
class ReputationResearchAgent:
    database: Database
    service: KonturService
    repositories: tuple[Path, ...]
    timezone: str = "Europe/Moscow"
    author_filters: tuple[str, ...] = ()

    def run(self, now: datetime | None = None) -> ReputationRunResult:
        now = now or datetime.now(ZoneInfo(self.timezone))
        evidence: dict[str, dict[str, object]] = defaultdict(
            lambda: {"count": 0, "repositories": set()}
        )
        heads: dict[str, str] = {}
        scanned = 0

        for repository in self.repositories:
            commits, head = self._commits(repository)
            heads[str(repository)] = head
            scanned += len(commits)
            for commit in commits:
                text = f"{commit.subject} {' '.join(commit.files)}".lower()
                for key, definition in TOPICS.items():
                    if any(term in text for term in definition["terms"]):
                        evidence[key]["count"] = int(evidence[key]["count"]) + 1
                        repositories = evidence[key]["repositories"]
                        assert isinstance(repositories, set)
                        repositories.add(repository.name)

        pending_signals = self.database.pending_reputation_signals()
        signal_counts: dict[str, int] = defaultdict(int)
        for row in pending_signals:
            text = f"{row['title']} {row['summary']} {row['tags_json']}".lower()
            for key, definition in TOPICS.items():
                if any(term in text for term in definition["terms"]):
                    signal_counts[key] += int(row["frequency"])

        topics = self._rank(evidence, signal_counts)[:3]
        fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "heads": heads,
                    "signals": [
                        f"{row['source']}:{row['external_id']}"
                        for row in pending_signals
                    ],
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()[:20]
        event_id: str | None = None
        created = False
        if topics:
            event = self._event(
                topics,
                scanned,
                len(pending_signals),
                fingerprint,
                now,
            )
            saved, created = self.service.register_event(event)
            event_id = saved.id

        for repository, head in heads.items():
            self.database.save_reputation_checkpoint(repository, head)
        self.database.mark_reputation_signals_processed()
        return ReputationRunResult(
            scanned_commits=scanned,
            accepted_signals=len(pending_signals),
            topics=topics,
            event_id=event_id,
            created=created,
        )

    def _commits(self, repository: Path) -> tuple[list[GitCommit], str]:
        if not (repository / ".git").exists():
            raise ValueError(f"not a git repository: {repository}")
        head = self._git(repository, "rev-parse", "HEAD").strip()
        checkpoint = self.database.reputation_checkpoint(str(repository))
        revision = f"{checkpoint}..HEAD" if checkpoint else "HEAD"
        output = self._git(
            repository,
            "log",
            revision,
            "--format=__KONTUR_COMMIT__%H%x1f%an%x1f%ae%x1f%s",
            "--name-only",
            "--no-renames",
        )
        commits: list[GitCommit] = []
        current: tuple[str, str, str, str] | None = None
        files: list[str] = []
        for raw_line in output.splitlines():
            if raw_line.startswith("__KONTUR_COMMIT__"):
                if current and self._author_allowed(current[1], current[2]):
                    commits.append(GitCommit(current[0], current[3], tuple(files)))
                parts = raw_line.removeprefix("__KONTUR_COMMIT__").split("\x1f", 3)
                current = (
                    parts[0],
                    parts[1],
                    parts[2],
                    ISSUE_KEY.sub("", parts[3]),
                )
                files = []
            elif raw_line and current:
                files.append(raw_line)
        if current and self._author_allowed(current[1], current[2]):
            commits.append(GitCommit(current[0], current[3], tuple(files)))
        return commits, head

    def _author_allowed(self, name: str, email: str) -> bool:
        if not self.author_filters:
            return True
        identity = f"{name} {email}".lower()
        return any(value.lower() in identity for value in self.author_filters)

    @staticmethod
    def _git(repository: Path, *args: str) -> str:
        result = subprocess.run(
            ["git", "-C", str(repository), *args],
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
        return result.stdout

    @staticmethod
    def _rank(
        evidence: dict[str, dict[str, object]],
        signal_counts: dict[str, int],
    ) -> list[ReputationTopic]:
        result: list[ReputationTopic] = []
        for key, definition in TOPICS.items():
            count = int(evidence[key]["count"])
            repositories_value = evidence[key]["repositories"]
            assert isinstance(repositories_value, set)
            repositories = sorted(str(item) for item in repositories_value)
            signals = signal_counts[key]
            if count == 0 and signals == 0:
                continue
            source_type = (
                "combined"
                if count and signals
                else "engineering_evidence"
                if count
                else "audience_demand"
            )
            disclosure_risk = 3 if count else 1
            score = min(math.ceil(math.log2(count + 1)), 8)
            score += min(len(repositories), 3) + min(signals, 5)
            score += int(definition["career_weight"])
            score += 3 if source_type == "combined" else 1
            score -= disclosure_risk
            result.append(
                ReputationTopic(
                    key=key,
                    title=str(definition["title"]),
                    angle=str(definition["angle"]),
                    source_type=source_type,
                    evidence_count=count,
                    repositories=repositories,
                    signal_count=signals,
                    score=score,
                    disclosure_risk=disclosure_risk,
                )
            )
        return sorted(result, key=lambda item: (-item.score, item.key))

    @staticmethod
    def _event(
        topics: list[ReputationTopic],
        scanned: int,
        signals: int,
        fingerprint: str,
        now: datetime,
    ) -> EventCreate:
        lines = [
            f"{index}. {topic.title} — {topic.angle}"
            for index, topic in enumerate(topics, start=1)
        ]
        return EventCreate(
            id=f"evt_reputation_{fingerprint}",
            occurred_at=now,
            producer="reputation-agent",
            agent="reputation",
            type="reputation_weekly_digest",
            severity=Severity.INFO,
            title="Reputation Agent: кандидаты для материалов",
            summary=(
                f"Проанализировано commits: {scanned}; новых сигналов спроса: "
                f"{signals}.\n" + "\n".join(lines)
            ),
            confidence=0.7,
            requires_action=True,
            recommended_action="Выбрать тему для prior-art research",
            deduplication_key=f"reputation:{fingerprint}",
            source={"kind": "git_metadata", "name": "approved-repositories"},
            metrics={
                "commits_scanned": scanned,
                "signals_processed": signals,
                "topics_found": len(topics),
            },
            payload={
                "automation_id": "reputation-weekly",
                "topics": [topic.model_dump(mode="json") for topic in topics],
                "raw_code_shared": False,
                "authorship_confirmation_required": True,
            },
            correlation_id=f"reputation-{fingerprint}",
        )
