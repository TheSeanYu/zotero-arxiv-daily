from loguru import logger
from pyzotero import zotero
from omegaconf import DictConfig, ListConfig
from .utils import glob_match
from .retriever import get_retriever_cls
from .protocol import CorpusPaper
import random
from datetime import datetime
from .reranker import get_reranker_cls
from .construct_email import render_email
from .utils import send_email
from openai import OpenAI
from tqdm import tqdm
from .exporter import ObsidianExporter
from .summarizer import StructuredSummarizer
from .web_publisher import WebPublisher
from .retriever.arxiv_retriever import hydrate_arxiv_full_text


def normalize_path_patterns(patterns: list[str] | ListConfig | None, config_key: str) -> list[str] | None:
    if patterns is None:
        return None

    if not isinstance(patterns, (list, ListConfig)):
        raise TypeError(
            f"config.zotero.{config_key} must be a list of glob patterns or null, "
            'for example ["2026/survey/**"]. Single strings are not supported.'
        )

    if any(not isinstance(pattern, str) for pattern in patterns):
        raise TypeError(f"config.zotero.{config_key} must contain only glob pattern strings.")

    return list(patterns)


def select_publish_papers(
    papers: list,
    min_score: float,
    min_export_num: int,
    max_export_num: int | None,
) -> list:
    if min_export_num < 0:
        raise ValueError("exporter.min_export_num must be non-negative")
    if max_export_num is not None:
        max_export_num = int(max_export_num)
        if max_export_num < min_export_num:
            raise ValueError("exporter.max_export_num must be null or at least min_export_num")

    arxiv_papers = sorted(
        (
            paper
            for paper in papers
            if paper.source == "arxiv" and paper.score is not None
        ),
        key=lambda paper: paper.score,
        reverse=True,
    )
    threshold_count = sum(paper.score >= min_score for paper in arxiv_papers)
    selected_count = max(min_export_num, threshold_count)
    if max_export_num is not None:
        selected_count = min(selected_count, max_export_num)
    return arxiv_papers[:selected_count]


class Executor:
    def __init__(self, config:DictConfig):
        self.config = config
        self.include_path_patterns = normalize_path_patterns(config.zotero.include_path, "include_path")
        self.ignore_path_patterns = normalize_path_patterns(config.zotero.ignore_path, "ignore_path")
        self.retrievers = {
            source: get_retriever_cls(source)(config) for source in config.executor.source
        }
        self.reranker = get_reranker_cls(config.executor.reranker)(config)
        self.openai_client = OpenAI(api_key=config.llm.api.key, base_url=config.llm.api.base_url)
        self.summarizer = (
            StructuredSummarizer(self.openai_client, config.llm, config.exporter.summary)
            if config.exporter.enabled
            else None
        )
        self.exporter = (
            ObsidianExporter(config.exporter)
            if config.exporter.enabled and config.exporter.obsidian.enabled
            else None
        )
        self.web_publisher = (
            WebPublisher(config.web)
            if config.exporter.enabled and config.web.enabled
            else None
        )
    def fetch_zotero_corpus(self) -> list[CorpusPaper]:
        logger.info("Fetching zotero corpus")
        zot = zotero.Zotero(self.config.zotero.user_id, 'user', self.config.zotero.api_key)
        collections = zot.everything(zot.collections())
        collections = {c['key']:c for c in collections}
        corpus = zot.everything(zot.items(itemType='conferencePaper || journalArticle || preprint'))
        corpus = [c for c in corpus if c['data']['abstractNote'] != '']
        def get_collection_path(col_key:str) -> str:
            if p := collections[col_key]['data']['parentCollection']:
                return get_collection_path(p) + '/' + collections[col_key]['data']['name']
            else:
                return collections[col_key]['data']['name']
        for c in corpus:
            paths = [get_collection_path(col) for col in c['data']['collections']]
            c['paths'] = paths
        logger.info(f"Fetched {len(corpus)} zotero papers")
        return [CorpusPaper(
            title=c['data']['title'],
            abstract=c['data']['abstractNote'],
            added_date=datetime.strptime(c['data']['dateAdded'], '%Y-%m-%dT%H:%M:%SZ'),
            paths=c['paths']
        ) for c in corpus]
    
    def filter_corpus(self, corpus:list[CorpusPaper]) -> list[CorpusPaper]:
        if self.include_path_patterns:
            logger.info(f"Selecting zotero papers matching include_path: {self.include_path_patterns}")
            corpus = [
                c for c in corpus
                if any(
                    glob_match(path, pattern)
                    for path in c.paths
                    for pattern in self.include_path_patterns
                )
            ]
        if self.ignore_path_patterns:
            logger.info(f"Excluding zotero papers matching ignore_path: {self.ignore_path_patterns}")
            corpus = [
                c for c in corpus
                if not any(
                    glob_match(path, pattern)
                    for path in c.paths
                    for pattern in self.ignore_path_patterns
                )
            ]
        if self.include_path_patterns or self.ignore_path_patterns:
            samples = random.sample(corpus, min(5, len(corpus)))
            samples = '\n'.join([c.title + ' - ' + '\n'.join(c.paths) for c in samples])
            logger.info(f"Selected {len(corpus)} zotero papers:\n{samples}\n...")
        return corpus

    
    def run(self):
        corpus = self.fetch_zotero_corpus()
        corpus = self.filter_corpus(corpus)
        if len(corpus) == 0:
            logger.error(f"No zotero papers found. Please check your zotero settings:\n{self.config.zotero}")
            return
        all_papers = []
        for source, retriever in self.retrievers.items():
            logger.info(f"Retrieving {source} papers...")
            papers = retriever.retrieve_papers()
            if len(papers) == 0:
                logger.info(f"No {source} papers found")
                continue
            logger.info(f"Retrieved {len(papers)} {source} papers")
            all_papers.extend(papers)
        logger.info(f"Total {len(all_papers)} papers retrieved from all sources")
        reranked_papers = []
        if len(all_papers) > 0:
            logger.info("Reranking papers...")
            ranked_papers = self.reranker.rerank(all_papers, corpus)
            reranked_papers = ranked_papers[:self.config.executor.max_paper_num]
            if ranked_papers:
                logger.info(
                    "Top reranker scores: "
                    + ", ".join(f"{paper.score:.3f}" for paper in ranked_papers[:10])
                )
            logger.info("Generating TLDR and affiliations...")
            for p in tqdm(reranked_papers):
                p.generate_tldr(self.openai_client, self.config.llm)
                p.generate_affiliations(self.openai_client, self.config.llm)
            if self.summarizer is not None:
                publish_papers = select_publish_papers(
                    ranked_papers,
                    float(self.config.exporter.min_score),
                    int(self.config.exporter.min_export_num),
                    self.config.exporter.max_export_num,
                )
                logger.info(
                    f"Selected {len(publish_papers)} papers: top "
                    f"{self.config.exporter.min_export_num} plus all scores >= "
                    f"{self.config.exporter.min_score}"
                )
                logger.info(f"Fetching full text for {len(publish_papers)} selected arXiv papers...")
                for paper in tqdm(publish_papers):
                    hydrate_arxiv_full_text(paper)
                logger.info(f"Generating structured summaries for {len(publish_papers)} papers...")
                for paper in tqdm(publish_papers):
                    self.summarizer.summarize(paper)
                if self.exporter is not None:
                    self.exporter.export(publish_papers)
                if self.web_publisher is not None:
                    self.web_publisher.publish(publish_papers)
        elif not self.config.executor.send_empty:
            logger.info("No new papers found. Publishing maintenance will still run.")

        if self.summarizer is not None and not reranked_papers:
            if self.exporter is not None:
                self.exporter.export([])
            if self.web_publisher is not None:
                self.web_publisher.publish([])

        if self.config.email.enabled and (reranked_papers or self.config.executor.send_empty):
            logger.info("Sending email...")
            email_content = render_email(reranked_papers)
            send_email(self.config, email_content)
            logger.info("Email sent successfully")
        else:
            logger.info("Email delivery is disabled or there are no papers to send")
