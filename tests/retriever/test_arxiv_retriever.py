"""Tests for ArxivRetriever."""

import time
from types import SimpleNamespace

import feedparser

from zotero_arxiv_daily.retriever.arxiv_retriever import (
    ArxivRetriever,
    _run_with_hard_timeout,
    hydrate_arxiv_full_text,
)
import zotero_arxiv_daily.retriever.arxiv_retriever as arxiv_retriever
from tests.canned_responses import make_sample_paper


def _sleep_and_return(value: str, delay_seconds: float) -> str:
    time.sleep(delay_seconds)
    return value


def _raise_runtime_error() -> None:
    raise RuntimeError("boom")


def test_arxiv_retriever(config, mock_feedparser, monkeypatch):
    monkeypatch.setattr("zotero_arxiv_daily.retriever.base.sleep", lambda _: None)

    # The RSS fixture gives us paper IDs.  After feedparser, the code calls
    # arxiv.Client().results(search) which makes real HTTP requests.  We mock
    # the arxiv Client so the test stays offline.
    new_entries = [
        e for e in mock_feedparser.entries
        if e.get("arxiv_announce_type", "new") == "new"
    ]
    paper_ids = [e.id.removeprefix("oai:arXiv.org:") for e in new_entries]

    # Build fake ArxivResult-like objects matching each RSS entry
    fake_results = []
    for entry in new_entries:
        pid = entry.id.removeprefix("oai:arXiv.org:")
        fake_results.append(SimpleNamespace(
            title=entry.title,
            authors=[SimpleNamespace(name="Test Author")],
            summary="Test abstract",
            pdf_url=f"https://arxiv.org/pdf/{pid}",
            entry_id=f"https://arxiv.org/abs/{pid}",
            source_url=lambda pid=pid: f"https://arxiv.org/e-print/{pid}",
        ))

    class FakeClient:
        def __init__(self, **kw):
            pass
        def results(self, search):
            return iter(fake_results)

    monkeypatch.setattr(arxiv_retriever.arxiv, "Client", FakeClient)

    retriever = ArxivRetriever(config)
    papers = retriever.retrieve_papers()

    assert len(papers) == len(new_entries)
    assert set(p.title for p in papers) == set(e.title for e in new_entries)
    assert all(p.full_text is None for p in papers)


def test_hydrate_full_text_runs_only_for_selected_paper(monkeypatch):
    calls = []
    monkeypatch.setattr(
        arxiv_retriever,
        "extract_text_from_tar",
        lambda paper: calls.append(("tar", paper.entry_id)) or None,
    )
    monkeypatch.setattr(
        arxiv_retriever,
        "extract_text_from_html",
        lambda paper: calls.append(("html", paper.entry_id)) or "full text",
    )
    monkeypatch.setattr(
        arxiv_retriever,
        "extract_text_from_pdf",
        lambda paper: calls.append(("pdf", paper.entry_id)) or None,
    )
    paper = make_sample_paper(full_text=None)

    assert hydrate_arxiv_full_text(paper) == "full text"
    assert paper.full_text == "full text"
    assert calls == [("html", paper.url)]

    assert hydrate_arxiv_full_text(paper) == "full text"
    assert calls == [("html", paper.url)]


def test_convert_to_paper_normalizes_arxiv_urls_to_https(config):
    retriever = ArxivRetriever(config)
    raw = SimpleNamespace(
        title="Paper",
        authors=[SimpleNamespace(name="Author")],
        summary="Abstract",
        pdf_url="http://arxiv.org/pdf/2607.08784v1",
        entry_id="http://export.arxiv.org/abs/2607.08784v1",
    )

    paper = retriever.convert_to_paper(raw)
    assert paper.url == "https://arxiv.org/abs/2607.08784v1"
    assert paper.pdf_url == "https://arxiv.org/pdf/2607.08784v1"


def test_run_with_hard_timeout_returns_value():
    result = _run_with_hard_timeout(
        _sleep_and_return, ("done", 0.01), timeout=1, operation="test op", paper_title="paper"
    )
    assert result == "done"


def test_run_with_hard_timeout_returns_none_on_timeout(monkeypatch):
    warnings: list[str] = []
    monkeypatch.setattr(arxiv_retriever, "logger", SimpleNamespace(warning=warnings.append))
    result = _run_with_hard_timeout(
        _sleep_and_return, ("done", 1.0), timeout=0.01, operation="test op", paper_title="paper"
    )
    assert result is None
    assert "timed out" in warnings[0]


def test_run_with_hard_timeout_returns_none_on_failure(monkeypatch):
    warnings: list[str] = []
    monkeypatch.setattr(arxiv_retriever, "logger", SimpleNamespace(warning=warnings.append))
    result = _run_with_hard_timeout(
        _raise_runtime_error, (), timeout=1, operation="test op", paper_title="paper"
    )
    assert result is None
    assert "boom" in warnings[0]
