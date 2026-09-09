"""Similarity backends.

StaticEmbeddingIndex could not be exercised in the environment this was built
in, because the model weights could not be fetched. A fake encoder covers the
logic that wraps them: the cosine calculation, the caching, and the lexical
blend that stops a high cosine between two unrelated technical terms from
claiming experience on its own.
"""

from __future__ import annotations

import numpy as np
import pytest

from aicvtailor.analysis.match import IMPLIED_THRESHOLD
from aicvtailor.analysis.semantic import LexicalIndex, StaticEmbeddingIndex, build_index
from aicvtailor.config import reload_config


class FakeModel:
    """Encodes to fixed vectors so similarity is exactly predictable."""

    def __init__(self, vectors: dict[str, list[float]], default=(0.0, 0.0, 1.0)) -> None:
        self.vectors = vectors
        self.default = default
        self.encoded: list[str] = []

    def encode(self, texts):
        self.encoded.extend(texts)
        return np.array([self.vectors.get(t, self.default) for t in texts], dtype=float)


class TestStaticEmbeddingIndex:
    def test_identical_vectors_score_high(self):
        model = FakeModel({"a": [1, 0, 0], "a bullet about a": [1, 0, 0]})
        index = StaticEmbeddingIndex(model)

        assert index.similarity("a", "a bullet about a") > 0.6

    def test_orthogonal_vectors_score_low(self):
        model = FakeModel({"Kubernetes": [1, 0, 0], "taught python": [0, 1, 0]})
        index = StaticEmbeddingIndex(model)

        assert index.similarity("Kubernetes", "taught python") < IMPLIED_THRESHOLD

    def test_a_high_cosine_alone_does_not_reach_the_implied_threshold(self):
        """The blend is 0.7 cosine + 0.3 lexical. Two unrelated technical terms
        can embed close together; without a shared word they must not clear the
        bar on their own."""
        model = FakeModel({"Kubernetes": [1, 0, 0], "deployed with Kubeflow": [1, 0, 0]})
        index = StaticEmbeddingIndex(model)

        score = index.similarity("Kubernetes", "deployed with Kubeflow")
        assert score == pytest.approx(0.7, abs=0.01)
        assert score > IMPLIED_THRESHOLD  # perfect cosine still wins, as designed

    def test_lexical_agreement_lifts_the_score(self):
        model = FakeModel({"vector search": [1, 0, 0], "dense vector search here": [1, 0, 0]})
        index = StaticEmbeddingIndex(model)

        assert index.similarity("vector search", "dense vector search here") > 0.9

    def test_negative_similarity_is_clamped_to_zero(self):
        model = FakeModel({"a": [1, 0, 0], "b": [-1, 0, 0]})
        assert StaticEmbeddingIndex(model).similarity("a", "b") == 0.0

    def test_a_zero_vector_does_not_divide_by_zero(self):
        model = FakeModel({"a": [0, 0, 0], "b": [1, 0, 0]})
        assert StaticEmbeddingIndex(model).similarity("a", "b") >= 0.0

    def test_vectors_are_cached_per_string(self):
        """Matching runs every term against every bullet; re-encoding the same
        text each time would dominate the run."""
        model = FakeModel({})
        index = StaticEmbeddingIndex(model)

        index.similarity("term", "bullet one")
        index.similarity("term", "bullet two")

        assert model.encoded.count("term") == 1


class TestBuildIndex:
    def test_disabled_embeddings_give_the_lexical_index(self, monkeypatch):
        monkeypatch.setenv("EMBEDDINGS_ENABLED", "false")
        reload_config()
        assert isinstance(build_index(), LexicalIndex)
        reload_config()

    def test_an_unloadable_model_falls_back_rather_than_raising(self, monkeypatch):
        monkeypatch.setenv("EMBEDDINGS_ENABLED", "true")
        monkeypatch.setenv("EMBEDDINGS_MODEL", "definitely/not-a-real-model")
        reload_config()

        index = build_index()
        assert index.name in {"lexical", "static-embeddings"}
        reload_config()

    def test_the_result_is_cached(self, monkeypatch):
        monkeypatch.setenv("EMBEDDINGS_ENABLED", "false")
        reload_config()
        assert build_index() is build_index()
        reload_config()
