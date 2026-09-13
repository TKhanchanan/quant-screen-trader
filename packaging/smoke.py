"""Bounded, native package smoke tests. Never visits a broker or uses an existing profile."""

import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import psutil


def unused_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_for(check, child, seconds=60):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if child.poll() is not None:
            raise AssertionError(f"Process exited before readiness: {child.returncode}")
        result = check()
        if result:
            return result
        time.sleep(0.2)
    raise AssertionError("Package readiness timed out")


def healthy(port, version):
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/health", timeout=1
        ) as response:
            data = json.load(response)
        assert data["type"] == "health" and data["service"] == "quant-engine"
        assert data["status"] == "ok" and data["database"] == "ok"
        assert data["version"] == version
        return data
    except OSError:
        return None


def descendants(child):
    try:
        return psutil.Process(child.pid).children(recursive=True)
    except psutil.NoSuchProcess:
        return []


def assert_gone(processes):
    _, alive = psutil.wait_procs(processes, timeout=10)
    # A zombie has terminated but its OS parent has not yet reaped it.
    alive = [p for p in alive if p.status() != psutil.STATUS_ZOMBIE]
    try:
        assert not alive, f"Orphan processes: {[p.pid for p in alive]}"
    finally:
        for process in alive:
            process.kill()


def writable_database(directory):
    database = directory / "db" / "quant-screen-trader.sqlite3"
    assert database.is_file(), "Engine did not initialize its data directory"
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        connection.execute("CREATE TABLE IF NOT EXISTS packaging_smoke (value INTEGER)")
        connection.execute("INSERT INTO packaging_smoke VALUES (1)")


def smoke(kind, binary, version):
    with tempfile.TemporaryDirectory(prefix="qst-package-") as temporary:
        root = Path(temporary).resolve()
        # No interpreter lookup is possible in the child, even on a development machine.
        env = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith(("QST_", "PYTHON", "VIRTUAL_ENV", "ELECTRON_"))
        }
        env["PATH"] = str(root / "no-system-python")
        env["QST_ENGINE_HOST"] = "127.0.0.1"
        if kind == "engine":
            help_result = subprocess.run(
                [binary, "--help"],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
                timeout=15,
                check=True,
            )
            assert "--data-dir" in help_result.stdout and "--host" in help_result.stdout
            rejected = subprocess.run(
                [binary, "--host", "0.0.0.0"],
                cwd=root,
                env=env,
                capture_output=True,
                timeout=15,
                check=False,
            )
            assert rejected.returncode != 0, "Network-visible bind was accepted"
        for attempt in range(2):
            port = unused_port()
            env["QST_ENGINE_PORT"] = str(port)
            data = root / "QuantScreenTrader"
            report = data / "package-smoke.json"
            quit_file = data / "package-smoke.quit"
            for file in (report, quit_file):
                file.unlink(missing_ok=True)
            if kind == "engine":
                args = [
                    binary,
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                    "--data-dir",
                    str(data),
                ]
            else:
                args = [binary, "--qst-package-smoke"]
                env["QST_PACKAGE_SMOKE_ROOT"] = str(root)
            with (root / f"{kind}-{attempt}.log").open("w+") as output:
                child = subprocess.Popen(
                    args, cwd=root, env=env, stdout=output, stderr=output
                )
                tracked = []
                try:
                    wait_for(lambda port=port: healthy(port, version), child)
                    tracked = descendants(child)
                    writable_database(data)
                    if kind == "app":
                        wait_for(report.is_file, child)
                        result = json.loads(report.read_text())
                        assert result["ocr"] == "ok" and result["renderer"] == "ok", (
                            result
                        )
                        assert result["appVersion"] == version, result
                        assert result["arch"] == (
                            "arm64" if sys.platform == "darwin" else "x64"
                        ), result
                        assert result["userData"] == str(data), result
                        assert result["enginePath"].startswith(
                            str(Path(binary).parent.parent)
                        ), result
                        assert result["enginePid"] in [p.pid for p in tracked], result
                        # Second invocation must exit without starting another engine.
                        before = {
                            p.pid
                            for p in descendants(child)
                            if p.name().startswith("quant-engine")
                        }
                        second = subprocess.Popen(
                            args, cwd=root, env=env, stdout=output, stderr=output
                        )
                        try:
                            assert second.wait(timeout=15) == 0
                        finally:
                            if second.poll() is None:
                                second.kill()
                                second.wait(timeout=5)
                        after = {
                            p.pid
                            for p in descendants(child)
                            if p.name().startswith("quant-engine")
                        }
                        assert before == after and len(after) == 1, (
                            "Single-instance engine invariant failed"
                        )
                        tracked = descendants(child)
                        quit_file.touch()  # Application calls app.quit(), exercising its normal quit handler.
                    else:
                        child.terminate()
                    assert child.wait(timeout=20) in (
                        (0,) if kind == "app" else (0, -15, 1)
                    ), "Unexpected shutdown status"
                    assert_gone(tracked)
                    assert not healthy(port, version), (
                        "Engine still listening after quit"
                    )
                    with sqlite3.connect(
                        data / "db/quant-screen-trader.sqlite3"
                    ) as connection:
                        assert (
                            connection.execute(
                                "SELECT count(*) FROM packaging_smoke"
                            ).fetchone()[0]
                            == attempt + 1
                        )
                except BaseException:
                    output.seek(0)
                    print(output.read(), file=sys.stderr)
                    raise
                finally:
                    remaining = descendants(child)
                    if child.poll() is None:
                        child.kill()
                        child.wait(timeout=10)
                    # Failure cleanup must not mask the original assertion. Successful
                    # shutdown already asserted that all descendants terminated above.
                    for process in tracked + remaining:
                        try:
                            if process.is_running():
                                process.kill()
                        except psutil.NoSuchProcess:
                            pass
                    psutil.wait_procs(tracked + remaining, timeout=5)
            print(
                f"{kind} smoke {attempt + 1}/2 PASS: health, writable/preserved data, clean exit, no orphans"
            )


if __name__ == "__main__":
    smoke(sys.argv[1], str(Path(sys.argv[2]).resolve()), sys.argv[3])
