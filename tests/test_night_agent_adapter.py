import json
from pathlib import Path

from kontur.night_agent_adapter import main, summarize_report


def report(tmp_path: Path) -> Path:
    run_dir = tmp_path / "2026-07-24"
    run_dir.mkdir()
    result = run_dir / "morning-report.md"
    result.write_text(
        "# Ночной запуск — 2026-07-24\n\n"
        "## task-ok: completed\n\n"
        "## task-bad: needs_attention\n",
        encoding="utf-8",
    )
    return result


def test_summarize_report(tmp_path: Path) -> None:
    event = summarize_report(report(tmp_path), exit_code=2)
    assert event["type"] == "run_failed"
    assert event["severity"] == "error"
    assert event["metrics"]["tasks_total"] == 2
    assert event["metrics"]["tasks_succeeded"] == 1
    assert event["requires_action"] is True


def test_unavailable_api_writes_outbox(tmp_path: Path) -> None:
    outbox = tmp_path / "outbox"
    result = main(
        [
            str(report(tmp_path)),
            "--api-key",
            "test",
            "--api-url",
            "http://127.0.0.1:1",
            "--outbox",
            str(outbox),
        ]
    )
    assert result == 0
    files = list(outbox.glob("*.json"))
    assert len(files) == 1
    assert json.loads(files[0].read_text(encoding="utf-8"))["producer"] == "night-agent"


def test_missing_key_writes_outbox(tmp_path: Path) -> None:
    outbox = tmp_path / "outbox"
    result = main([str(report(tmp_path)), "--outbox", str(outbox)])
    assert result == 0
    assert len(list(outbox.glob("*.json"))) == 1
