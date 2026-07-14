from __future__ import annotations

from datetime import date, datetime, timedelta
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import unicodedata
from zoneinfo import ZoneInfo

from loguru import logger
from omegaconf import DictConfig

from ..protocol import Paper


ARXIV_ID_PATTERN = re.compile(r"/(?:abs|pdf)/([^/?#]+?)(?:\.pdf)?(?:[?#]|$)")


def extract_arxiv_id(paper: Paper) -> str:
    for url in (paper.url, paper.pdf_url):
        if url and (match := ARXIV_ID_PATTERN.search(url)):
            return match.group(1)
    raise ValueError(f"Cannot extract arXiv ID from {paper.url!r}")


def slugify(title: str, max_length: int = 50) -> str:
    normalized = unicodedata.normalize("NFKC", title).lower()
    slug = re.sub(r"[^\w]+", "-", normalized, flags=re.UNICODE).strip("-_")
    return slug[:max_length].rstrip("-_") or "paper"


def render_paper(paper: Paper, arxiv_id: str, exported_on: date) -> str:
    authors = json.dumps(paper.authors, ensure_ascii=False)
    title = json.dumps(paper.title, ensure_ascii=False)
    pdf_url = json.dumps(paper.pdf_url or paper.url, ensure_ascii=False)
    score = "null" if paper.score is None else f"{paper.score:.4f}"
    summary = paper.structured_summary or {}
    tldr = summary.get("tldr") or paper.tldr or paper.abstract

    return f'''---
title: {title}
authors: {authors}
arxiv_id: "{arxiv_id}"
pdf_url: {pdf_url}
exported: {exported_on.isoformat()}
relevance_score: {score}
tags: [arxiv-daily]
source: arxiv
status: summarized
---

# {paper.title}

> **一句话**: {tldr}

## 研究背景

{summary.get("background", "")}

## 核心方法

{summary.get("method", "")}

## 主要贡献

{summary.get("contributions", "")}

## 实验与结果

{summary.get("results", "")}

## 局限与展望

{summary.get("limitations", "")}

## 个人备注

## 原文摘要

{paper.abstract}

## 相关链接

- [arXiv]({paper.url})
- [PDF]({paper.pdf_url or paper.url})
'''


class ObsidianExporter:
    def __init__(self, config: DictConfig):
        self.config = config
        self.root = Path(config.obsidian.vault_path).expanduser()
        if not self.root.is_absolute():
            raise ValueError("exporter.obsidian.vault_path must be an absolute path")

        retention = config.obsidian.retention
        self.retention_enabled = bool(retention.enabled)
        self.retention_days = int(retention.days)
        if self.retention_days < 1:
            raise ValueError("exporter.obsidian.retention.days must be at least 1")
        self.timezone = ZoneInfo(str(retention.timezone))
        self.directory_format = str(retention.directory_format)
        self.dry_run = bool(retention.dry_run)

    def export(self, papers: list[Paper], now: datetime | None = None) -> list[Path]:
        current = now.astimezone(self.timezone) if now else datetime.now(self.timezone)
        today = current.date()
        day_directory = self.root / today.strftime(self.directory_format)
        self._ensure_day_directory(day_directory)

        existing_ids = self._existing_arxiv_ids()
        exported = []
        for paper in papers:
            if paper.source != "arxiv":
                continue
            try:
                arxiv_id = extract_arxiv_id(paper)
            except ValueError as exc:
                logger.warning(str(exc))
                continue
            if arxiv_id in existing_ids:
                logger.info(f"Skipping already exported arXiv paper {arxiv_id}")
                continue

            filename = f"{arxiv_id}-{slugify(paper.title)}.md"
            target = day_directory / filename
            if self._publish_exclusive(target, render_paper(paper, arxiv_id, today)):
                existing_ids.add(arxiv_id)
                exported.append(target)
                logger.info(f"Exported Obsidian note: {target}")

        if self.retention_enabled:
            self.cleanup(today)
        return exported

    def _ensure_day_directory(self, day_directory: Path) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        if self.root.is_symlink():
            raise ValueError("exporter.obsidian.vault_path must not be a symbolic link")
        day_directory.mkdir(exist_ok=True)
        if day_directory.is_symlink() or day_directory.resolve().parent != self.root.resolve():
            raise ValueError(f"Refusing to write outside exporter root: {day_directory}")

    def cleanup(self, today: date) -> list[Path]:
        if not self.root.exists():
            return []

        cutoff = today - timedelta(days=self.retention_days - 1)
        expired = []
        for child in self.root.iterdir():
            if not child.is_dir():
                continue
            try:
                directory_date = datetime.strptime(child.name, self.directory_format).date()
            except ValueError:
                continue
            if directory_date >= cutoff:
                continue

            resolved = child.resolve()
            if resolved.parent != self.root.resolve():
                raise ValueError(f"Refusing to delete path outside exporter root: {resolved}")
            expired.append(child)
            if self.dry_run:
                logger.info(f"Retention dry run would delete: {child}")
            else:
                shutil.rmtree(child)
                logger.info(f"Deleted expired Obsidian directory: {child}")
        return expired

    def _existing_arxiv_ids(self) -> set[str]:
        if not self.root.exists():
            return set()
        return {
            path.name.split("-", 1)[0]
            for path in self.root.glob("*/*.md")
            if "-" in path.name
        }

    @staticmethod
    def _publish_exclusive(target: Path, content: str) -> bool:
        temporary_path = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=target.parent,
                prefix=".arxiv-daily-",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary.write(content)
                temporary.flush()
                os.fsync(temporary.fileno())
                temporary_path = Path(temporary.name)
            try:
                os.link(temporary_path, target)
            except FileExistsError:
                logger.info(f"Skipping existing Obsidian note: {target}")
                return False
            return True
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
