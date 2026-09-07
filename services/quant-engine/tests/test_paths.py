from __future__ import annotations

from pathlib import Path

from quant_engine.paths import application_data_dir, ensure_app_paths


def test_macos_application_data_path_has_no_hard_coded_user() -> None:
    home = Path("/Users/example")

    assert application_data_dir(system="Darwin", environ={}, home=home) == (
        home / "Library" / "Application Support" / "QuantScreenTrader"
    )


def test_windows_application_data_path_uses_appdata() -> None:
    appdata = "C:/Users/example/AppData/Roaming"

    assert (
        application_data_dir(
            system="Windows",
            environ={"APPDATA": appdata},
            home=Path("C:/Users/example"),
        )
        == Path(appdata) / "QuantScreenTrader"
    )


def test_windows_path_falls_back_when_appdata_is_missing() -> None:
    home = Path("C:/Users/example")

    assert application_data_dir(system="Windows", environ={}, home=home) == (
        home / "AppData" / "Roaming" / "QuantScreenTrader"
    )


def test_linux_path_uses_xdg_data_home() -> None:
    xdg_home = "/var/tmp/example-data"

    assert (
        application_data_dir(
            system="Linux",
            environ={"XDG_DATA_HOME": xdg_home},
            home=Path("/home/example"),
        )
        == Path(xdg_home) / "QuantScreenTrader"
    )


def test_data_directory_override_and_subdirectories(tmp_path: Path) -> None:
    root = tmp_path / "custom"

    assert application_data_dir(environ={"QST_DATA_DIR": str(root)}) == root

    paths = ensure_app_paths(root)
    assert paths.database_file == root / "db" / "quant-screen-trader.sqlite3"
    assert all(directory.is_dir() for directory in paths.directories)
