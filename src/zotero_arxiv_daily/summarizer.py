from __future__ import annotations

import json
import re

from loguru import logger
from openai import OpenAI
from omegaconf import DictConfig
import tiktoken

from .protocol import Paper


SUMMARY_FIELDS = (
    "tldr",
    "background",
    "method",
    "contributions",
    "results",
    "limitations",
)

_ENCODER = None
_ENCODER_ATTEMPTED = False


class StructuredSummarizer:
    def __init__(self, client: OpenAI, llm_config: DictConfig, summary_config: DictConfig):
        self.client = client
        self.llm_config = llm_config
        self.language = str(summary_config.language)
        self.max_input_tokens = int(summary_config.max_input_tokens)
        self.chunk_tokens = int(summary_config.chunk_tokens)
        if self.max_input_tokens < 1 or self.chunk_tokens < 1:
            raise ValueError("summary token limits must be positive")
        self.encoder = self._load_encoder()

    def summarize(self, paper: Paper) -> dict[str, str]:
        source = paper.full_text or paper.abstract
        if not source:
            summary = self._fallback(paper)
            paper.structured_summary = summary
            return summary

        try:
            chunks = self._split_source(source)
            if len(chunks) == 1:
                summary = self._request_summary(paper, chunks[0])
            else:
                notes = [self._request_chunk_notes(paper, chunk) for chunk in chunks]
                summary = self._request_summary(paper, "\n\n--- CHUNK NOTES ---\n".join(notes))
        except Exception as exc:
            logger.warning(f"Failed to generate structured summary for {paper.url}: {exc}")
            summary = self._fallback(paper)

        paper.structured_summary = summary
        return summary

    @staticmethod
    def _load_encoder():
        global _ENCODER, _ENCODER_ATTEMPTED
        if _ENCODER_ATTEMPTED:
            return _ENCODER
        _ENCODER_ATTEMPTED = True
        try:
            _ENCODER = tiktoken.encoding_for_model("gpt-4o")
        except Exception as exc:
            logger.warning(
                "Could not load the tiktoken encoding; using conservative character "
                f"chunking instead: {exc}"
            )
        return _ENCODER

    def _split_source(self, source: str) -> list[str]:
        if self.encoder is not None:
            tokens = self.encoder.encode(source)[: self.max_input_tokens]
            return [
                self.encoder.decode(tokens[index:index + self.chunk_tokens])
                for index in range(0, len(tokens), self.chunk_tokens)
            ]

        # One Unicode code point per token is conservative for mixed Chinese/English
        # scientific text and guarantees chunks do not grow beyond the configured cap.
        source = source[: self.max_input_tokens]
        return [
            source[index:index + self.chunk_tokens]
            for index in range(0, len(source), self.chunk_tokens)
        ]

    def _request_chunk_notes(self, paper: Paper, chunk: str) -> str:
        response = self.client.chat.completions.create(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Extract factual notes from one chunk of a scientific paper. "
                        f"Write in {self.language}. Preserve methods, quantitative results, "
                        "contributions, assumptions, and limitations. Do not invent missing facts."
                    ),
                },
                {"role": "user", "content": f"Title: {paper.title}\n\n{chunk}"},
            ],
            **self._generation_kwargs(),
        )
        return response.choices[0].message.content or ""

    def _request_summary(self, paper: Paper, content: str) -> dict[str, str]:
        fields = ", ".join(SUMMARY_FIELDS)
        response = self.client.chat.completions.create(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Summarize a scientific paper using only the supplied content. "
                        f"Write in {self.language}. Return one JSON object with exactly these "
                        f"string fields: {fields}. Use an empty string when evidence is absent. "
                        "Do not wrap the JSON in Markdown."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Title: {paper.title}\nAuthors: {', '.join(paper.authors)}\n\n{content}"
                    ),
                },
            ],
            **self._generation_kwargs(),
        )
        return parse_summary_json(response.choices[0].message.content or "")

    def _generation_kwargs(self) -> dict:
        return dict(self.llm_config.get("generation_kwargs", {}))

    @staticmethod
    def _fallback(paper: Paper) -> dict[str, str]:
        return {
            "tldr": paper.tldr or paper.abstract,
            "background": "",
            "method": "",
            "contributions": "",
            "results": "",
            "limitations": "",
        }


def parse_summary_json(content: str) -> dict[str, str]:
    match = re.search(r"\{.*\}", content, flags=re.DOTALL)
    if match is None:
        raise ValueError("LLM response did not contain a JSON object")
    data = json.loads(match.group(0))
    if not isinstance(data, dict):
        raise ValueError("LLM summary must be a JSON object")
    summary = {}
    for field in SUMMARY_FIELDS:
        value = data.get(field, "")
        if not isinstance(value, str):
            raise ValueError(f"LLM summary field {field!r} must be a string")
        summary[field] = value.strip()
    return summary
