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
    # Use the question text as the Qdrant query — retrieves chunks semantically
    # closest to this specific question, more targeted than topic/chapter name.
    question_query = req.question.get("question_text") or req.topic or req.chapter
    context_text = await resolve_context(
        chapter_id=req.chapter_id,
        query=question_query,
        fallback_text=req.context_text,
        log_prefix=f"Session {req.session_id}: ",
    )

    # Strip fields not useful as LLM input before building the prompt
    _STRIP_FIELDS = {
        "image_prompt", "image_base64", "image",
        "correct_answers_count", "options_count",
        "is_hots", "ai_generated", "is_active", "created_by",
    }
    question_for_prompt = {k: v for k, v in req.question.items() if k not in _STRIP_FIELDS}

    # Clean option dicts — Django's OptionSerializer adds 'question' (FK UUID) and
    # 'question_text' (the parent question's text) to every option object.
    # These fields are invisible noise that confuse the AI when it reads the original question.
    _OPTION_STRIP_FIELDS = {"id", "question", "question_text"}
    if "options" in question_for_prompt and isinstance(question_for_prompt["options"], list):
        question_for_prompt["options"] = [
            {k: v for k, v in opt.items() if k not in _OPTION_STRIP_FIELDS}
            for opt in question_for_prompt["options"]
            if isinstance(opt, dict)
        ]

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
