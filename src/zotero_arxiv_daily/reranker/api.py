from .base import BaseReranker, register_reranker
from openai import OpenAI
import numpy as np
import json

from .cache import EmbeddingCache
@register_reranker("api")
class ApiReranker(BaseReranker):
    def get_similarity_score(self, s1: list[str], s2: list[str]) -> np.ndarray:
        client = OpenAI(api_key=self.config.reranker.api.key, base_url=self.config.reranker.api.base_url)
        batch_size = self.config.reranker.api.get("batch_size") or 64
        def embed(texts: list[str]) -> np.ndarray:
            embeddings = []
            for i in range(0, len(texts), batch_size):
                batch = texts[i:i + batch_size]
                response = client.embeddings.create(
                    input=batch,
                    model=self.config.reranker.api.model
                )
                embeddings.extend([r.embedding for r in response.data])
            return np.asarray(embeddings)

        s1_embeddings = embed(s1)
        if self.config.reranker.cache.enabled:
            namespace = json.dumps(
                {
                    "backend": "api",
                    "base_url": str(self.config.reranker.api.base_url),
                    "model": str(self.config.reranker.api.model),
                },
                sort_keys=True,
            )
            cache = EmbeddingCache(self.config.reranker.cache, namespace)
            s2_embeddings = cache.get_or_compute(s2, embed)
        else:
            s2_embeddings = embed(s2)
        s1_embeddings_normalized = s1_embeddings / np.linalg.norm(s1_embeddings, axis=1, keepdims=True)
        s2_embeddings_normalized = s2_embeddings / np.linalg.norm(s2_embeddings, axis=1, keepdims=True)
        sim = np.dot(s1_embeddings_normalized, s2_embeddings_normalized.T) # [n_s1, n_s2]
        return sim
