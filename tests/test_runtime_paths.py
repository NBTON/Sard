from pathlib import Path

from sard.runtime_paths import output_root


def test_output_root_keeps_local_configuration(monkeypatch, tmp_path):
    configured = tmp_path / "artifacts"
    monkeypatch.delenv("VERCEL", raising=False)
    monkeypatch.setenv("SARD_OUTPUT_ROOT", str(configured))

    assert output_root() == configured.resolve()


def test_output_root_moves_read_only_vercel_path_to_temp(monkeypatch, tmp_path):
    temp_root = tmp_path / "tmp"
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.setenv("SARD_OUTPUT_ROOT", "output/runs")
    monkeypatch.setattr("sard.runtime_paths.tempfile.gettempdir", lambda: str(temp_root))

    assert output_root() == temp_root.resolve() / "sard-output"


def test_output_root_accepts_explicit_vercel_temp_path(monkeypatch, tmp_path):
    temp_root = tmp_path / "tmp"
    configured = temp_root / "custom-output"
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.setenv("SARD_OUTPUT_ROOT", str(configured))
    monkeypatch.setattr("sard.runtime_paths.tempfile.gettempdir", lambda: str(temp_root))

    assert output_root() == configured.resolve()
