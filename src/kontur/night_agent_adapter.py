from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

TASK_HEADING = re.compile(r"^##\s+(.+?):\s+([a-z_]+)\s*$", re.MULTILINE)


def summarize_report(report_path: Path, exit_code: int) -> dict[str, Any]:
    content = report_path.read_text(encoding="utf-8")
    tasks = TASK_HEADING.findall(content)
    total = len(tasks)
    completed = sum(status == "completed" for _, status in tasks)
    failed = total - completed
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    occurred_at = datetime.fromtimestamp(report_path.stat().st_mtime, UTC)
    event_type = "run_completed" if exit_code == 0 else "run_failed"
    severity = "info" if exit_code == 0 else "error"
    requires_action = exit_code != 0
    date_label = report_path.parent.name
    event_id = f"evt_night_{date_label}_{digest[:10]}"
    return {
        "id": event_id,
        "schema_version": 1,
        "occurred_at": occurred_at.isoformat(),
        "producer": "night-agent",
        "agent": "coding",
        "run_id": f"run_night_{date_label}",
        "type": event_type,
        "severity": severity,
        "title": "Ночной запуск завершён" if exit_code == 0 else "Ночной запуск требует внимания",
        "summary": (
            f"Задач: {total}. Успешно: {completed}. Требуют внимания: {failed}."
        ),
        "confidence": 1.0,
        "requires_action": requires_action,
        "approval_required": False,
        "recommended_action": "Проверить morning report" if requires_action else None,
        "deduplication_key": f"night-agent:{date_label}:{digest}",
        "source": {
            "kind": "local_file",
            "name": "morning-report",
            "uri": str(report_path.resolve()),
        },
        "metrics": {
            "tasks_total": total,
            "tasks_succeeded": completed,
            "tasks_failed": failed,
        },
        "payload": {
            "exit_code": exit_code,
            "task_statuses": [{"task_id": task_id, "status": status} for task_id, status in tasks],
        },
        "artifacts": [
            {
                "kind": "markdown",
                "title": "Morning report",
                "uri": str(report_path.resolve()),
                "content_type": "text/markdown",
                "checksum": f"sha256:{digest}",
            }
        ],
        "correlation_id": f"night-{date_label}",
    }


def post_event(
    event: dict[str, Any],
    *,
    api_url: str,
    api_key: str,
    timeout_seconds: float = 10,
) -> None:
    request = urllib.request.Request(
        f"{api_url.rstrip('/')}/api/v1/events",
        data=json.dumps(event, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Kontur-Producer": event["producer"],
            "X-Kontur-API-Key": api_key,
            "Idempotency-Key": event["deduplication_key"],
        },
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310
        if response.status not in {200, 201}:
            raise RuntimeError(f"Kontur API returned HTTP {response.status}")


def write_outbox(event: dict[str, Any], outbox_dir: Path) -> Path:
    outbox_dir.mkdir(parents=True, exist_ok=True)
    target = outbox_dir / f"{event['id']}.json"
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(event, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(target)
    return target


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Send Night Agent morning report to Kontur")
    parser.add_argument("report", type=Path)
    parser.add_argument("--exit-code", type=int, default=0)
    parser.add_argument(
        "--api-url",
        default=os.getenv("KONTUR_API_URL", "http://127.0.0.1:8090"),
    )
    parser.add_argument("--api-key", default=os.getenv("KONTUR_NIGHT_AGENT_API_KEY", ""))
    parser.add_argument(
        "--outbox",
        type=Path,
        default=Path(os.getenv("KONTUR_NIGHT_AGENT_OUTBOX", "./runtime/night-agent-outbox")),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.report.is_file():
        print(f"Report does not exist: {args.report}", file=sys.stderr)
        return 2
    event = summarize_report(args.report, args.exit_code)
    if not args.api_key:
        target = write_outbox(event, args.outbox)
        print(
            f"Kontur API key is not configured; saved to {target}",
            file=sys.stderr,
        )
        return 0
    try:
        post_event(event, api_url=args.api_url, api_key=args.api_key)
    except (OSError, urllib.error.URLError, RuntimeError) as error:
        target = write_outbox(event, args.outbox)
        print(
            f"Kontur delivery deferred ({type(error).__name__}); saved to {target}",
            file=sys.stderr,
        )
        return 0
    print(event["id"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
