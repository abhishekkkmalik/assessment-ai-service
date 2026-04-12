"""
POST /ai/modify — single MCQ question modification endpoint.

Accepts a question dict and a modification type (REPHRASE, INCREASE_DIFFICULTY,
DECREASE_DIFFICULTY, CHANGE_OPTIONS, REGENERATE, or CUSTOM with a free-text
instruction), then returns the Gemini-modified question.
"""
import logging

from fastapi import APIRouter, HTTPException

from schemas.mcq import ModifyRequest, ModifyResponse
from services.llm_provider import mcq_service
from services.mcq_validator import validate_questions
from services.prompt_builder import build_system_prompt, build_modify_user_prompt
from services.question_utils import finalize_question
from services.vector_store import resolve_context

router = APIRouter()
logger = logging.getLogger("ai_service.modify")


@router.post("/ai/modify", response_model=ModifyResponse)
async def modify_question(req: ModifyRequest):
    """
    Modify a single MCQ question via Gemini 2.5 Flash.

    Pipeline:
      1. Context retrieval — uses the question's own text as the Qdrant search
         query (more targeted than the topic/chapter name).  Falls back to
         req.context_text if no Qdrant chunks are found.
      2. Prompt building — strips large image fields (image_prompt, image_base64)
         from the question before sending to Gemini to save tokens.
      3. LLM modification call — sends system + user prompt to Gemini and parses
         the returned question JSON.
      4. Field preservation — restores 'id' and 'question_order' from the original
         question if Gemini omitted them.
      5. Normalisation — clears image_prompt if Gemini returned "null" or "", and
         removes correct_order from non-rearrange question options.
      6. Finalisation — syncs exp_points with difficulty_level and generates a new
         diagram via Gemini Image if the question has an image_prompt.
      7. Validation — runs structural validation; a failure is logged as a warning
         but never blocks the response (the teacher's explicit change is honoured).

    Args:
        req: ModifyRequest with session_id, question (original question dict),
             modification_type, instruction (for CUSTOM type), grade_level,
             subject, chapter, topic, board, chapter_id, and context_text.

    Returns:
        ModifyResponse with the modified question dict.

    Raises:
        HTTPException 502: Gemini call failed.
    """
    # Use the question text as the Qdrant query — retrieves chunks semantically
    # closest to this specific question, more targeted than topic/chapter name.
    question_query = req.question.get("question_text") or req.topic or req.chapter
    context_text = await resolve_context(
        chapter_id=req.chapter_id,
        query=question_query,
        fallback_text=req.context_text,
        log_prefix=f"Session {req.session_id}: ",
    )

    # Strip large fields not useful as LLM input before building the prompt
    _STRIP_FIELDS = {"image_prompt", "image_base64"}
    question_for_prompt = {k: v for k, v in req.question.items() if k not in _STRIP_FIELDS}

    system_prompt = build_system_prompt(
        grade_level=req.grade_level,
        subject=req.subject,
        chapter=req.chapter,
        board=req.board,
    )
    user_prompt = build_modify_user_prompt(
        question=question_for_prompt,
        modification_type=req.modification_type,
        instruction=req.instruction,
        context_text=context_text,
    )

    try:
        modified = await mcq_service.modify(system_prompt, user_prompt)
    except Exception as exc:
        logger.error(f"Modify failed for session {req.session_id}: {exc}")
        raise HTTPException(status_code=502, detail=f"LLM modification failed: {exc}")

    # Preserve fields the LLM doesn't return
    if "id" in req.question and "id" not in modified:
        modified["id"] = req.question["id"]
    if "question_order" not in modified and "question_order" in req.question:
        modified["question_order"] = req.question["question_order"]

    # Normalise nullable fields
    if modified.get("image_prompt") in ("null", ""):
        modified["image_prompt"] = None
    if modified.get("question_type") != "rearrange":
        for opt in modified.get("options", []):
            opt["correct_order"] = None

    # Sync exp_points with difficulty + generate diagram via Gemini image
    await finalize_question(
        modified,
        log_prefix=f"Session {req.session_id}: ",
        subject=req.subject,
        grade_level=req.grade_level,
    )

    # Validate — log warning but never block the teacher's explicit change
    valid, rejected = validate_questions([modified])
    if rejected:
        logger.warning(
            f"Session {req.session_id}: modified question failed validation: "
            f"{rejected[0].get('rejection_reason')}"
        )
        return ModifyResponse(session_id=req.session_id, question=modified)

    return ModifyResponse(session_id=req.session_id, question=valid[0])
