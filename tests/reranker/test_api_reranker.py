"""Tests for ApiReranker — uses stub OpenAI client via monkeypatch."""

from types import SimpleNamespace

from omegaconf import open_dict

from zotero_arxiv_daily.reranker.api import ApiReranker


def test_api_reranker_similarity_shape(config, patch_openai):
    reranker = ApiReranker(config)
    score = reranker.get_similarity_score(["hello", "world"], ["ping"])
    assert score.shape == (2, 1)


def test_api_reranker_batching(config, patch_openai):
    reranker = ApiReranker(config)
    s1 = [f"text {i}" for i in range(5)]
    s2 = [f"corpus {i}" for i in range(3)]
    score = reranker.get_similarity_score(s1, s2)
    assert score.shape == (5, 3)


def test_api_reranker_caches_only_zotero_corpus(config, monkeypatch, tmp_path):
    calls = []

    def create(*, input, model):
        calls.append(list(input))
        return SimpleNamespace(
            data=[SimpleNamespace(embedding=[float(len(text)), 1.0]) for text in input]
        )

    client = SimpleNamespace(embeddings=SimpleNamespace(create=create))
    monkeypatch.setattr("zotero_arxiv_daily.reranker.api.OpenAI", lambda **kwargs: client)
    with open_dict(config):
        config.reranker.cache.enabled = True
        config.reranker.cache.path = str(tmp_path / "embeddings.sqlite3")

    reranker = ApiReranker(config)
    reranker.get_similarity_score(["candidate"], ["corpus one", "corpus two"])
    reranker.get_similarity_score(["candidate"], ["corpus one", "corpus two"])

    assert calls == [
        ["candidate"],
        ["corpus one", "corpus two"],
        ["candidate"],
    ]
