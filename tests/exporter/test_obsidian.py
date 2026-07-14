from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from omegaconf import OmegaConf

from tests.canned_responses import make_sample_paper
from zotero_arxiv_daily.exporter.obsidian import ObsidianExporter, extract_arxiv_id, slugify


def make_config(root: Path, *, dry_run: bool = True):
    return OmegaConf.create(
        {
            "obsidian": {
                "vault_path": str(root),
                "retention": {
                    "enabled": True,
                    "days": 7,
                    "timezone": "Asia/Shanghai",
                    "directory_format": "%Y-%m-%d",
                    "dry_run": dry_run,
                },
            }
        }
    )


def test_extract_arxiv_id_handles_abs_pdf_and_version():
    assert extract_arxiv_id(make_sample_paper()) == "2026.00001"
    paper = make_sample_paper(url="https://arxiv.org/abs/2401.12345v2", pdf_url=None)
    assert extract_arxiv_id(paper) == "2401.12345v2"


def test_slugify_preserves_readable_unicode_and_limits_length():
    assert slugify("A Study: 测试 / Transformers") == "a-study-测试-transformers"
    assert len(slugify("x" * 100)) == 50


def test_export_creates_date_directory_and_never_overwrites(tmp_path):
    exporter = ObsidianExporter(make_config(tmp_path))
    paper = make_sample_paper(score=7.5, tldr="Short summary")
    now = datetime(2026, 7, 13, 9, tzinfo=ZoneInfo("Asia/Shanghai"))

    exported = exporter.export([paper], now=now)
    assert len(exported) == 1
    note = exported[0]
    assert note.parent.name == "2026-07-13"
    assert 'arxiv_id: "2026.00001"' in note.read_text()

    original = note.read_text()
    paper.tldr = "This must not overwrite the note"
    assert exporter.export([paper], now=now) == []
    assert note.read_text() == original


def test_cleanup_only_removes_expired_date_directories(tmp_path):
    exporter = ObsidianExporter(make_config(tmp_path, dry_run=False))
    expired = tmp_path / "2026-07-06"
    retained = tmp_path / "2026-07-07"
    unrelated = tmp_path / "archive"
    for directory in (expired, retained, unrelated):
        directory.mkdir()
        (directory / "note.md").write_text("test")

    removed = exporter.cleanup(datetime(2026, 7, 13).date())
    assert removed == [expired]
    assert not expired.exists()
    assert retained.exists()
    assert unrelated.exists()


def test_cleanup_dry_run_does_not_delete(tmp_path):
    exporter = ObsidianExporter(make_config(tmp_path, dry_run=True))
    expired = tmp_path / "2026-07-01"
    expired.mkdir()

    assert exporter.cleanup(datetime(2026, 7, 13).date()) == [expired]
    assert expired.exists()


def test_export_rejects_symlinked_date_directory(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "vault"
    root.mkdir()
    (root / "2026-07-13").symlink_to(outside, target_is_directory=True)
    exporter = ObsidianExporter(make_config(root))

    now = datetime(2026, 7, 13, 9, tzinfo=ZoneInfo("Asia/Shanghai"))
    try:
        exporter.export([make_sample_paper()], now=now)
    except ValueError as exc:
        assert "outside exporter root" in str(exc)
    else:
        raise AssertionError("Expected exporter to reject a symlinked date directory")
