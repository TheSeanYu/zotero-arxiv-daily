from datetime import date
from pathlib import Path

from omegaconf import OmegaConf

from tests.canned_responses import make_sample_paper
from zotero_arxiv_daily.web_publisher import WebPublisher


def make_config(output_dir: Path, archive_days=30):
    return OmegaConf.create(
        {
            "output_dir": str(output_dir),
            "title": "Daily Test",
            "timezone": "Asia/Shanghai",
            "archive_days": archive_days,
        }
    )


def test_publish_creates_daily_page_and_index(tmp_path):
    paper = make_sample_paper(score=7.25, tldr="TLDR")
    paper.structured_summary = {
        "tldr": "Structured TLDR",
        "background": "Background",
        "method": "Method",
        "contributions": "Contribution",
        "results": "Result",
        "limitations": "Limitation",
    }
    publisher = WebPublisher(make_config(tmp_path))

    daily = publisher.publish([paper], date(2026, 7, 14))
    assert daily.name == "2026-07-14.html"
    assert "Structured TLDR" in daily.read_text()
    assert "Sample Paper Title" in daily.read_text()
    assert "2026-07-14.html" in (tmp_path / "index.html").read_text()


def test_publish_prunes_oldest_pages(tmp_path):
    publisher = WebPublisher(make_config(tmp_path, archive_days=2))
    paper = make_sample_paper()
    publisher.publish([paper], date(2026, 7, 12))
    publisher.publish([paper], date(2026, 7, 13))
    publisher.publish([paper], date(2026, 7, 14))

    assert not (tmp_path / "2026-07-12.html").exists()
    assert (tmp_path / "2026-07-13.html").exists()
    assert (tmp_path / "2026-07-14.html").exists()
