"""Knowledge service — RAG pipeline for channel knowledge.

Transforms uploaded documents (PDF, TXT, MD, DOCX) into a queryable
knowledge base that the AI uses during video generation.

Pipeline:
  1. Parse document → text (reuse existing document_parser)
  2. Chunk text (structure-aware: heading-based + sliding window)
  3. Embed each chunk via Ollama's embedding API (with heading path prefix)
  4. Store chunks with embeddings in KnowledgeChunk table
  5. At generation time: retrieve relevant chunks by hybrid search + MMR

Architecture inspired by Avesia's GraphRAG but simplified for GPCG:
- No graph/community detection (channel knowledge doesn't need it)
- No spaCy NER (Ollama embeddings are sufficient)
- No Celery/Redis (synchronous processing is fine for document uploads)
- SQLite with JSON-stored embeddings (no external vector DB)
- In-memory cosine similarity (fast for hundreds of chunks per channel)

Patterns reused from Avesia:
- Structure-aware chunking with heading path breadcrumbs (chunker.py)
- Heading path prepended to content before embedding (embedder.py)
- Hybrid search (vector + keyword) with Reciprocal Rank Fusion (graph-retriever.ts)
- MMR diversification to reduce redundancy (graph-retriever.ts)
- Context builder with relevance scores and token budget (context-builder.ts)
- _clean_section_name to strip page numbers and figure refs (chunker.py)
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Optional

import httpx
from sqlalchemy import or_
from sqlalchemy.orm import Session

from gpcg.config import get_settings
from gpcg.domain.models import Document, KnowledgeChunk
from gpcg.infrastructure.database import session_scope
from gpcg.infrastructure.document_parser import DocumentParseError, parse_document
from gpcg.infrastructure.llm import LLMError
from gpcg.logging import get_logger

log = get_logger(__name__)

# ── Chunking config (aligned with Avesia's chunker.py) ────────────────────────
# Token estimation: ~4 chars per token for Portuguese
CHARS_PER_TOKEN = 4
TARGET_TOKENS = 400   # ~1600 chars per chunk
OVERLAP_TOKENS = 50   # ~200 chars overlap
MAX_TOKENS = 800      # ~3200 chars max per chunk
MIN_TOKENS = 40       # ~160 chars — skip tiny chunks

# Embedding model — Ollama's nomic-embed-text is lightweight and fast.
DEFAULT_EMBEDDING_MODEL = "nomic-embed-text"


# ── Chunking (adapted from Avesia's chunker.py) ───────────────────────────────

# Heading detection heuristics (from Avesia)
MARKDOWN_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*$")
HEURISTIC_HEADING = re.compile(r"^[A-ZÀ-Ý0-9][A-Za-zÀ-Ý0-9 \-:&/()]{2,80}$")

# Patterns to strip from section names
TRAILING_PAGE_NUM = re.compile(r"\s+\d{1,4}$")
FIGURE_REF = re.compile(r"^(figura|fig\.?|tabela|tab\.?|quadro|imagem|gráfico)\s*\d", re.IGNORECASE)
CAPITULO_REF = re.compile(r"^cap[ií]tulo\s+\d", re.IGNORECASE)


@dataclass
class TextChunk:
    content: str
    index: int
    token_count: int
    section: Optional[str] = None
    heading_path: list[str] = field(default_factory=list)
    char_start: int = 0
    char_end: int = 0
    embedding: list[float] = field(default_factory=list)


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // CHARS_PER_TOKEN)


def _clean_section_name(name: str) -> str:
    """Clean a section/heading name (from Avesia's chunker.py).

    - Strips trailing page numbers
    - Filters out figure/table references
    - Filters out bare chapter references
    """
    name = name.strip()
    if FIGURE_REF.match(name):
        return ""
    if CAPITULO_REF.match(name) and len(name.split()) <= 3:
        return ""
    name = TRAILING_PAGE_NUM.sub("", name).strip()
    if len(name) < 3:
        return ""
    return name


def chunk_text(text: str) -> list[TextChunk]:
    """Structure-aware chunking: split at heading boundaries, then sliding window.

    Adapted from Avesia's chunker.py:
    1. Split into sections at heading boundaries (Markdown + heuristic)
    2. For each section, if it fits in one chunk, keep it whole
    3. If too large, use sliding window at sentence boundaries with overlap
    """
    if not text.strip():
        return []

    sections = _split_into_sections(text)
    chunks: list[TextChunk] = []
    chunk_index = 0

    for section in sections:
        body = section["body"]
        if not body.strip():
            continue

        section_tokens = _estimate_tokens(body)

        if section_tokens <= MAX_TOKENS:
            # Section fits in one chunk
            if section_tokens >= MIN_TOKENS or len(chunks) == 0:
                chunks.append(_make_chunk(body, section, chunk_index, char_offset=0))
                chunk_index += 1
        else:
            # Sliding window
            sub_chunks = _sliding_window(
                body, section, chunk_index,
                TARGET_TOKENS, OVERLAP_TOKENS, MIN_TOKENS,
            )
            chunks.extend(sub_chunks)
            chunk_index = len(chunks)

    return chunks


def _split_into_sections(text: str) -> list[dict]:
    """Split text into sections based on heading detection (from Avesia)."""
    sections: list[dict] = []
    current_heading_path: list[str] = []
    current_section: Optional[str] = None
    current_body: list[str] = []

    for line in text.split("\n"):
        md_match = MARKDOWN_HEADING.match(line)
        heuristic_match = HEURISTIC_HEADING.match(line)

        is_heading = False
        heading_level = 0
        heading_text = ""

        if md_match:
            is_heading = True
            heading_level = len(md_match.group(1))
            heading_text = md_match.group(2).strip()
        elif heuristic_match and len(line) < 80 and not line.endswith("."):
            is_heading = True
            heading_level = 2
            heading_text = line.strip()

        if is_heading:
            if current_body:
                sections.append({
                    "heading_path": list(current_heading_path),
                    "section": current_section,
                    "body": "\n".join(current_body),
                })

            heading_text = _clean_section_name(heading_text)
            if not heading_text:
                current_body = []
                continue

            # Update heading path (Avesia's approach: pop to level, then append)
            while len(current_heading_path) >= heading_level:
                current_heading_path.pop()
            current_heading_path.append(heading_text)
            current_section = heading_text
            current_body = []
        else:
            current_body.append(line)

    if current_body:
        sections.append({
            "heading_path": list(current_heading_path),
            "section": current_section,
            "body": "\n".join(current_body),
        })

    return sections


def _make_chunk(body: str, section: dict, index: int, *, char_offset: int) -> TextChunk:
    return TextChunk(
        content=body.strip(),
        index=index,
        token_count=_estimate_tokens(body),
        section=section.get("section"),
        heading_path=section.get("heading_path", []),
        char_start=char_offset,
        char_end=char_offset + len(body),
    )


def _sliding_window(
    body: str,
    section: dict,
    start_index: int,
    target_tokens: int,
    overlap_tokens: int,
    min_tokens: int,
) -> list[TextChunk]:
    """Split a large section into overlapping chunks at sentence boundaries (Avesia)."""
    target_chars = target_tokens * CHARS_PER_TOKEN
    overlap_chars = overlap_tokens * CHARS_PER_TOKEN

    sentences = re.split(r"(?<=[.!?])\s+", body)
    chunks: list[TextChunk] = []
    current: list[str] = []
    current_len = 0
    chunk_idx = start_index
    char_offset = 0

    for sentence in sentences:
        s_len = len(sentence)
        if current_len + s_len > target_chars and current:
            text = " ".join(current)
            chunks.append(_make_chunk(text, section, chunk_idx, char_offset=char_offset))
            char_offset += len(text) + 1
            chunk_idx += 1

            # Keep overlap: last few sentences
            overlap: list[str] = []
            overlap_len = 0
            for s in reversed(current):
                if overlap_len + len(s) > overlap_chars:
                    break
                overlap.insert(0, s)
                overlap_len += len(s)
            current = overlap
            current_len = overlap_len

        current.append(sentence)
        current_len += s_len

    if current and _estimate_tokens(" ".join(current)) >= min_tokens:
        text = " ".join(current)
        chunks.append(_make_chunk(text, section, chunk_idx, char_offset=char_offset))

    return chunks


# ── Embeddings (adapted from Avesia's embedder.py) ────────────────────────────


def _get_embedding_model() -> str:
    settings = get_settings()
    model = getattr(settings, "gpcg_embedding_model", None)
    return model or DEFAULT_EMBEDDING_MODEL


def _embed_with_heading_prefix(content: str, heading_path: list[str], model: str) -> list[float]:
    """Embed a chunk, prepending heading path for better retrieval context.

    This is Avesia's pattern: the embedding includes the section breadcrumb
    so that queries about a specific topic match chunks in the right section.
    """
    prefix = ""
    if heading_path:
        prefix = " > ".join(heading_path) + "\n"
    return _embed_text(prefix + content, model=model)


def _embed_text(text: str, model: Optional[str] = None) -> list[float]:
    """Embed text via Ollama's /api/embeddings endpoint."""
    settings = get_settings()
    host = settings.ollama_host.rstrip("/")
    embed_model = model or _get_embedding_model()

    url = f"{host}/api/embeddings"
    payload = {"model": embed_model, "prompt": text}

    try:
        resp = httpx.post(url, json=payload, timeout=60.0)
        resp.raise_for_status()
        data = resp.json()
        embedding = data.get("embedding", [])
        if not embedding:
            raise LLMError(f"empty embedding from Ollama: {data}")
        return embedding
    except httpx.HTTPError as e:
        # Fallback to default LLM model if embedding model unavailable
        if embed_model != settings.gpcg_llm_model:
            log.warning(f"Embedding model {embed_model} not available, falling back to {settings.gpcg_llm_model}")
            return _embed_text(text, model=settings.gpcg_llm_model)
        raise LLMError(f"Ollama embedding request failed: {e}") from e


def embed_query(text: str) -> list[float]:
    """Embed a search query."""
    return _embed_text(text)


def generate_embedding(content: str, heading_path: list[str] | None = None) -> list[float]:
    """Generate an embedding for a chunk, with optional heading path prefix.

    Public API for the worker to use when indexing documents locally.
    Returns an empty list if embedding fails (keyword-only searchable).
    """
    model = _get_embedding_model()
    try:
        return _embed_with_heading_prefix(content, heading_path or [], model)
    except LLMError as e:
        log.warning(f"Failed to embed chunk: {e}")
        return []


# ── Cosine similarity ─────────────────────────────────────────────────────────


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


# ── Reciprocal Rank Fusion (from Avesia's retriever.ts) ───────────────────────


def _reciprocal_rank_fusion(
    vector_results: list[tuple[int, float]],
    keyword_results: list[tuple[int, float]],
    vector_weight: float = 0.65,
    k: int = 60,
) -> list[tuple[int, float]]:
    """Fuse vector and keyword search results via RRF.

    score = vector_weight * (1 / (k + rank_vec)) + (1 - vector_weight) * (1 / (k + rank_kw))
    """
    vector_ranked = sorted(vector_results, key=lambda x: x[1], reverse=True)
    keyword_ranked = sorted(keyword_results, key=lambda x: x[1], reverse=True)

    fused: dict[int, float] = {}
    for rank, (chunk_id, _) in enumerate(vector_ranked):
        fused[chunk_id] = fused.get(chunk_id, 0) + vector_weight * (1.0 / (k + rank + 1))
    for rank, (chunk_id, _) in enumerate(keyword_ranked):
        fused[chunk_id] = fused.get(chunk_id, 0) + (1 - vector_weight) * (1.0 / (k + rank + 1))

    return sorted(fused.items(), key=lambda x: x[1], reverse=True)


# ── MMR diversification (from Avesia's graph-retriever.ts) ────────────────────


def _content_similarity(a: str, b: str) -> float:
    """Jaccard word overlap — cheaper than embedding-based similarity (Avesia)."""
    set_a = set(w for w in a.lower().split() if len(w) > 3)
    set_b = set(w for w in b.lower().split() if len(w) > 3)
    if not set_a or not set_b:
        return 0.0
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    return intersection / union


def _mmr_diversify(
    candidates: list[tuple[int, str, float]],  # (chunk_id, content, score)
    lambda_: float,
    limit: int,
) -> list[tuple[int, str, float]]:
    """Maximal Marginal Relevance: balance relevance vs. redundancy (Avesia).

    Score = λ * relevance(query) - (1-λ) * max_similarity(selected)
    """
    if len(candidates) <= limit:
        return candidates

    selected: list[tuple[int, str, float]] = [candidates[0]]
    remaining = list(candidates[1:])

    while len(selected) < limit and remaining:
        best_idx = 0
        best_score = -math.inf

        for i, (chunk_id, content, relevance) in enumerate(remaining):
            max_sim = max(_content_similarity(content, sel[1]) for sel in selected)
            mmr_score = lambda_ * relevance - (1 - lambda_) * max_sim
            if mmr_score > best_score:
                best_score = mmr_score
                best_idx = i

        selected.append(remaining[best_idx])
        remaining.pop(best_idx)

    return selected


# ── Document indexing ─────────────────────────────────────────────────────────


@dataclass
class IndexingResult:
    document_id: int
    chunk_count: int
    status: str  # indexed | error
    error: Optional[str] = None


def index_document(document: Document, user_id: int) -> IndexingResult:
    """Full RAG indexing pipeline for a single document.

    1. Parse → text
    2. Chunk (structure-aware)
    3. Embed each chunk (with heading path prefix — Avesia pattern)
    4. Store in KnowledgeChunk table (propagating game_id from the document)
    5. Update Document.knowledge_status

    The document's game_id is propagated to every chunk so that retrieval
    can filter by game: game-specific chunks are only retrieved when
    generating content for that game; general chunks (game_id=NULL) are
    always retrieved.
    """
    log.info(f"Indexing document {document.id} ({document.filename}) for user {user_id}")

    try:
        # Mark as processing
        with session_scope() as session:
            doc = session.query(Document).filter(Document.id == document.id).first()
            if doc:
                doc.knowledge_status = "processing"
                session.flush()

        # 1. Parse
        text = parse_document(document.file_path, document.file_type)
        if not text.strip():
            raise DocumentParseError("document is empty or could not be parsed")

        # 2. Chunk
        chunks = chunk_text(text)
        if not chunks:
            raise DocumentParseError("no chunks generated from document")

        log.info(f"Document {document.id}: {len(chunks)} chunks generated")

        # 3. Embed + 4. Store
        embed_model = _get_embedding_model()

        # Delete existing chunks (re-indexing case)
        with session_scope() as session:
            session.query(KnowledgeChunk).filter(
                KnowledgeChunk.document_id == document.id
            ).delete()
            session.flush()

        # Determine game_id from the document (propagate to all chunks)
        game_id = None
        with session_scope() as session:
            doc = session.query(Document).filter(Document.id == document.id).first()
            if doc:
                game_id = doc.game_id

        with session_scope() as session:
            for chunk in chunks:
                try:
                    embedding = _embed_with_heading_prefix(
                        chunk.content, chunk.heading_path, embed_model
                    )
                except LLMError as e:
                    log.warning(f"Failed to embed chunk {chunk.index} of doc {document.id}: {e}")
                    embedding = []  # Store without embedding — keyword-only searchable

                kc = KnowledgeChunk(
                    user_id=user_id,
                    document_id=document.id,
                    game_id=game_id,
                    content=chunk.content,
                    embedding=embedding,
                    chunk_index=chunk.index,
                    section=chunk.section,
                    char_start=chunk.char_start,
                    char_end=chunk.char_end,
                    embedding_model=embed_model if embedding else None,
                )
                session.add(kc)
            session.flush()

        # 5. Update document status
        with session_scope() as session:
            doc = session.query(Document).filter(Document.id == document.id).first()
            if doc:
                doc.knowledge_status = "indexed"
                doc.chunk_count = len(chunks)
                doc.text_extracted = True
                session.flush()

        log.info(f"Document {document.id} indexed: {len(chunks)} chunks")
        return IndexingResult(document_id=document.id, chunk_count=len(chunks), status="indexed")

    except Exception as e:
        log.error(f"Failed to index document {document.id}: {e}")
        with session_scope() as session:
            doc = session.query(Document).filter(Document.id == document.id).first()
            if doc:
                doc.knowledge_status = "error"
                session.flush()
        return IndexingResult(document_id=document.id, chunk_count=0, status="error", error=str(e))


# ── Retrieval (adapted from Avesia's graph-retriever.ts) ──────────────────────


@dataclass
class RetrievedChunk:
    chunk_id: int
    content: str
    section: str
    heading_path: list[str]
    score: float
    document_id: Optional[int] = None


def retrieve_knowledge(
    session: Session,
    user_id: int,
    query: str,
    game_id: Optional[int] = None,
    top_k: int = 5,
    max_tokens: int = 1500,
    vector_weight: float = 0.65,
    mmr_lambda: float = 0.6,
) -> list[RetrievedChunk]:
    """Retrieve the most relevant knowledge chunks for a given query.

    Uses Avesia's hybrid retrieval pattern:
    1. Vector similarity (cosine of query embedding vs chunk embeddings)
    2. Keyword matching (substring search for exact terminology)
    3. Reciprocal Rank Fusion (RRF) to merge both signals
    4. MMR diversification to reduce redundancy

    Game isolation: when game_id is provided, only retrieves chunks that are
    either game-specific (game_id == game_id) or general channel knowledge
    (game_id IS NULL). Chunks from OTHER games are NEVER retrieved — this
    prevents cross-game knowledge leakage in the RAG pipeline.

    No graph expansion (unlike Avesia) — GPCG's channel knowledge is simpler
    and doesn't need community detection. Pure hybrid search + MMR is enough.
    """
    # Game-isolated retrieval: game-specific + general, never other games
    if game_id is not None:
        chunks = session.query(KnowledgeChunk).filter(
            KnowledgeChunk.user_id == user_id,
            or_(
                KnowledgeChunk.game_id == game_id,
                KnowledgeChunk.game_id.is_(None),
            ),
        ).all()
    else:
        # No game context — retrieve only general channel knowledge
        chunks = session.query(KnowledgeChunk).filter(
            KnowledgeChunk.user_id == user_id,
            KnowledgeChunk.game_id.is_(None),
        ).all()

    if not chunks:
        return []

    # 1. Vector similarity
    query_embedding: list[float] = []
    try:
        query_embedding = embed_query(query)
    except LLMError as e:
        log.warning(f"Failed to embed query, falling back to keyword-only: {e}")

    vector_scores: list[tuple[int, float]] = []
    if query_embedding:
        for chunk in chunks:
            if chunk.embedding:
                score = _cosine_similarity(query_embedding, chunk.embedding)
                vector_scores.append((chunk.id, score))

    # 2. Keyword matching (ILIKE-style substring search)
    query_lower = query.lower()
    query_terms = [t for t in query_lower.split() if len(t) > 2]
    keyword_scores: list[tuple[int, float]] = []
    for chunk in chunks:
        content_lower = chunk.content.lower()
        matches = sum(1 for term in query_terms if term in content_lower)
        if matches > 0:
            keyword_scores.append((chunk.id, matches / max(len(query_terms), 1)))

    # 3. Reciprocal Rank Fusion
    fused = _reciprocal_rank_fusion(vector_scores, keyword_scores, vector_weight)

    # Take top_k * 3 candidates for MMR (need more than final limit for diversification)
    candidates_raw = fused[:top_k * 3]
    chunk_map = {c.id: c for c in chunks}

    # Build candidates for MMR: (chunk_id, content, score)
    candidates: list[tuple[int, str, float]] = []
    for chunk_id, score in candidates_raw:
        chunk = chunk_map.get(chunk_id)
        if chunk:
            candidates.append((chunk_id, chunk.content, score))

    # 4. MMR diversification
    diversified = _mmr_diversify(candidates, mmr_lambda, top_k)

    # Build results respecting token budget
    max_chars = max_tokens * CHARS_PER_TOKEN
    results: list[RetrievedChunk] = []
    total_chars = 0
    for chunk_id, content, score in diversified:
        chunk = chunk_map.get(chunk_id)
        if not chunk:
            continue
        if total_chars + len(content) > max_chars:
            break
        results.append(RetrievedChunk(
            chunk_id=chunk.id,
            content=chunk.content,
            section=chunk.section or "",
            heading_path=[],  # Not stored in DB — would need a column for this
            score=score,
            document_id=chunk.document_id,
        ))
        total_chars += len(content)

    return results


def build_knowledge_context(chunks: list[RetrievedChunk]) -> str:
    """Build a natural-language context block from retrieved chunks.

    Adapted from Avesia's context-builder.ts:
    - Prepends heading breadcrumb: [Section > Subsection]
    - Adds relevance score: [Relevância: 85%]
    - Joins with \n\n---\n\n
    - Respects token budget

    Includes a CRITICAL language instruction: the knowledge may be in any
    language (often English — wikis, guides), but the generated script MUST
    be in Brazilian Portuguese. This prevents the LLM from leaking English
    phrasing into the output.
    """
    if not chunks:
        return ""

    header = (
        "=== CONHECIMENTO DO CANAL (FONTE DE REFERÊNCIA) ===\n"
        "Este conteúdo pode estar em diferentes idiomas.\n"
        "Use estas informações apenas para compreender fatos, contexto e ideias.\n\n"
        "REGRA OBRIGATÓRIA:\n"
        "Todo roteiro, narração e texto gerado deve ser escrito exclusivamente\n"
        "em português brasileiro (pt-BR).\n"
        "Nunca responda em inglês.\n"
        "Não copie frases das fontes.\n"
        "Sempre adapte as informações para uma narrativa natural em português brasileiro."
    )
    parts: list[str] = [header]
    for chunk in chunks:
        breadcrumb = f"[{chunk.section}]\n" if chunk.section else ""
        relevance = f"[Relevância: {chunk.score * 100:.0f}%]\n" if chunk.score > 0 else ""
        parts.append(f"{breadcrumb}{relevance}{chunk.content}")

    parts.append("=== FIM DO CONHECIMENTO DO CANAL ===")
    return "\n\n---\n\n".join(parts)
