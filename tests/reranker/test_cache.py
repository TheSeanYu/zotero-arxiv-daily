import os
import sqlite3
import time

import numpy as np
from omegaconf import OmegaConf

from zotero_arxiv_daily.reranker.cache import EmbeddingCache


def make_config(path, *, max_entries=10, max_age_days=30):
    return OmegaConf.create(
        {
            "path": str(path),
            "max_entries": max_entries,
            "max_age_days": max_age_days,
        }
    )


def test_cache_reuses_embeddings_and_preserves_order(tmp_path):
    cache = EmbeddingCache(make_config(tmp_path / "cache.sqlite3"), "model-a")
    calls = []

    def compute(texts):
        calls.append(list(texts))
        return np.array([[len(text), len(text) + 1] for text in texts], dtype=np.float32)

    first = cache.get_or_compute(["alpha", "beta", "alpha"], compute)
    second = cache.get_or_compute(["beta", "alpha"], compute)

    assert calls == [["alpha", "beta"]]
    np.testing.assert_array_equal(first[0], first[2])
    np.testing.assert_array_equal(second[0], first[1])
    assert os.stat(cache.path).st_mode & 0o777 == 0o600


def test_cache_content_change_creates_new_entry(tmp_path):
    cache = EmbeddingCache(make_config(tmp_path / "cache.sqlite3"), "model-a")
    cache.get_or_compute(["old abstract"], lambda texts: np.ones((len(texts), 2)))
    cache.get_or_compute(["updated abstract"], lambda texts: np.ones((len(texts), 2)))
    assert cache.count() == 2


def test_cache_prunes_least_recently_used_entries(tmp_path):
    cache = EmbeddingCache(
        make_config(tmp_path / "cache.sqlite3", max_entries=2), "model-a"
    )
    cache.get_or_compute(["one", "two", "three"], lambda texts: np.ones((len(texts), 2)))
    assert cache.count() == 2


def test_cache_prunes_stale_entries(tmp_path):
    cache = EmbeddingCache(
        make_config(tmp_path / "cache.sqlite3", max_age_days=1), "model-a"
    )
    cache.get_or_compute(["old"], lambda texts: np.ones((len(texts), 2)))
    with sqlite3.connect(cache.path) as connection:
        connection.execute(
            "UPDATE embeddings SET last_access = ?", (time.time() - 2 * 86400,)
        )
    assert cache.cleanup() == 1
    assert cache.count() == 0
