from pathlib import Path

from kontur import service_manager


def test_plist_is_local_and_uses_project_working_directory() -> None:
    plist = service_manager.render_plist("api")
    assert "com.personal.kontur.api" in plist
    assert str(service_manager.PROJECT_ROOT) in plist
    assert str(service_manager.VENV_BIN / "kontur-api") in plist
    assert "<key>RunAtLoad</key>" in plist
    assert "<key>KeepAlive</key>" in plist


def test_each_service_has_unique_plist() -> None:
    paths = [service_manager.plist_path(name) for name in service_manager.SERVICES]
    assert len(paths) == len(set(paths))
    assert all(isinstance(path, Path) for path in paths)
