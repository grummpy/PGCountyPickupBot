"""The public tree does not carry a household address."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NEEDLE = "1301 McCormick"
ALLOWED = {
    "README.md",
    "docs/SOURCES.md",
}


def test_cover_is_a_jpeg_at_the_top_of_the_readme():
    cover = (ROOT / "docs" / "cover.jpg").read_bytes()
    assert cover[:3] == b"\xff\xd8\xff"
    readme = (ROOT / "README.md").read_text(encoding="utf-8").splitlines()
    assert any("docs/cover.jpg" in line for line in readme[:8])


def test_known_public_building_is_only_in_docs():
    hits: list[str] = []
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in {".git", ".venv", "__pycache__"} for part in path.parts):
            continue
        if path.suffix in {".jpg", ".png", ".pyc"}:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if NEEDLE in text:
            hits.append(str(path.relative_to(ROOT)))
    assert set(hits) == ALLOWED


def test_example_config_and_workflow_have_no_street_address():
    example = (ROOT / "config.example.yaml").read_text(encoding="utf-8")
    assert "address: null" in example
    workflow = (ROOT / ".github/workflows/publish-ics.yml").read_text(encoding="utf-8")
    assert "PGPICKUP_PUBLISH_ENABLED" in workflow
    assert "secrets.PGPICKUP_ADDRESS" in workflow
    assert "\n  schedule:" not in workflow
    assert "#   - cron:" in workflow
    assert NEEDLE not in workflow
    assert NEEDLE not in example
