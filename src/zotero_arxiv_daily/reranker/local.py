from .base import BaseReranker, register_reranker
import logging
import os
import warnings
import numpy as np
import json
from omegaconf import OmegaConf
from loguru import logger

from .cache import EmbeddingCache


def _endpoint_fallbacks(config) -> list[str]:
    fallbacks = config.reranker.local.get("hf_endpoint_fallbacks", [])
    current = os.environ.get("HF_ENDPOINT")
    endpoints = []
    for endpoint in fallbacks or []:
        endpoint = str(endpoint).rstrip("/")
        if endpoint and endpoint != current and endpoint not in endpoints:
            endpoints.append(endpoint)
    return endpoints


def _apply_hf_endpoint(endpoint: str) -> None:
    endpoint = endpoint.rstrip("/")
    os.environ["HF_ENDPOINT"] = endpoint
    try:
        import huggingface_hub.constants as hf_constants

        hf_constants.ENDPOINT = endpoint
        hf_constants.HUGGINGFACE_CO_URL_HOME = f"{endpoint}/"
        hf_constants.HUGGINGFACE_CO_URL_TEMPLATE = (
            f"{endpoint}/{{repo_id}}/resolve/{{revision}}/{{filename}}"
        )
    except Exception as exc:
        logger.debug(f"Could not update imported Hugging Face constants: {exc}")


def _load_sentence_transformer(model: str, config):
    from sentence_transformers import SentenceTransformer

    try:
        return SentenceTransformer(model, trust_remote_code=True)
    except Exception as first_error:
        for endpoint in _endpoint_fallbacks(config):
            logger.warning(
                f"Failed to load Hugging Face model from default endpoint; "
                f"retrying with HF_ENDPOINT={endpoint}: {first_error}"
            )
            _apply_hf_endpoint(endpoint)
            try:
                return SentenceTransformer(model, trust_remote_code=True)
            except Exception as retry_error:
                logger.warning(
                    f"Failed to load Hugging Face model with HF_ENDPOINT={endpoint}: "
                    f"{retry_error}"
                )
        raise first_error


@register_reranker("local")
class LocalReranker(BaseReranker):
    def get_similarity_score(self, s1: list[str], s2: list[str]) -> np.ndarray:
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

        encoder = _load_sentence_transformer(self.config.reranker.local.model, self.config)
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
