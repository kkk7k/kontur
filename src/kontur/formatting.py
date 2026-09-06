from __future__ import annotations

from html import escape
from zoneinfo import ZoneInfo

from kontur.models import EventView

SEVERITY_ICON = {
    "debug": "⚪️",
    "info": "ℹ️",
    "warning": "⚠️",
    "error": "❌",
    "critical": "🚨",
}


def format_event_line(event: EventView, *, timezone: str = "UTC") -> str:
    icon = SEVERITY_ICON[event.severity.value]
    local_time = event.occurred_at.astimezone(ZoneInfo(timezone))
    time_label = local_time.strftime("%d.%m %H:%M")
    return f"{icon} {time_label} — {escape(event.title)} (<code>{escape(event.id)}</code>)"


def format_event_list(
    events: list[EventView], *, header: str, timezone: str = "UTC"
) -> str:
    lines = [header, ""]
    lines.extend(format_event_line(event, timezone=timezone) for event in events)
    return "\n".join(lines)


def format_event(event: EventView) -> str:
    icon = SEVERITY_ICON[event.severity.value]
    parts = [
        f"{icon} <b>{escape(event.title)}</b>",
        "",
        escape(event.summary) if event.summary else "Без дополнительного описания",
        "",
        f"<b>Источник:</b> {escape(event.producer)}",
        f"<b>Тип:</b> <code>{escape(event.type)}</code>",
        f"<b>ID:</b> <code>{escape(event.id)}</code>",
    ]
    if event.recommended_action:
        parts.extend(["", f"<b>Следующее действие:</b> {escape(event.recommended_action)}"])
    return "\n".join(parts)
