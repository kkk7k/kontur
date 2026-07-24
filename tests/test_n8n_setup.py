from pathlib import Path

from kontur.n8n_setup import add_producer_key, configure, read_env


def test_add_producer_preserves_other_keys() -> None:
    result = add_producer_key("night-agent:one,career:two", "n8n", "three")
    assert result == "night-agent:one,career:two,n8n:three"


def test_add_producer_replaces_existing_key() -> None:
    result = add_producer_key("night-agent:one,n8n:old", "n8n", "new")
    assert result == "night-agent:one,n8n:new"


def test_configure_writes_same_hidden_secret_to_both_envs(tmp_path: Path) -> None:
    kontur_env = tmp_path / "kontur.env"
    n8n_env = tmp_path / "n8n.env"
    kontur_env.write_text(
        "# Kontur\nKONTUR_API_KEYS=night-agent:existing\nTOKEN=keep\n",
        encoding="utf-8",
    )
    n8n_env.write_text("N8N_VERSION=1.101.0\n", encoding="utf-8")
    configure(kontur_env, n8n_env)
    _, kontur = read_env(kontur_env)
    _, n8n = read_env(n8n_env)
    pairs = dict(item.split(":", 1) for item in kontur["KONTUR_API_KEYS"].split(","))
    assert pairs["night-agent"] == "existing"
    assert pairs["n8n"] == n8n["KONTUR_N8N_API_KEY"]
    assert n8n["KONTUR_API_URL"] == "http://host.docker.internal:8090"
    assert kontur["TOKEN"] == "keep"
    assert len(pairs["n8n"]) >= 32
