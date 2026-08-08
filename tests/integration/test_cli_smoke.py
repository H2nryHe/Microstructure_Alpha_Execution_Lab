from pathlib import Path

from microalpha.cli import main


def test_cli_smoke_generates_manifest(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.yaml"

    exit_code = main(
        [
            "--config-dir",
            "configs",
            "--manifest-out",
            str(manifest_path),
            "--random-seed",
            "42",
        ]
    )

    assert exit_code == 0
    manifest_text = manifest_path.read_text(encoding="utf-8")
    assert "run_id:" in manifest_text
    assert "config_hash:" in manifest_text
    assert "instrument:" in manifest_text
