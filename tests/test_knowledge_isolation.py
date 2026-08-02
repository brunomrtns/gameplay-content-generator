"""Tests for game-isolated knowledge retrieval.

Validates that the RAG retrieval never leaks knowledge from one game into
content generated for another game. This is the critical correctness test
for the knowledge architecture:

  Case 1: Two game-specific documents → retrieval only returns chunks
          for the correct game + general chunks, never the other game.

  Case 2: General + game-specific → retrieval combines both.

  Case 3: English document → build_knowledge_context includes the pt-BR
          language instruction.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from gpcg.domain.models import (
    Base,
    Document,
    Game,
    KnowledgeChunk,
    User,
)
from gpcg.application.knowledge_service import (
    RetrievedChunk,
    build_knowledge_context,
    retrieve_knowledge,
)


@pytest.fixture
def db_session():
    """In-memory SQLite session for isolated testing."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


@pytest.fixture
def test_setup(db_session):
    """Create a user, two games, and knowledge chunks for each game + general."""
    user = User(email="test@test.com", name="Test", is_active=True)
    db_session.add(user)
    db_session.flush()

    game_bully = Game(canonical_name="Bully", user_id=user.id)
    game_gta = Game(canonical_name="GTA V", user_id=user.id)
    db_session.add_all([game_bully, game_gta])
    db_session.flush()

    # General channel knowledge chunks (game_id=NULL)
    general_chunk = KnowledgeChunk(
        user_id=user.id,
        document_id=None,
        game_id=None,
        content="Dicas gerais para narrar vídeos de games com humor e personalidade.",
        embedding=[0.1, 0.2, 0.3],
        chunk_index=0,
        section="Geral",
    )

    # Bully-specific knowledge chunks
    bully_chunk_1 = KnowledgeChunk(
        user_id=user.id,
        document_id=None,
        game_id=game_bully.id,
        content="Bully é um jogo ambientado em uma escola chamada Bullworth Academy.",
        embedding=[0.5, 0.1, 0.1],
        chunk_index=0,
        section="Bully Wiki",
    )
    bully_chunk_2 = KnowledgeChunk(
        user_id=user.id,
        document_id=None,
        game_id=game_bully.id,
        content="O protagonista Jimmy Hopkins pode andar de skateboard pelo campus.",
        embedding=[0.6, 0.2, 0.1],
        chunk_index=1,
        section="Bully Wiki",
    )

    # GTA V-specific knowledge chunks
    gta_chunk_1 = KnowledgeChunk(
        user_id=user.id,
        document_id=None,
        game_id=game_gta.id,
        content="GTA V se passa na cidade fictícia de Los Santos, baseada em Los Angeles.",
        embedding=[0.1, 0.5, 0.1],
        chunk_index=0,
        section="GTA Wiki",
    )
    gta_chunk_2 = KnowledgeChunk(
        user_id=user.id,
        document_id=None,
        game_id=game_gta.id,
        content="Os três protagonistas de GTA V são Michael, Trevor e Franklin.",
        embedding=[0.1, 0.6, 0.2],
        chunk_index=1,
        section="GTA Wiki",
    )

    db_session.add_all([general_chunk, bully_chunk_1, bully_chunk_2, gta_chunk_1, gta_chunk_2])
    db_session.flush()

    return {
        "user_id": user.id,
        "game_bully_id": game_bully.id,
        "game_gta_id": game_gta.id,
        "general_chunk_id": general_chunk.id,
        "bully_chunk_ids": [bully_chunk_1.id, bully_chunk_2.id],
        "gta_chunk_ids": [gta_chunk_1.id, gta_chunk_2.id],
    }


class TestGameIsolatedRetrieval:
    """Test Case 1: No cross-game knowledge leakage."""

    def test_bully_retrieval_excludes_gta(self, db_session, test_setup):
        """Retrieving knowledge for Bully should NEVER return GTA chunks."""
        chunks = retrieve_knowledge(
            db_session,
            user_id=test_setup["user_id"],
            query="escola skateboard campus",
            game_id=test_setup["game_bully_id"],
            top_k=10,
        )

        chunk_ids = {c.chunk_id for c in chunks}
        gta_chunk_ids = set(test_setup["gta_chunk_ids"])

        # Critical assertion: NO GTA chunks should be in the results
        leaked = chunk_ids & gta_chunk_ids
        assert len(leaked) == 0, f"GTA knowledge leaked into Bully retrieval: {leaked}"

    def test_gta_retrieval_excludes_bully(self, db_session, test_setup):
        """Retrieving knowledge for GTA should NEVER return Bully chunks."""
        chunks = retrieve_knowledge(
            db_session,
            user_id=test_setup["user_id"],
            query="cidade protagonistas Los Santos",
            game_id=test_setup["game_gta_id"],
            top_k=10,
        )

        chunk_ids = {c.chunk_id for c in chunks}
        bully_chunk_ids = set(test_setup["bully_chunk_ids"])

        leaked = chunk_ids & bully_chunk_ids
        assert len(leaked) == 0, f"Bully knowledge leaked into GTA retrieval: {leaked}"

    def test_bully_retrieval_includes_general(self, db_session, test_setup):
        """Retrieving knowledge for Bully should include general channel knowledge."""
        chunks = retrieve_knowledge(
            db_session,
            user_id=test_setup["user_id"],
            query="narrar vídeos humor personalidade",
            game_id=test_setup["game_bully_id"],
            top_k=10,
        )

        chunk_ids = {c.chunk_id for c in chunks}
        assert test_setup["general_chunk_id"] in chunk_ids, \
            "General channel knowledge should be included in game-specific retrieval"


class TestGeneralPlusGameSpecific:
    """Test Case 2: General + game-specific knowledge are combined."""

    def test_combines_general_and_bully(self, db_session, test_setup):
        """Retrieval for Bully can return both general and Bully-specific chunks."""
        chunks = retrieve_knowledge(
            db_session,
            user_id=test_setup["user_id"],
            query="narrar escola skateboard humor",
            game_id=test_setup["game_bully_id"],
            top_k=10,
        )

        chunk_ids = {c.chunk_id for c in chunks}
        # Should include at least one Bully chunk
        bully_included = chunk_ids & set(test_setup["bully_chunk_ids"])
        assert len(bully_included) > 0, "No Bully-specific chunks retrieved"
        # Should include the general chunk
        assert test_setup["general_chunk_id"] in chunk_ids, \
            "General chunk not included"

    def test_no_game_context_returns_only_general(self, db_session, test_setup):
        """When game_id is None, only general knowledge is retrieved."""
        chunks = retrieve_knowledge(
            db_session,
            user_id=test_setup["user_id"],
            query="narrar humor personalidade",
            game_id=None,
            top_k=10,
        )

        chunk_ids = {c.chunk_id for c in chunks}
        # Should NOT include any game-specific chunks
        assert chunk_ids & set(test_setup["bully_chunk_ids"]) == set()
        assert chunk_ids & set(test_setup["gta_chunk_ids"]) == set()
        # Should include the general chunk
        assert test_setup["general_chunk_id"] in chunk_ids


class TestLanguageInstruction:
    """Test Case 3: Knowledge context includes pt-BR language instruction."""

    def test_context_has_pt_br_instruction(self):
        """build_knowledge_context must include the pt-BR language rule."""
        chunks = [
            RetrievedChunk(
                chunk_id=1,
                content="Bully is set in Bullworth Academy, a fictional boarding school.",
                section="Bully Wiki",
                heading_path=[],
                score=0.9,
            ),
        ]

        context = build_knowledge_context(chunks)

        # Must contain the pt-BR language instruction
        assert "português brasileiro" in context.lower() or "pt-br" in context.lower(), \
            "Knowledge context missing pt-BR language instruction"
        assert "Nunca responda em inglês" in context, \
            "Knowledge context missing 'never respond in English' instruction"
        assert "Não copie frases das fontes" in context, \
            "Knowledge context missing anti-copy instruction"

    def test_context_header_present(self):
        """Context should have the CONHECIMENTO header."""
        chunks = [
            RetrievedChunk(
                chunk_id=1,
                content="Some knowledge content.",
                section="Section",
                heading_path=[],
                score=0.8,
            ),
        ]
        context = build_knowledge_context(chunks)
        assert "CONHECIMENTO DO CANAL" in context
        assert "FONTE DE REFERÊNCIA" in context

    def test_empty_chunks_returns_empty(self):
        """No chunks → empty context (no header)."""
        context = build_knowledge_context([])
        assert context == ""
