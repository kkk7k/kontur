from __future__ import annotations

import argparse
import secrets
from pathlib import Path

DEFAULT_N8N_ENV = Path("/Users/kkk7k/DEV/pet/n8n_proj/.env")


def read_env(path: Path) -> tuple[list[str], dict[str, str]]:
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    values: dict[str, str] = {}
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key] = value
    return lines, values


def update_env(path: Path, updates: dict[str, str]) -> None:
    lines, _ = read_env(path)
    remaining = dict(updates)
    output: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0]
            if key in remaining:
                output.append(f"{key}={remaining.pop(key)}")
                continue
        output.append(line)
    if remaining and output and output[-1] != "":
        output.append("")
    output.extend(f"{key}={value}" for key, value in remaining.items())
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text("\n".join(output) + "\n", encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(path)


def add_producer_key(raw: str, producer: str, secret: str) -> str:
    pairs: list[tuple[str, str]] = []
    for item in raw.split(","):
        if not item.strip():
            continue
        name, separator, value = item.partition(":")
        if separator and name.strip() != producer:
            pairs.append((name.strip(), value.strip()))
    pairs.append((producer, secret))
    return ",".join(f"{name}:{value}" for name, value in pairs)


def configure(kontur_env: Path, n8n_env: Path) -> None:
    _, kontur_values = read_env(kontur_env)
    secret = secrets.token_urlsafe(32)
    api_keys = add_producer_key(
        kontur_values.get("KONTUR_API_KEYS", ""),
        "n8n",
        secret,
    )
    update_env(kontur_env, {"KONTUR_API_KEYS": api_keys})
    update_env(
        n8n_env,
        {
            "KONTUR_API_URL": "http://host.docker.internal:8090",
            "KONTUR_N8N_API_KEY": secret,
        },
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Configure private n8n access to Kontur")
    parser.add_argument("--kontur-env", type=Path, default=Path(".env"))
    parser.add_argument("--n8n-env", type=Path, default=DEFAULT_N8N_ENV)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.kontur_env.is_file():
        raise SystemExit(f"Kontur env does not exist: {args.kontur_env}")
    if not args.n8n_env.is_file():
        raise SystemExit(f"n8n env does not exist: {args.n8n_env}")
    configure(args.kontur_env, args.n8n_env)
    print("n8n producer key configured without exposing the secret.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
