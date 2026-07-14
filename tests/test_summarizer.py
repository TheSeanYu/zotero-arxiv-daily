import json
from types import SimpleNamespace

from omegaconf import OmegaConf

from tests.canned_responses import make_sample_paper
from zotero_arxiv_daily.summarizer import SUMMARY_FIELDS, StructuredSummarizer, parse_summary_json


def make_client(responses):
    values = iter(responses)

    def create(**kwargs):
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=next(values)))],
        )

    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


def make_configs(chunk_tokens=8000):
    llm = OmegaConf.create({"generation_kwargs": {"model": "test-model"}})
    summary = OmegaConf.create(
        {
            "language": "Chinese",
            "max_input_tokens": 24000,
            "chunk_tokens": chunk_tokens,
        }
    )
    return llm, summary


def summary_json(**overrides):
    data = {field: f"value-{field}" for field in SUMMARY_FIELDS}
    data.update(overrides)
    return json.dumps(data)


def test_parse_summary_json_accepts_surrounding_text():
    parsed = parse_summary_json(f"result:\n{summary_json()}\nend")
    assert parsed["method"] == "value-method"


def test_summarize_stores_structured_summary():
    llm, summary = make_configs()
    paper = make_sample_paper(full_text="short full text")
    summarizer = StructuredSummarizer(make_client([summary_json()]), llm, summary)

    result = summarizer.summarize(paper)
    assert result["tldr"] == "value-tldr"
    assert paper.structured_summary == result


def test_summarize_long_text_uses_chunk_notes_then_reduces():
    llm, summary = make_configs(chunk_tokens=5)
    paper = make_sample_paper(full_text="one two three four five six seven eight nine ten")
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        system_prompt = kwargs["messages"][0]["content"]
        content = summary_json() if "Return one JSON object" in system_prompt else "chunk notes"
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        )

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    summarizer = StructuredSummarizer(client, llm, summary)

    result = summarizer.summarize(paper)
    assert result["results"] == "value-results"
    assert len(calls) > 2


def test_summarize_falls_back_when_json_is_invalid():
    llm, summary = make_configs()
    paper = make_sample_paper(full_text="short", tldr="existing tldr")
    summarizer = StructuredSummarizer(make_client(["not json"]), llm, summary)

    result = summarizer.summarize(paper)
    assert result["tldr"] == "existing tldr"
    assert result["method"] == ""
