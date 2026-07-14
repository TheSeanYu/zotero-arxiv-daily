from __future__ import annotations

from collections.abc import Callable
import hashlib
import os
from pathlib import Path
import sqlite3
import time

from loguru import logger
import numpy as np
from omegaconf import DictConfig


class EmbeddingCache:
    def __init__(self, config: DictConfig, namespace: str):
        self.path = Path(str(config.path)).expanduser()
        if not self.path.is_absolute():
            raise ValueError("reranker.cache.path must be an absolute path")
        self.namespace = namespace
        self.max_entries = int(config.max_entries)
        self.max_age_days = config.get("max_age_days")
        if self.max_entries < 1:
            raise ValueError("reranker.cache.max_entries must be at least 1")
        if self.max_age_days is not None and int(self.max_age_days) < 1:
            raise ValueError("reranker.cache.max_age_days must be at least 1 or null")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def get_or_compute(
        self,
        texts: list[str],
        compute: Callable[[list[str]], np.ndarray],
    ) -> np.ndarray:
        if not texts:
            return np.empty((0, 0), dtype=np.float32)

        keys = [self._key(text) for text in texts]
        unique_texts = dict(zip(keys, texts))
        vectors = self._get_many(list(unique_texts))
        missing_keys = [key for key in unique_texts if key not in vectors]
        if missing_keys:
            missing_texts = [unique_texts[key] for key in missing_keys]
            computed = np.asarray(compute(missing_texts), dtype=np.float32)
            if computed.ndim != 2 or computed.shape[0] != len(missing_keys):
                raise ValueError("Embedding provider returned an unexpected shape")
            new_vectors = dict(zip(missing_keys, computed))
            self._put_many(new_vectors)
            vectors.update(new_vectors)

        hits = len(unique_texts) - len(missing_keys)
        logger.info(
            f"Zotero embedding cache: {hits} hits, {len(missing_keys)} misses, "
            f"{len(unique_texts)} unique abstracts"
        )
        return np.stack([vectors[key] for key in keys])

    def cleanup(self) -> int:
        now = time.time()
        removed = 0
        with self._connect() as connection:
            if self.max_age_days is not None:
                cutoff = now - int(self.max_age_days) * 86400
                cursor = connection.execute(
                    "DELETE FROM embeddings WHERE last_access < ?", (cutoff,)
                )
                removed += cursor.rowcount

            count = connection.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
            overflow = max(0, count - self.max_entries)
            if overflow:
                cursor = connection.execute(
                    """
                    DELETE FROM embeddings
                    WHERE rowid IN (
                        SELECT rowid FROM embeddings ORDER BY last_access ASC LIMIT ?
                    )
                    """,
                    (overflow,),
                )
                removed += cursor.rowcount
        if removed:
            logger.info(f"Removed {removed} expired/LRU embedding cache entries")
        return removed

    def count(self) -> int:
        with self._connect() as connection:
            return connection.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS embeddings (
                    namespace TEXT NOT NULL,
                    text_hash TEXT NOT NULL,
                    vector BLOB NOT NULL,
                    dimensions INTEGER NOT NULL,
                    last_access REAL NOT NULL,
                    PRIMARY KEY (namespace, text_hash)
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS embeddings_last_access ON embeddings(last_access)"
            )
        os.chmod(self.path, 0o600)
        self.cleanup()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=30)

    def _get_many(self, keys: list[str]) -> dict[str, np.ndarray]:
        if not keys:
            return {}
        now = time.time()
        result = {}
        with self._connect() as connection:
            for start in range(0, len(keys), 900):
                batch = keys[start:start + 900]
                placeholders = ",".join("?" for _ in batch)
                rows = connection.execute(
                    f"""
                    SELECT text_hash, vector, dimensions FROM embeddings
                    WHERE namespace = ? AND text_hash IN ({placeholders})
                    """,
                    (self.namespace, *batch),
                ).fetchall()
                for key, blob, dimensions in rows:
                    vector = np.frombuffer(blob, dtype=np.float32)
                    if vector.size == dimensions:
                        result[key] = vector.copy()
                if rows:
                    connection.executemany(
                        """
                        UPDATE embeddings SET last_access = ?
                        WHERE namespace = ? AND text_hash = ?
                        """,
                        [(now, self.namespace, row[0]) for row in rows],
                    )
        return result

    def _put_many(self, vectors: dict[str, np.ndarray]) -> None:
        now = time.time()
        rows = []
        for key, vector in vectors.items():
            normalized = np.asarray(vector, dtype=np.float32).reshape(-1)
            rows.append(
                (self.namespace, key, normalized.tobytes(), normalized.size, now)
            )
        with self._connect() as connection:
            connection.executemany(
                """
                INSERT INTO embeddings(namespace, text_hash, vector, dimensions, last_access)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(namespace, text_hash) DO UPDATE SET
                    vector = excluded.vector,
                    dimensions = excluded.dimensions,
                    last_access = excluded.last_access
                """,
                rows,
            )
        self.cleanup()

    @staticmethod
    def _key(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()
