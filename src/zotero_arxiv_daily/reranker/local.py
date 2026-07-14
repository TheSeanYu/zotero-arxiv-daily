from .base import BaseReranker, register_reranker
import logging
import warnings
import numpy as np
import json
from omegaconf import OmegaConf

from .cache import EmbeddingCache
@register_reranker("local")
class LocalReranker(BaseReranker):
    def get_similarity_score(self, s1: list[str], s2: list[str]) -> np.ndarray:
        from sentence_transformers import SentenceTransformer
        if not self.config.executor.debug:
            from transformers.utils import logging as transformers_logging
            from huggingface_hub.utils import logging as hf_logging
    
            transformers_logging.set_verbosity_error()
            hf_logging.set_verbosity_error()
            logging.getLogger("sentence_transformers").setLevel(logging.ERROR)
            logging.getLogger("sentence_transformers.SentenceTransformer").setLevel(logging.ERROR)
            logging.getLogger("transformers").setLevel(logging.ERROR)
            logging.getLogger("huggingface_hub").setLevel(logging.ERROR)
            logging.getLogger("huggingface_hub.utils._http").setLevel(logging.ERROR)
            warnings.filterwarnings("ignore", category=FutureWarning)

        encoder = SentenceTransformer(self.config.reranker.local.model, trust_remote_code=True)
        if self.config.reranker.local.encode_kwargs:
            encode_kwargs = self.config.reranker.local.encode_kwargs
        else:
            encode_kwargs = {}
        def encode(texts: list[str]) -> np.ndarray:
            return encoder.encode(texts, **encode_kwargs, show_progress_bar=True)

        s1_feature = encode(s1)
        if self.config.reranker.cache.enabled:
            namespace = json.dumps(
                {
                    "backend": "local",
                    "model": str(self.config.reranker.local.model),
                    "encode_kwargs": OmegaConf.to_container(
                        self.config.reranker.local.encode_kwargs, resolve=True
                    ),
                },
                sort_keys=True,
            )
            cache = EmbeddingCache(self.config.reranker.cache, namespace)
            s2_feature = cache.get_or_compute(s2, encode)
        else:
            s2_feature = encode(s2)
        sim = encoder.similarity(s1_feature, s2_feature)
        return sim.numpy()
