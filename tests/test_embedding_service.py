"""Tests for V2 Embedding Service — store, retrieve, similarity.

Tests embedding serialization, storage, retrieval, and cosine similarity
for both KnowledgeItem and GameplayEvent embeddings.

Covers ARCHITECTURE_V2.md §7.2 and §8.1.
"""

from unittest.mock import MagicMock, patch

import pytest

from gpcg.application.content_collectors import _compute_hash
from gpcg.application.embedding_service import (
    cosine_similarity,
    deserialize_embedding,
    generate_and_store_knowledge_item_embedding,
    get_knowledge_item_embedding,
    serialize_embedding,
    store_knowledge_item_embedding,
)
from gpcg.domain.models import KnowledgeItem, KnowledgeItemSource, KnowledgeItemStatus, KnowledgeItemType
from gpcg.infrastructure.database import init_db, session_scope


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    """Use a temp DB for each test."""
    db_path = tmp_path / "test_embed.db"
    monkeypatch.setenv("GPCG_DB_PATH", str(db_path))
    monkeypatch.setenv("GPCG_DATA_DIR", str(tmp_path))
    from gpcg.config import get_settings
    get_settings.cache_clear()
    from gpcg.infrastructure import database
    database._engine = None
    database._SessionLocal = None
    init_db()
    yield
    get_settings.cache_clear()
    database._engine = None
    database._SessionLocal = None


class TestSerialization:
    """Tests for embedding serialization/deserialization."""

    def test_serialize_deserialize_roundtrip(self):
        """Serializing and deserializing should preserve the vector."""
        vector = [0.1, 0.2, 0.3, 0.4, 0.5]
        data = serialize_embedding(vector)
        result = deserialize_embedding(data)
        # float32 has some precision loss, use approx
        assert len(result) == len(vector)
        for a, b in zip(result, vector):
            assert abs(a - b) < 1e-5

    def test_serialize_empty_vector(self):
        """Serializing an empty vector should produce empty bytes."""
        data = serialize_embedding([])
        assert data == b""

    def test_deserialize_empty_bytes(self):
        """Deserializing empty bytes should return empty list."""
        result = deserialize_embedding(b"")
        assert result == []

    def test_serialize_produces_compact_bytes(self):
        """Serialized vector should be 4 bytes per float."""
        vector = [1.0, 2.0, 3.0]
        data = serialize_embedding(vector)
        assert len(data) == 12  # 3 floats * 4 bytes


class TestKnowledgeItemEmbeddings:
    """Tests for KnowledgeItem embedding storage."""

    def test_store_and_retrieve(self):
        """Storing and retrieving an embedding should work."""
        with session_scope() as s:
            item = KnowledgeItem(
                title="Test Item",
                content="Test content",
                item_type=KnowledgeItemType.news.value,
                source_type=KnowledgeItemSource.rss.value,
                editorial_score=50.0,
                status=KnowledgeItemStatus.fresh.value,
                content_hash=_compute_hash("Test Item", "Test content"),
            )
            s.add(item)
            s.flush()
            item_id = item.id

            vector = [0.1, 0.2, 0.3, 0.4]
            store_knowledge_item_embedding(s, item_id, vector)

        with session_scope() as s:
            retrieved = get_knowledge_item_embedding(s, item_id)
            assert retrieved is not None
            assert len(retrieved) == 4
            for a, b in zip(retrieved, vector):
                assert abs(a - b) < 1e-5

    def test_update_existing_embedding(self):
        """Updating an existing embedding should overwrite."""
        with session_scope() as s:
            item = KnowledgeItem(
                title="Update Test",
                content="Content",
                item_type=KnowledgeItemType.news.value,
                source_type=KnowledgeItemSource.rss.value,
                editorial_score=50.0,
                status=KnowledgeItemStatus.fresh.value,
                content_hash=_compute_hash("Update Test", "Content"),
            )
            s.add(item)
            s.flush()
            item_id = item.id

            store_knowledge_item_embedding(s, item_id, [0.1, 0.2])
            store_knowledge_item_embedding(s, item_id, [0.3, 0.4, 0.5])

        with session_scope() as s:
            retrieved = get_knowledge_item_embedding(s, item_id)
            assert retrieved is not None
            assert len(retrieved) == 3  # updated to 3 dims

    def test_retrieve_nonexistent_returns_none(self):
        """Retrieving an embedding for a nonexistent item should return None."""
        with session_scope() as s:
            result = get_knowledge_item_embedding(s, 99999)
            assert result is None

    def test_generate_and_store_with_mocked_llm(self):
        """generate_and_store should call LLM embed and store the result."""
        with session_scope() as s:
            item = KnowledgeItem(
                title="Embed Gen Test",
                content="Content for embedding",
                item_type=KnowledgeItemType.news.value,
                source_type=KnowledgeItemSource.rss.value,
                editorial_score=50.0,
                status=KnowledgeItemStatus.fresh.value,
                content_hash=_compute_hash("Embed Gen Test", "Content for embedding"),
            )
            s.add(item)
            s.flush()
            item_id = item.id

        mock_llm = MagicMock()
        mock_llm.embed.return_value = [0.1, 0.2, 0.3, 0.4, 0.5]

        with session_scope() as s:
            result = generate_and_store_knowledge_item_embedding(s, item_id, mock_llm)
            assert result is True

        with session_scope() as s:
            retrieved = get_knowledge_item_embedding(s, item_id)
            assert retrieved is not None
            assert len(retrieved) == 5

    def test_generate_fails_gracefully_on_llm_error(self):
        """generate_and_store should return False on LLM error."""
        from gpcg.infrastructure.llm import LLMError

        with session_scope() as s:
            item = KnowledgeItem(
                title="Error Test",
                content="Content",
                item_type=KnowledgeItemType.news.value,
                source_type=KnowledgeItemSource.rss.value,
                editorial_score=50.0,
                status=KnowledgeItemStatus.fresh.value,
                content_hash=_compute_hash("Error Test", "Content"),
            )
            s.add(item)
            s.flush()
            item_id = item.id

        mock_llm = MagicMock()
        mock_llm.embed.side_effect = LLMError("Ollama unavailable")

        with session_scope() as s:
            result = generate_and_store_knowledge_item_embedding(s, item_id, mock_llm)
            assert result is False


class TestCosineSimilarity:
    """Tests for cosine similarity computation."""

    def test_identical_vectors(self):
        """Identical vectors should have similarity 1.0."""
        v = [1.0, 2.0, 3.0]
        assert abs(cosine_similarity(v, v) - 1.0) < 1e-6

    def test_orthogonal_vectors(self):
        """Orthogonal vectors should have similarity 0.0."""
        a = [1.0, 0.0]
        b = [0.0, 1.0]
        assert abs(cosine_similarity(a, b)) < 1e-6

    def test_opposite_vectors(self):
        """Opposite vectors should have similarity -1.0."""
        a = [1.0, 0.0]
        b = [-1.0, 0.0]
        assert abs(cosine_similarity(a, b) + 1.0) < 1e-6

    def test_empty_vectors(self):
        """Empty vectors should have similarity 0.0."""
        assert cosine_similarity([], []) == 0.0

    def test_different_length_vectors(self):
        """Vectors of different lengths should have similarity 0.0."""
        assert cosine_similarity([1.0, 2.0], [1.0]) == 0.0

    def test_zero_norm_vectors(self):
        """Zero vectors should have similarity 0.0."""
        assert cosine_similarity([0.0, 0.0], [1.0, 2.0]) == 0.0
