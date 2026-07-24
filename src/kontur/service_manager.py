from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from xml.sax.saxutils import escape

PROJECT_ROOT = Path(__file__).resolve().parents[2]
VENV_BIN = PROJECT_ROOT / ".venv" / "bin"
LOG_DIR = PROJECT_ROOT / "runtime" / "logs"
LAUNCH_AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"

SERVICES = {
    "api": {
        "label": "com.personal.kontur.api",
        "executable": VENV_BIN / "kontur-api",
    },
    "bot": {
        "label": "com.personal.kontur.bot",
        "executable": VENV_BIN / "kontur-bot",
    },
    "worker": {
        "label": "com.personal.kontur.worker",
        "executable": VENV_BIN / "kontur-worker",
    },
}


def render_plist(service_name: str) -> str:
    service = SERVICES[service_name]
    label = str(service["label"])
    executable = Path(service["executable"])
    stdout = LOG_DIR / f"{service_name}.log"
    stderr = LOG_DIR / f"{service_name}.error.log"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{escape(label)}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{escape(str(executable))}</string>
  </array>
  <key>WorkingDirectory</key>
  <string>{escape(str(PROJECT_ROOT))}</string>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <dict>
    <key>SuccessfulExit</key>
    <false/>
  </dict>
  <key>ProcessType</key>
  <string>Background</string>
  <key>StandardOutPath</key>
  <string>{escape(str(stdout))}</string>
  <key>StandardErrorPath</key>
  <string>{escape(str(stderr))}</string>
  <key>ThrottleInterval</key>
  <integer>10</integer>
</dict>
</plist>
"""


def plist_path(service_name: str) -> Path:
    return LAUNCH_AGENTS_DIR / f"{SERVICES[service_name]['label']}.plist"


def install() -> int:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    LAUNCH_AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    for name in SERVICES:
        executable = Path(SERVICES[name]["executable"])
        if not executable.is_file():
            print(f"Missing executable: {executable}", file=sys.stderr)
            return 2
        target = plist_path(name)
        target.write_text(render_plist(name), encoding="utf-8")
        subprocess.run(["launchctl", "unload", str(target)], check=False)
        result = subprocess.run(["launchctl", "load", str(target)], check=False)
        if result.returncode != 0:
            print(f"Failed to load {target}", file=sys.stderr)
            return result.returncode
    print("Kontur services installed.")
    return 0


def uninstall() -> int:
    for name in SERVICES:
        target = plist_path(name)
        if target.exists():
            subprocess.run(["launchctl", "unload", str(target)], check=False)
            target.unlink()
    print("Kontur services uninstalled.")
    return 0


def status() -> int:
    process = subprocess.run(
        ["launchctl", "list"],
        text=True,
        stdout=subprocess.PIPE,
        check=False,
    )
    for name, service in SERVICES.items():
        label = str(service["label"])
        state = "loaded" if label in process.stdout else "not loaded"
        print(f"{name}: {state}")
    return process.returncode


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Manage Kontur launchd services")
    parser.add_argument("command", choices=("install", "uninstall", "status", "render"))
    parser.add_argument("--service", choices=tuple(SERVICES), default="api")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "install":
        return install()
    if args.command == "uninstall":
        return uninstall()
    if args.command == "status":
        return status()
    print(render_plist(args.service))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
