from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _load_dotenv(path: Path = Path(".env")) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def _parse_int_set(raw: str) -> frozenset[int]:
    if not raw.strip():
        return frozenset()
    return frozenset(int(item.strip()) for item in raw.split(",") if item.strip())


def _parse_paths(raw: str) -> tuple[Path, ...]:
    return tuple(
        Path(item.strip()).expanduser().resolve()
        for item in raw.split(",")
        if item.strip()
    )


def _parse_api_keys(raw: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for pair in raw.split(","):
        if not pair.strip():
            continue
        producer, separator, secret = pair.partition(":")
        if not separator or not producer.strip() or not secret.strip():
            raise ValueError("KONTUR_API_KEYS must use producer:secret pairs")
        result[producer.strip()] = secret.strip()
    return result


def _parse_bool(raw: str) -> bool:
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class Settings:
    environment: str
    database_path: Path
    api_host: str
    api_port: int
    api_keys: dict[str, str]
    telegram_bot_token: str
    telegram_allowed_user_ids: frozenset[int]
    telegram_discovery_mode: bool
    timezone: str
    log_level: str
    poll_interval_seconds: float
    night_agent_outbox: Path
    artifact_allowed_roots: tuple[Path, ...]
    artifact_max_bytes: int

    @classmethod
    def from_env(cls) -> Settings:
        _load_dotenv()
        return cls(
            environment=os.getenv("KONTUR_ENV", "local"),
            database_path=Path(
                os.getenv("KONTUR_DATABASE_PATH", "./runtime/kontur.sqlite")
            ).expanduser(),
            api_host=os.getenv("KONTUR_API_HOST", "127.0.0.1"),
            api_port=int(os.getenv("KONTUR_API_PORT", "8090")),
            api_keys=_parse_api_keys(os.getenv("KONTUR_API_KEYS", "")),
            telegram_bot_token=os.getenv("KONTUR_TELEGRAM_BOT_TOKEN", ""),
            telegram_allowed_user_ids=_parse_int_set(
                os.getenv("KONTUR_TELEGRAM_ALLOWED_USER_IDS", "")
            ),
            telegram_discovery_mode=_parse_bool(
                os.getenv("KONTUR_TELEGRAM_DISCOVERY_MODE", "false")
            ),
            timezone=os.getenv("KONTUR_TIMEZONE", "Europe/Moscow"),
            log_level=os.getenv("KONTUR_LOG_LEVEL", "INFO"),
            poll_interval_seconds=float(os.getenv("KONTUR_POLL_INTERVAL_SECONDS", "5")),
            night_agent_outbox=Path(
                os.getenv("KONTUR_NIGHT_AGENT_OUTBOX", "./runtime/night-agent-outbox")
            ).expanduser(),
            artifact_allowed_roots=_parse_paths(
                os.getenv(
                    "KONTUR_ARTIFACT_ALLOWED_ROOTS",
                    (
                        "/Users/kkk7k/OBSIDIAN/development/ai/"
                        "_System/Night_Agent_Orchestrator/runs"
                    ),
                )
            ),
            artifact_max_bytes=int(
                os.getenv("KONTUR_ARTIFACT_MAX_BYTES", str(10 * 1024 * 1024))
            ),
        )
