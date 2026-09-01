"""Similarity backends for `implied_by` detection.

Two implementations behind one interface. Static embeddings (model2vec, about
30MB and no torch) when available; a lexical scorer otherwise. The lexical path
is not a stub -- it is the one that runs on a machine with no model downloaded,
so it gets the same tests.
"""

from __future__ import annotations

import logging
import re
from typing import Protocol

from rapidfuzz import fuzz

log = logging.getLogger(__name__)

_WORD_RE = re.compile(r"[a-z0-9+#.]+")
_GENERIC = frozenset(
    "and or the a an of in on for with to from using used build built".split()
)


class SimilarityIndex(Protocol):
    name: str

    def similarity(self, term: str, text: str) -> float: ...


class LexicalIndex:
    """Token overlap plus fuzzy ratio, with a hard gate on content words.

    The gate matters. Pure fuzzy ratio thinks "Kubernetes" and "Kubeflow" are
    close, which would let a missing skill be reported as implied -- the exact
    failure the no-fabrication rule exists to prevent. Requiring a real content
    word in common makes a false 'implied' much harder.
    """

    name = "lexical"

    @staticmethod
    def _tokens(text: str) -> set[str]:
        return {t for t in _WORD_RE.findall(text.lower()) if t not in _GENERIC}

    def similarity(self, term: str, text: str) -> float:
        term_tokens = self._tokens(term)
        text_tokens = self._tokens(text)
        if not term_tokens or not text_tokens:
            return 0.0

        shared = term_tokens & text_tokens
        if not shared:
            # No content word in common. Allow only a near-identical fuzzy
            # match, which catches spelling variants without inventing links.
            ratio = fuzz.token_set_ratio(term.lower(), text.lower()) / 100.0
            return ratio if ratio >= 0.95 else 0.0

        coverage = len(shared) / len(term_tokens)
        ratio = fuzz.partial_token_set_ratio(term.lower(), text.lower()) / 100.0
        return 0.7 * coverage + 0.3 * ratio


class StaticEmbeddingIndex:
    """model2vec static vectors. No torch, ~30MB, CPU-instant."""

    name = "static-embeddings"

    def __init__(self, model) -> None:
        self._model = model
        self._cache: dict[str, object] = {}
        self._lexical = LexicalIndex()

    def _vector(self, text: str):
        if text not in self._cache:
            self._cache[text] = self._model.encode([text])[0]
        return self._cache[text]

    def similarity(self, term: str, text: str) -> float:
        import numpy as np

        a, b = self._vector(term), self._vector(text)
        denominator = float(np.linalg.norm(a) * np.linalg.norm(b))
        cosine = float(np.dot(a, b) / denominator) if denominator else 0.0

        # Blended with lexical so a high cosine between two unrelated technical
        # terms cannot on its own claim experience the resume does not show.
        return max(0.0, 0.7 * cosine + 0.3 * self._lexical.similarity(term, text))


def build_index() -> SimilarityIndex:
    """Static embeddings if configured and loadable, otherwise lexical."""
    from ..config import get_settings

    settings = get_settings()
    if not settings.embeddings_enabled:
        return LexicalIndex()

    try:
        from model2vec import StaticModel

        model = StaticModel.from_pretrained(settings.embeddings_model)
    except Exception as exc:  # noqa: BLE001 -- any failure means lexical
        log.warning(
            "static embeddings unavailable (%s); using lexical matching. "
            "Terms only covered semantically will read as missing.",
            exc,
        )
        return LexicalIndex()

    return StaticEmbeddingIndex(model)
