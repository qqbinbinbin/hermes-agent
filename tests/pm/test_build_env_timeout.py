"""Explicit project builds retain a bounded, caller-selected PM deadline."""
from pathlib import Path

import pytest

from pm import build_env
import pm.client


@pytest.mark.parametrize("seconds, expected", [(None, 1800), (7200, 7200)])
def test_project_build_timeout_reaches_pm_operation(tmp_path, monkeypatch, seconds, expected):
    calls = []

    def operation(name, arguments):
        calls.append((name, arguments))
        return tmp_path / "out/bin/python"

    monkeypatch.setattr(pm.client, "_python_operation", operation)
    argv = ["--source", str(tmp_path), "--out", str(tmp_path / "out")]
    if seconds is not None:
        argv += ["--timeout", str(seconds)]
    assert build_env.main(argv) == 0
    assert len(calls) == 1
    name, arguments = calls[0]
    assert name == "build_environment"
    assert arguments["timeout"] == expected
    assert arguments["frozen"] is True
    assert arguments["explicit"] is True
    assert arguments["source"] == Path(tmp_path)


@pytest.mark.parametrize("seconds", ["0", "-1", "1.5"])
def test_invalid_project_timeout_is_rejected_before_pm(tmp_path, monkeypatch, capsys, seconds):
    def forbidden(*args, **kwargs):
        pytest.fail("invalid timeout reached a PM operation")

    monkeypatch.setattr(pm.client, "_python_operation", forbidden)
    with pytest.raises(SystemExit) as exc:
        build_env.main(["--source", str(tmp_path), "--out", str(tmp_path / "out"),
                        "--timeout", seconds])
    assert exc.value.code == 2
    assert "--timeout" in capsys.readouterr().err
