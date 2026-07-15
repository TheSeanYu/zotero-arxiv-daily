import sys
from types import ModuleType

import pytest
from omegaconf import OmegaConf

from zotero_arxiv_daily.reranker import local as local_reranker
from zotero_arxiv_daily.reranker.local import LocalReranker


@pytest.mark.slow
def test_local_reranker(config):
    reranker = LocalReranker(config)
    score = reranker.get_similarity_score(["hello", "world"], ["ping"])
    assert score.shape == (2, 1)


def test_load_sentence_transformer_retries_hf_endpoint(monkeypatch):
    monkeypatch.delenv("HF_ENDPOINT", raising=False)
    calls = []

    class FakeSentenceTransformer:
        def __init__(self, model, trust_remote_code=False):
            calls.append((model, trust_remote_code))
            if len(calls) == 1:
                raise RuntimeError("default endpoint failed")

    fake_module = ModuleType("sentence_transformers")
    fake_module.SentenceTransformer = FakeSentenceTransformer
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_module)

    constants = ModuleType("huggingface_hub.constants")
    constants.ENDPOINT = "https://huggingface.co"
    constants.HUGGINGFACE_CO_URL_HOME = "https://huggingface.co/"
    constants.HUGGINGFACE_CO_URL_TEMPLATE = (
        "https://huggingface.co/{repo_id}/resolve/{revision}/{filename}"
    )
    monkeypatch.setitem(sys.modules, "huggingface_hub.constants", constants)

    config = OmegaConf.create(
        {
            "reranker": {
                "local": {
                    "hf_endpoint_fallbacks": ["https://hf-mirror.com"],
                }
            }
        }
    )

    try:
        model = local_reranker._load_sentence_transformer("test-model", config)
    finally:
        monkeypatch.delenv("HF_ENDPOINT", raising=False)

    assert isinstance(model, FakeSentenceTransformer)
    assert calls == [("test-model", True), ("test-model", True)]
    assert constants.ENDPOINT == "https://hf-mirror.com"
    assert constants.HUGGINGFACE_CO_URL_TEMPLATE == (
        "https://hf-mirror.com/{repo_id}/resolve/{revision}/{filename}"
    )
