"""
Qdrant vector store service.
- Collection: pdf_chunks (size=768, cosine distance)
- Payload fields: pdf_id, chapter_id, page_number, chunk_index, text, is_active, section_header
- Embedding model: Vertex AI text-multilingual-embedding-002 (768-dim)
- Retrieval: hybrid search — dense (semantic) + keyword (MatchText), fused with RRF
"""
from __future__ import annotations

import logging
import re
from functools import lru_cache

from config import settings

logger = logging.getLogger("ai_service.vector_store")

COLLECTION_NAME = settings.qdrant_collection
EMBEDDING_DIM = 768
CHUNK_SIZE_CHARS = settings.chunk_size_chars
CHUNK_OVERLAP_CHARS = settings.chunk_overlap_chars

# RRF rank constant (standard: 60)
_RRF_K = 60
# Candidates per search leg — internal tuning, not env-configurable
_SEARCH_CANDIDATES = 20
# Final top-k returned from hybrid search
_RETRIEVE_TOP_K = settings.retrieve_top_k


@lru_cache(maxsize=1)
def _get_qdrant():
    """
    Return the process-level QdrantClient singleton (created on first call).

    lru_cache(maxsize=1) ensures only one client instance exists per process.
    Adds api_key only if settings.qdrant_api_key is non-empty, supporting both
    unauthenticated local Qdrant and authenticated cloud deployments.
    """
    from qdrant_client import QdrantClient
    kwargs = {"host": settings.qdrant_host, "port": settings.qdrant_port}
    if settings.qdrant_api_key:
        kwargs["api_key"] = settings.qdrant_api_key
    return QdrantClient(**kwargs)


def ensure_collection() -> None:
    """Create the pdf_chunks collection if it doesn't exist, and ensure all indexes."""
    from qdrant_client.http.models import (
        Distance, VectorParams, PayloadSchemaType, TextIndexParams, TokenizerType,
    )

    client = _get_qdrant()
    existing = {c.name for c in client.get_collections().collections}

    if COLLECTION_NAME not in existing:
        client.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=VectorParams(size=EMBEDDING_DIM, distance=Distance.COSINE),
        )
        logger.info(f"Qdrant collection '{COLLECTION_NAME}' created")

    # Keyword payload indexes
    for field, schema_type in [
        ("pdf_id", PayloadSchemaType.KEYWORD),
        ("chapter_id", PayloadSchemaType.KEYWORD),
        ("is_active", PayloadSchemaType.BOOL),
        ("section_header", PayloadSchemaType.KEYWORD),
    ]:
        try:
            client.create_payload_index(COLLECTION_NAME, field, schema_type)
        except Exception:
            pass  # index already exists

    # Full-text index on text for hybrid search (Qdrant >= 1.5)
    try:
        client.create_payload_index(
            collection_name=COLLECTION_NAME,
            field_name="text",
            field_schema=TextIndexParams(
                type="text",
                tokenizer=TokenizerType.WORD,
                min_token_len=3,
                lowercase=True,
            ),
        )
    except Exception:
        pass  # index already exists or Qdrant version doesn't support it

    logger.info(f"Qdrant collection '{COLLECTION_NAME}' ready")

    # Backfill is_active=True on any existing points that lack the field
    _backfill_is_active(client)


def _backfill_is_active(client) -> None:
    """Set is_active=True on existing points that don't have the field."""
    from qdrant_client.http.models import Filter, IsNullCondition, PayloadField

    try:
        null_filter = Filter(
            must=[IsNullCondition(is_null=PayloadField(key="is_active"))]
        )
        client.set_payload(
            collection_name=COLLECTION_NAME,
            payload={"is_active": True},
            points=null_filter,
        )
        logger.info("Backfilled is_active=True on existing Qdrant points")
    except Exception as exc:
        logger.warning(f"is_active backfill skipped: {exc}")

def _is_section_header(line: str) -> bool:
    """
    Heuristic: a line is a section header if it is short, and is either
    numbered (e.g. "1. Introduction", "2.3 Photosynthesis"),
    all-caps (len > 3), or starts with '#'.
    """
    stripped = line.strip()
    if not stripped or len(stripped) > 120:
        return False
    if stripped.startswith('#'):
        return True
    if re.match(r'^\d+[\.\)]\s', stripped):
        return True
    if len(stripped) > 3 and stripped == stripped.upper() and stripped.replace(' ', '').isalpha():
        return True
    return False

def _split_if_oversized(
    text: str,
    pdf_id: str,
    chapter_id: str,
    page_number: int,
    start_index: int,
    section_header: str,
) -> list[dict]:
    """
    If text exceeds CHUNK_SIZE_CHARS, split at sentence boundaries.
    Returns list of chunk dicts (without id, assigned later).
    """
    if len(text) <= CHUNK_SIZE_CHARS:
        return [{
            "text": text,
            "page_number": page_number,
            "chunk_index": start_index,
            "pdf_id": pdf_id,
            "chapter_id": chapter_id,
            "section_header": section_header,
        }]

    # Split at sentence boundaries (period/question/exclamation + space)
    sentences = re.split(r'(?<=[.!?])\s+', text)
    result = []
    idx = start_index
    bucket = ""
    for sentence in sentences:
        candidate = (bucket + " " + sentence).strip() if bucket else sentence
        if len(candidate) > CHUNK_SIZE_CHARS and bucket:
            result.append({
                "text": bucket.strip(),
                "page_number": page_number,
                "chunk_index": idx,
                "pdf_id": pdf_id,
                "chapter_id": chapter_id,
                "section_header": section_header,
            })
            idx += 1
            bucket = sentence
        else:
            bucket = candidate
    if bucket.strip():
        if len(bucket) > CHUNK_SIZE_CHARS:
            # Single unsplittable sentence (no punctuation) — emit as-is with a warning
            logger.warning(
                f"Chunk at index {idx} exceeds CHUNK_SIZE_CHARS ({len(bucket)} chars) "
                "— no sentence boundary found, emitting oversized chunk."
            )
        result.append({
            "text": bucket.strip(),
            "page_number": page_number,
            "chunk_index": idx,
            "pdf_id": pdf_id,
            "chapter_id": chapter_id,
            "section_header": section_header,
        })
    return result


def chunk_pages(pages: list[dict], pdf_id: str, chapter_id: str) -> list[dict]:
    """
    Paragraph-aware chunking with overlap:
    1. Split on double-newlines (paragraph boundaries).
    2. Detect section headers — flush + hard reset before each new section.
    3. Merge paragraphs until CHUNK_SIZE_CHARS, then flush.
    4. Seed the next chunk with the last CHUNK_OVERLAP_CHARS of the flushed chunk
       so concepts at chunk boundaries are never split out of context.
    5. Split oversized paragraphs at sentence boundaries.
    Returns a list of chunk dicts with page_number, section_header, etc.
    """
    import uuid as _uuid

    chunks: list[dict] = []
    current_section = ""

    def _flush(text: str, page_number: int) -> str:
        """Append split chunks; return overlap seed for the next accumulation."""
        new_chunks = _split_if_oversized(
            text, pdf_id, chapter_id, page_number, len(chunks), current_section
        )
        chunks.extend(new_chunks)
        if new_chunks:
            last_text = new_chunks[-1]["text"]
            return last_text[-CHUNK_OVERLAP_CHARS:] if len(last_text) > CHUNK_OVERLAP_CHARS else ""
        return ""

    for page in pages:
        page_number = page["page_number"]
        text = page["text"]
        raw_paragraphs = re.split(r'\n{2,}', text)
        accumulated = ""

        for para in raw_paragraphs:
            para = para.strip()
            if not para:
                continue

            # Detect section headers — flush and hard-reset (no overlap across sections)
            first_line = para.split('\n')[0]
            if _is_section_header(first_line):
                if accumulated.strip():
                    _flush(accumulated.strip(), page_number)
                accumulated = ""   # hard reset — no overlap at section boundary
                current_section = first_line.strip()
                para_body = '\n'.join(para.split('\n')[1:]).strip()
                if not para_body:
                    continue
                para = para_body

            candidate = (accumulated + "\n\n" + para).strip() if accumulated else para
            if len(candidate) > CHUNK_SIZE_CHARS and accumulated:
                overlap = _flush(accumulated.strip(), page_number)
                accumulated = (overlap + "\n\n" + para).strip() if overlap else para
            else:
                accumulated = candidate

        # Flush remaining at end of page (no overlap needed — next page starts fresh)
        if accumulated.strip():
            _flush(accumulated.strip(), page_number)

    # Assign UUIDs
    for chunk in chunks:
        chunk["id"] = str(_uuid.uuid4())

    return chunks

async def embed_and_store(pdf_id: str, chapter_id: str, pages: list[dict]) -> int:
    """
    Full pipeline: chunk pages → embed → upsert to Qdrant with is_active=True.
    Returns number of chunks stored.
    """
    import asyncio
    import vertexai
    from vertexai.language_models import TextEmbeddingModel
    from qdrant_client.http.models import PointStruct

    chunks = chunk_pages(pages, pdf_id, chapter_id)
    if not chunks:
        logger.warning(f"No chunks generated for pdf_id={pdf_id}")
        return 0

    BATCH = 50
    all_texts = [c["text"] for c in chunks]
    all_vectors: list[list[float]] = []

    vertexai.init(
        project=settings.google_cloud_project,
        location=settings.google_cloud_location_gemini,
    )
    embed_model = TextEmbeddingModel.from_pretrained(settings.embedding_model)

    def _embed(texts):
        return [r.values for r in embed_model.get_embeddings(texts)]

    for i in range(0, len(all_texts), BATCH):
        vectors = await asyncio.get_event_loop().run_in_executor(None, _embed, all_texts[i:i + BATCH])
        all_vectors.extend(vectors)

    client = _get_qdrant()
    points = [
        PointStruct(
            id=chunk["id"],
            vector=vector,
            payload={
                "text": chunk["text"],
                "page_number": chunk["page_number"],
                "chunk_index": chunk["chunk_index"],
                "pdf_id": chunk["pdf_id"],
                "chapter_id": chunk["chapter_id"],
                "section_header": chunk.get("section_header", ""),
                "is_active": True,
            },
        )
        for chunk, vector in zip(chunks, all_vectors)
    ]

    UPSERT_BATCH = 100
    for i in range(0, len(points), UPSERT_BATCH):
        client.upsert(collection_name=COLLECTION_NAME, points=points[i:i + UPSERT_BATCH])

    logger.info(f"Stored {len(points)} chunks for pdf_id={pdf_id}, chapter_id={chapter_id}")
    return len(points)

async def embed_text(text: str) -> list[float]:
    """
    Embed a single text string using the configured Vertex AI embedding model.

    The model (settings.embedding_model) defaults to
    text-multilingual-embedding-002 (768 dimensions), which handles
    Hindi/English code-switching common in CBSE content.

    Returns:
        A list of 768 float values representing the text embedding.
    """
    import asyncio
    import vertexai
    from vertexai.language_models import TextEmbeddingModel

    vertexai.init(
        project=settings.google_cloud_project,
        location=settings.google_cloud_location_gemini,
    )

    def _call():
        model = TextEmbeddingModel.from_pretrained(settings.embedding_model)
        result = model.get_embeddings([text])
        return result[0].values

    return await asyncio.get_event_loop().run_in_executor(None, _call)

def _rrf_merge(
    dense_hits: list,
    keyword_hits: list,
    top_k: int,
) -> list:
    """
    Reciprocal Rank Fusion of two result lists.
    Each hit must have an .id attribute.
    Returns merged list sorted by RRF score descending, truncated to top_k.
    """
    scores: dict = {}
    hit_map: dict = {}

    for rank, hit in enumerate(dense_hits):
        scores[hit.id] = scores.get(hit.id, 0.0) + 1.0 / (_RRF_K + rank + 1)
        hit_map[hit.id] = hit

    for rank, hit in enumerate(keyword_hits):
        scores[hit.id] = scores.get(hit.id, 0.0) + 1.0 / (_RRF_K + rank + 1)
        hit_map[hit.id] = hit

    sorted_ids = sorted(scores, key=lambda k: scores[k], reverse=True)
    return [hit_map[i] for i in sorted_ids[:top_k]]


async def retrieve_context(
    chapter_id: str,
    query: str,
    top_k: int = _RETRIEVE_TOP_K,
) -> str:
    """
    Hybrid retrieval: dense semantic search + keyword (MatchText) search, fused with RRF.
    Only returns chunks where is_active=True.
    Returns the top-k chunks joined as a single context string.
    """
    import asyncio
    from qdrant_client.http.models import Filter, FieldCondition, MatchValue, MatchText

    query_vector = await embed_text(query)
    client = _get_qdrant()

    base_filter = Filter(
        must=[
            FieldCondition(key="chapter_id", match=MatchValue(value=chapter_id)),
            FieldCondition(key="is_active", match=MatchValue(value=True)),
        ]
    )

    keyword_filter = Filter(
        must=[
            FieldCondition(key="chapter_id", match=MatchValue(value=chapter_id)),
            FieldCondition(key="is_active", match=MatchValue(value=True)),
            FieldCondition(key="text", match=MatchText(text=query)),
        ]
    )

    def _dense_search():
        return client.query_points(
            collection_name=COLLECTION_NAME,
            query=query_vector,
            query_filter=base_filter,
            limit=_SEARCH_CANDIDATES,
            with_payload=True,
        ).points

    def _keyword_search():
        try:
            return client.query_points(
                collection_name=COLLECTION_NAME,
                query=query_vector,
                query_filter=keyword_filter,
                limit=_SEARCH_CANDIDATES,
                with_payload=True,
            ).points
        except Exception as exc:
            logger.warning(f"Keyword search failed (falling back to dense only): {exc}")
            return []

    dense_hits, keyword_hits = await asyncio.gather(
        asyncio.get_event_loop().run_in_executor(None, _dense_search),
        asyncio.get_event_loop().run_in_executor(None, _keyword_search),
    )

    if not dense_hits and not keyword_hits:
        logger.warning(f"No Qdrant results for chapter_id={chapter_id}")
        return ""

    merged = _rrf_merge(dense_hits, keyword_hits, top_k=top_k)

    # Sort merged results by document order for coherent reading
    chunks = sorted(
        merged,
        key=lambda r: (r.payload.get("page_number", 0), r.payload.get("chunk_index", 0)),
    )
    return "\n\n".join(r.payload.get("text", "") for r in chunks)


async def resolve_context(
    chapter_id: str,
    query: str,
    fallback_text: str,
    log_prefix: str = "",
) -> str:
    """
    Shared context resolver used by both generate and modify endpoints.
    Tries Qdrant first; falls back to fallback_text if no chunks found or on error.
    Raises ValueError if no context is available at all.
    """
    context_text = fallback_text
    if chapter_id:
        try:
            qdrant_context = await retrieve_context(chapter_id=chapter_id, query=query)
            if qdrant_context:
                context_text = qdrant_context
                logger.info(
                    f"{log_prefix}using Qdrant context ({len(qdrant_context)} chars) "
                    f"for chapter {chapter_id}"
                )
            else:
                logger.info(
                    f"{log_prefix}no Qdrant chunks for chapter {chapter_id}, "
                    "falling back to inline context_text"
                )
        except Exception as exc:
            logger.warning(
                f"{log_prefix}Qdrant retrieval failed for chapter {chapter_id}: {exc} "
                "— falling back to inline context_text"
            )
    return context_text


async def retrieve_full_chapter(chapter_id: str) -> str:
    """
    Returns ALL active chunks for a chapter, sorted by document order.
    Used for whole-chapter assessment modes (e.g. Competency Assessment)
    where topic-focused semantic retrieval is too narrow.
    """
    import asyncio
    from qdrant_client.http.models import Filter, FieldCondition, MatchValue

    client = _get_qdrant()
    chapter_filter = Filter(
        must=[
            FieldCondition(key="chapter_id", match=MatchValue(value=chapter_id)),
            FieldCondition(key="is_active", match=MatchValue(value=True)),
        ]
    )

    def _scroll():
        all_points = []
        offset = None
        while True:
            results, next_offset = client.scroll(
                collection_name=COLLECTION_NAME,
                scroll_filter=chapter_filter,
                limit=100,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
            all_points.extend(results)
            if next_offset is None:
                break
            offset = next_offset
        return all_points

    points = await asyncio.get_event_loop().run_in_executor(None, _scroll)

    if not points:
        logger.warning(f"retrieve_full_chapter: no chunks found for chapter_id={chapter_id}")
        return ""

    points.sort(key=lambda p: (
        p.payload.get("page_number", 0),
        p.payload.get("chunk_index", 0),
    ))

    logger.info(f"retrieve_full_chapter: {len(points)} chunks for chapter_id={chapter_id}")
    return "\n\n".join(p.payload.get("text", "") for p in points)


def deactivate_by_pdf_id(pdf_id: str) -> None:
    """
    Soft-delete: set is_active=False on all vectors for this pdf_id.
    Vectors remain in Qdrant but are excluded from retrieval.
    """
    from qdrant_client.http.models import Filter, FieldCondition, MatchValue

    client = _get_qdrant()
    client.set_payload(
        collection_name=COLLECTION_NAME,
        payload={"is_active": False},
        points=Filter(must=[FieldCondition(key="pdf_id", match=MatchValue(value=pdf_id))]),
    )
    logger.info(f"Deactivated Qdrant vectors for pdf_id={pdf_id}")


def reactivate_by_pdf_id(pdf_id: str) -> None:
    """
    Restore: set is_active=True on all vectors for this pdf_id.
    """
    from qdrant_client.http.models import Filter, FieldCondition, MatchValue

    client = _get_qdrant()
    client.set_payload(
        collection_name=COLLECTION_NAME,
        payload={"is_active": True},
        points=Filter(must=[FieldCondition(key="pdf_id", match=MatchValue(value=pdf_id))]),
    )
    logger.info(f"Reactivated Qdrant vectors for pdf_id={pdf_id}")


def hard_delete_by_pdf_id(pdf_id: str) -> None:
    """
    Permanent delete: remove all vectors for this pdf_id from Qdrant.
    Use only for permanent cleanup (e.g. GDPR deletion).
    """
    from qdrant_client.http.models import Filter, FieldCondition, MatchValue, FilterSelector

    client = _get_qdrant()
    client.delete(
        collection_name=COLLECTION_NAME,
        points_selector=FilterSelector(
            filter=Filter(must=[FieldCondition(key="pdf_id", match=MatchValue(value=pdf_id))])
        ),
    )
    logger.info(f"Hard-deleted Qdrant vectors for pdf_id={pdf_id}")
