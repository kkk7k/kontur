from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

from kontur.models import EventCreate, Severity
from kontur.registry import Registry
from kontur.service import KonturService


@dataclass(slots=True)
class Watchdog:
    registry: Registry
    service: KonturService
    n8n_url: str = "http://127.0.0.1:5678"
    flap_threshold: int = 3

    def check(self, now: datetime | None = None) -> int:
        now = now or datetime.now(UTC)
        self.registry.heartbeat("kontur-worker", now)
        try:
            response = httpx.get(
                f"{self.n8n_url.rstrip('/')}/healthz",
                timeout=5,
                trust_env=False,
            )
            if response.is_success:
                self.registry.heartbeat("n8n", now)
        except httpx.HTTPError:
            pass

        stale = self.registry.stale_services(now)
        stale_ids = {automation.id for automation in stale}
        all_service_ids = {
            automation.id
            for automation in self.registry.automations(now)
            if automation.kind == "service"
        }
        self.registry.close_recovered_incidents(all_service_ids - stale_ids, now)

        created = 0
        for automation in stale:
            suffix = hashlib.sha256(
                f"{automation.id}:{now.isoformat()}".encode()
            ).hexdigest()[:16]
            event_id = f"evt_watchdog_{suffix}"
            flap_count = self.registry.open_incident(automation.id, event_id, now)
            if flap_count is None:
                continue
            if flap_count > self.flap_threshold:
                # Flap storm already announced once this window — suppress
                # further spam until it quiets down or gets resolved.
                continue
            if flap_count == self.flap_threshold:
                severity = Severity.CRITICAL
                title = f"Частые сбои автоматизации: {automation.name}"
                summary = (
                    f"{flap_count} рестартов за "
                    f"{int(self.registry.FLAP_WINDOW.total_seconds() // 60)} минут — "
                    "похоже на флап, а не на разовый сбой."
                )
            else:
                severity = Severity.ERROR
                title = f"Сбой автоматизации: {automation.name}"
                summary = (
                    f"Heartbeat не получен дольше "
                    f"{automation.stale_after_seconds} секунд."
                )
            event = EventCreate(
                id=event_id,
                occurred_at=now,
                producer="kontur-watchdog",
                agent=None,
                type="automation_failed",
                severity=severity,
                title=title,
                summary=summary,
                requires_action=True,
                recommended_action="Проверить сервис и его логи",
                deduplication_key=f"watchdog:{automation.id}:{suffix}",
                payload={"automation_id": automation.id},
                correlation_id=automation.id,
            )
            self.service.register_event(event)
            created += 1
        return created
