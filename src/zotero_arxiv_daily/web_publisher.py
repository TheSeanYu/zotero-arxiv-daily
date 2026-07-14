from __future__ import annotations

from datetime import date, datetime
from html import escape
import os
from pathlib import Path
import re
import tempfile
from zoneinfo import ZoneInfo

from loguru import logger
from omegaconf import DictConfig

from .protocol import Paper


DATE_PAGE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}\.html$")


class WebPublisher:
    def __init__(self, config: DictConfig):
        self.output_dir = Path(config.output_dir).expanduser()
        if not self.output_dir.is_absolute():
            raise ValueError("web.output_dir must be an absolute path")
        self.title = str(config.title)
        self.timezone = ZoneInfo(str(config.timezone))
        self.archive_days = int(config.archive_days)
        if self.archive_days < 1:
            raise ValueError("web.archive_days must be at least 1")

    def publish(self, papers: list[Paper], published_on: date | None = None) -> Path:
        published_on = published_on or datetime.now(self.timezone).date()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        daily_path = self.output_dir / f"{published_on.isoformat()}.html"
        self._write_atomic(daily_path, render_daily_page(self.title, published_on, papers))
        self._prune_archive()
        self._write_atomic(self.output_dir / "index.html", self._render_index())
        logger.info(f"Published static HTML report: {daily_path}")
        return daily_path

    def _archive_pages(self) -> list[Path]:
        return sorted(
            (
                path
                for path in self.output_dir.iterdir()
                if path.is_file() and DATE_PAGE_PATTERN.fullmatch(path.name)
            ),
            reverse=True,
        )

    def _prune_archive(self) -> None:
        for path in self._archive_pages()[self.archive_days:]:
            path.unlink()
            logger.info(f"Deleted expired static HTML report: {path}")

    def _render_index(self) -> str:
        links = "\n".join(
            f'<li><a href="{escape(path.name)}">{escape(path.stem)}</a></li>'
            for path in self._archive_pages()
        )
        body = f'<main><h1>{escape(self.title)}</h1><ul class="archive">{links}</ul></main>'
        return page_shell(self.title, body)

    @staticmethod
    def _write_atomic(path: Path, content: str) -> None:
        temporary_path = None
        try:
            with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", dir=path.parent, prefix=".web-", suffix=".tmp", delete=False
            ) as temporary:
                temporary.write(content)
                temporary.flush()
                os.fsync(temporary.fileno())
                temporary_path = Path(temporary.name)
            os.replace(temporary_path, path)
            temporary_path = None
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)


def render_daily_page(title: str, published_on: date, papers: list[Paper]) -> str:
    cards = "\n".join(render_paper_card(paper) for paper in papers)
    if not cards:
        cards = '<p class="empty">今日没有达到阈值的论文。</p>'
    body = (
        f'<header><p class="date">{published_on.isoformat()}</p><h1>{escape(title)}</h1>'
        f'<p class="count">{len(papers)} 篇论文</p></header><main>{cards}</main>'
    )
    return page_shell(f"{title} - {published_on.isoformat()}", body)


def render_paper_card(paper: Paper) -> str:
    summary = paper.structured_summary or {}
    authors = "、".join(escape(author) for author in paper.authors)
    score = "-" if paper.score is None else f"{paper.score:.2f}"
    sections = (
        ("研究背景", summary.get("background", "")),
        ("核心方法", summary.get("method", "")),
        ("主要贡献", summary.get("contributions", "")),
        ("实验与结果", summary.get("results", "")),
        ("局限与展望", summary.get("limitations", "")),
    )
    details = "".join(
        f"<section><h3>{heading}</h3><p>{escape(text)}</p></section>"
        for heading, text in sections
        if text
    )
    tldr = summary.get("tldr") or paper.tldr or paper.abstract
    pdf_url = paper.pdf_url or paper.url
    return f'''<article>
<div class="paper-heading"><div><h2>{escape(paper.title)}</h2><p class="authors">{authors}</p></div><span class="score">{score}</span></div>
<p class="tldr">{escape(tldr)}</p>
{details}
<div class="links"><a href="{escape(paper.url)}">arXiv</a><a href="{escape(pdf_url)}">PDF</a></div>
</article>'''


def page_shell(title: str, body: str) -> str:
    return f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)}</title><style>
:root {{ color-scheme: light; --ink:#18201d; --muted:#66706c; --line:#d8dedb; --paper:#f7f8f6; --accent:#176b52; }}
* {{ box-sizing:border-box; }} body {{ margin:0; background:var(--paper); color:var(--ink); font:16px/1.65 system-ui,sans-serif; }}
header, main {{ width:min(960px,calc(100% - 32px)); margin:auto; }} header {{ padding:48px 0 24px; border-bottom:1px solid var(--line); }}
h1,h2,h3,p {{ margin-top:0; }} h1 {{ font-size:32px; }} h2 {{ font-size:21px; }} h3 {{ font-size:15px; margin-bottom:6px; }}
.date,.count,.authors {{ color:var(--muted); }} main {{ padding:24px 0 64px; }} article {{ padding:24px 0; border-bottom:1px solid var(--line); }}
.paper-heading {{ display:grid; grid-template-columns:1fr auto; gap:20px; }} .score {{ color:var(--accent); font-weight:700; }}
.tldr {{ padding-left:14px; border-left:3px solid var(--accent); }} .links {{ display:flex; gap:16px; }} a {{ color:var(--accent); }}
.archive {{ padding-left:20px; }} @media (max-width:600px) {{ header {{ padding-top:28px; }} h1 {{ font-size:26px; }} }}
</style></head><body>{body}</body></html>'''
