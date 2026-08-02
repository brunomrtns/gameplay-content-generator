"""Knowledge routes — channel knowledge management endpoints.

Endpoints:
  Channel Profile:
    GET    /api/channel/profile           — get or auto-create user's channel profile
    PUT    /api/channel/profile           — update channel profile

  Knowledge Documents:
    POST   /api/knowledge/upload          — upload a knowledge document (PDF/TXT/MD/DOCX)
    GET    /api/knowledge/documents       — list knowledge documents with status
    POST   /api/knowledge/documents/{id}/process — index document into knowledge base
    DELETE /api/knowledge/documents/{id}  — delete document + its chunks
    GET    /api/knowledge/documents/{id}/status — get indexing status

  Knowledge Query (for debugging/testing retrieval):
    POST   /api/knowledge/query           — retrieve relevant chunks for a query
"""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from gpcg.config import get_settings
from gpcg.domain.models import (
    ChannelProfile,
    Document,
    Job,
    JobPriority,
    JobStage,
    JobStatus,
    JobType,
    User,
    WorkerCapability,
)
from gpcg.infrastructure.auth import get_current_user
from gpcg.infrastructure.database import get_db, session_scope
from gpcg.infrastructure.document_parser import DocumentParseError, detect_type
from gpcg.logging import get_logger

log = get_logger(__name__)
router = APIRouter(tags=["knowledge"])

# Allowed file types for knowledge documents
KNOWLEDGE_FILE_TYPES = {"pdf", "txt", "md", "markdown", "docx", "doc"}
MAX_KNOWLEDGE_FILE_SIZE = 50 * 1024 * 1024  # 50 MB


# ── Channel Profile ───────────────────────────────────────────────────────────


@router.get("/channel/profile")
def get_channel_profile(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get the user's channel profile, auto-creating if it doesn't exist."""
    profile = db.query(ChannelProfile).filter(
        ChannelProfile.user_id == user.id
    ).first()

    if not profile:
        profile = ChannelProfile(user_id=user.id)
        with session_scope() as session:
            session.add(profile)
            session.flush()
            profile_id = profile.id
            # Re-fetch in the request session
        profile = db.query(ChannelProfile).filter(
            ChannelProfile.id == profile_id
        ).first()

    return {
        "id": profile.id,
        "channel_description": profile.channel_description,
        "niche": profile.niche,
        "target_audience": profile.target_audience,
        "tone_of_voice": profile.tone_of_voice,
        "narrative_style": profile.narrative_style,
        "content_goals": profile.content_goals,
        "special_rules": profile.special_rules,
        "updated_at": profile.updated_at.isoformat() if profile.updated_at else None,
    }


@router.put("/channel/profile")
def update_channel_profile(
    data: dict,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Update the user's channel profile.

    All fields are optional — only provided fields are updated.
    """
    profile = db.query(ChannelProfile).filter(
        ChannelProfile.user_id == user.id
    ).first()

    if not profile:
        profile = ChannelProfile(user_id=user.id)
        with session_scope() as session:
            session.add(profile)
            session.flush()
            profile_id = profile.id
        profile = db.query(ChannelProfile).filter(
            ChannelProfile.id == profile_id
        ).first()

    allowed_fields = [
        "channel_description", "niche", "target_audience",
        "tone_of_voice", "narrative_style", "content_goals", "special_rules",
    ]

    with session_scope() as session:
        p = session.query(ChannelProfile).filter(
            ChannelProfile.id == profile.id
        ).first()
        for field in allowed_fields:
            if field in data:
                setattr(p, field, data[field])
        session.flush()

    return {"ok": True, "id": profile.id}


# ── Knowledge Documents ───────────────────────────────────────────────────────


@router.post("/knowledge/upload")
def upload_knowledge_document(
    file: UploadFile = File(...),
    game_id: int | None = Form(None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Upload a knowledge document (PDF, TXT, MD, DOCX) for the channel.

    The document is stored and then indexed into the knowledge base
    (chunked + embedded) so the AI can retrieve relevant context during
    video generation.

    game_id: optional. If provided, the document is game-specific knowledge
    (only retrieved when generating content for that game). If NULL, it's
    general channel knowledge (always retrieved regardless of game).
    """
    # Validate file type
    try:
        file_type = detect_type(file.filename or "unknown")
    except DocumentParseError:
        raise HTTPException(400, f"Tipo de arquivo não suportado. Use: {', '.join(KNOWLEDGE_FILE_TYPES)}")

    if file_type not in KNOWLEDGE_FILE_TYPES:
        raise HTTPException(400, f"Tipo de arquivo não suportado. Use: {', '.join(KNOWLEDGE_FILE_TYPES)}")

    # Read and validate size
    content = file.file.read()
    if len(content) > MAX_KNOWLEDGE_FILE_SIZE:
        raise HTTPException(400, f"Arquivo muito grande. Máximo: {MAX_KNOWLEDGE_FILE_SIZE // (1024*1024)}MB")

    if not content:
        raise HTTPException(400, "Arquivo vazio")

    # Compute file hash for download verification
    file_hash = hashlib.sha256(content).hexdigest()

    # Validate game_id if provided (must belong to the user or be a known game)
    resolved_game_id = None
    if game_id is not None:
        from gpcg.domain.models import Game
        game = db.query(Game).filter(Game.id == game_id).first()
        if not game:
            raise HTTPException(400, f"Jogo com id {game_id} não encontrado")
        resolved_game_id = game.id

    # Store file
    settings = get_settings()
    upload_dir = settings.data_dir / "knowledge" / f"user_{user.id}"
    upload_dir.mkdir(parents=True, exist_ok=True)

    filename = file.filename or "document.txt"
    safe_name = f"{filename}"
    # Avoid overwriting — append counter if exists
    file_path = upload_dir / safe_name
    counter = 1
    while file_path.exists():
        stem = Path(filename).stem
        suffix = Path(filename).suffix
        file_path = upload_dir / f"{stem}_{counter}{suffix}"
        counter += 1

    file_path.write_bytes(content)

    # Create Document record + generate upload token for worker download
    upload_token = uuid.uuid4().hex
    with session_scope() as session:
        doc = Document(
            user_id=user.id,
            game_id=resolved_game_id,
            filename=file_path.name,
            file_path=str(file_path),
            file_type=file_type,
            file_size=len(content),
            file_hash=file_hash,
            upload_token=upload_token,
            text_extracted=False,
            facts_extracted=False,
            knowledge_status="pending",
        )
        session.add(doc)
        session.flush()
        doc_id = doc.id

        # Create a knowledge_index job for the worker to process
        job = Job(
            job_uuid=str(uuid.uuid4()),
            type=JobType.knowledge_index.value,
            status=JobStatus.queued.value,
            stage=JobStage.download.value,
            user_id=user.id,
            game_id=resolved_game_id,
            priority=JobPriority.normal.value,
            required_capabilities=[WorkerCapability.knowledge_index.value],
            artifacts={
                "document_id": doc_id,
                "filename": file_path.name,
            },
        )
        session.add(job)

    log.info(f"Document {doc_id} uploaded ({len(content)} bytes), job queued for worker indexing")

    return {
        "id": doc_id,
        "filename": file_path.name,
        "file_type": file_type,
        "file_size": len(content),
        "game_id": resolved_game_id,
        "knowledge_status": "pending",
        "chunk_count": 0,
        "error": None,
        "message": "Documento enviado. A indexação será processada pelo worker.",
    }


@router.get("/knowledge/documents")
def list_knowledge_documents(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List all knowledge documents for the current user with their indexing status.

    Includes both general channel knowledge (game_id=NULL) and game-specific
    knowledge documents. The game_name is included for game-specific docs.
    """
    from gpcg.domain.models import Game
    docs = db.query(Document).filter(
        Document.user_id == user.id,
    ).order_by(Document.created_at.desc()).all()

    # Build game_id → game_name map
    game_ids = {d.game_id for d in docs if d.game_id is not None}
    games = {}
    if game_ids:
        for g in db.query(Game).filter(Game.id.in_(game_ids)).all():
            games[g.id] = g.canonical_name

    return [
        {
            "id": d.id,
            "filename": d.filename,
            "file_type": d.file_type,
            "file_size": d.file_size,
            "game_id": d.game_id,
            "game_name": games.get(d.game_id) if d.game_id else None,
            "knowledge_status": d.knowledge_status,
            "chunk_count": d.chunk_count,
            "facts_extracted": d.facts_extracted,
            "created_at": d.created_at.isoformat() if d.created_at else None,
        }
        for d in docs
    ]


@router.post("/knowledge/documents/{doc_id}/process")
def process_knowledge_document(
    doc_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Re-index a knowledge document by queuing a new indexing job for the worker.

    Useful if the embedding model changed or if the initial indexing failed.
    The actual indexing (parsing, OCR, chunking, embeddings) runs on the
    worker (Compute Plane) where Ollama is available.
    """
    doc = db.query(Document).filter(
        Document.id == doc_id,
        Document.user_id == user.id,
    ).first()

    if not doc:
        raise HTTPException(404, "Documento não encontrado")

    # Reset status and generate new upload token
    doc.knowledge_status = "pending"
    doc.upload_token = uuid.uuid4().hex

    # Queue a new indexing job
    job = Job(
        job_uuid=str(uuid.uuid4()),
        type=JobType.knowledge_index.value,
        status=JobStatus.queued.value,
        stage=JobStage.download.value,
        user_id=user.id,
        game_id=doc.game_id,
        priority=JobPriority.normal.value,
        required_capabilities=[WorkerCapability.knowledge_index.value],
        artifacts={
            "document_id": doc.id,
            "filename": doc.filename,
            "reindex": True,
        },
    )
    db.add(job)
    db.commit()

    return {
        "id": doc_id,
        "knowledge_status": "pending",
        "chunk_count": 0,
        "error": None,
        "message": "Reindexação enfileirada para o worker processar.",
    }


@router.delete("/knowledge/documents/{doc_id}")
def delete_knowledge_document(
    doc_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Delete a knowledge document and all its chunks."""
    from gpcg.domain.models import KnowledgeChunk

    doc = db.query(Document).filter(
        Document.id == doc_id,
        Document.user_id == user.id,
    ).first()

    if not doc:
        raise HTTPException(404, "Documento não encontrado")

    # Delete chunks
    with session_scope() as session:
        session.query(KnowledgeChunk).filter(
            KnowledgeChunk.document_id == doc_id
        ).delete()
        session.query(Document).filter(Document.id == doc_id).delete()
        session.flush()

    # Delete file from disk
    try:
        Path(doc.file_path).unlink(missing_ok=True)
    except Exception as e:
        log.warning(f"Failed to delete file {doc.file_path}: {e}")

    return {"ok": True}


@router.get("/knowledge/documents/{doc_id}/status")
def knowledge_document_status(
    doc_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get the indexing status of a knowledge document."""
    doc = db.query(Document).filter(
        Document.id == doc_id,
        Document.user_id == user.id,
    ).first()

    if not doc:
        raise HTTPException(404, "Documento não encontrado")

    return {
        "id": doc.id,
        "knowledge_status": doc.knowledge_status,
        "chunk_count": doc.chunk_count,
    }


# ── Knowledge Query (debugging/testing) ───────────────────────────────────────


@router.post("/knowledge/query")
def query_knowledge(
    data: dict,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Retrieve relevant knowledge chunks for a query.

    This is primarily for debugging/testing the retrieval. During actual
    video generation, the pipeline calls retrieve_knowledge() directly.
    """
    query = data.get("query", "")
    if not query:
        raise HTTPException(400, "query is required")

    top_k = data.get("top_k", 5)
    game_id = data.get("game_id")  # Optional — for game-specific retrieval testing

    from gpcg.application.knowledge_service import retrieve_knowledge, build_knowledge_context
    chunks = retrieve_knowledge(db, user.id, query, game_id=game_id, top_k=top_k)
    context = build_knowledge_context(chunks)

    return {
        "query": query,
        "chunks": [
            {
                "chunk_id": c.chunk_id,
                "content": c.content[:500] + "..." if len(c.content) > 500 else c.content,
                "section": c.section,
                "score": c.score,
                "document_id": c.document_id,
            }
            for c in chunks
        ],
        "context": context,
        "chunk_count": len(chunks),
    }
