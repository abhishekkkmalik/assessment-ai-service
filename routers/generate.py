import asyncio
import uuid
import logging

from fastapi import APIRouter, HTTPException

from schemas.mcq import GenerateRequest, GenerateResponse, GeneratedQuestion
from services.llm_provider import mcq_service
from services.prompt_builder import (
    build_system_prompt, build_user_prompt, build_batch_fix_prompt,
    build_prereq_system_prompt, build_prereq_user_prompt,
    build_competency_system_prompt, build_competency_user_prompt,
    build_comprehension_system_prompt, build_comprehension_user_prompt,
    build_grammar_system_prompt, build_grammar_user_prompt,
    build_lab_manual_system_prompt, build_lab_manual_user_prompt,
)
from services.mcq_validator import (
    validate_questions, validate_single, question_hash, build_fix_instruction,
)
from services.vector_store import resolve_context, retrieve_full_chapter
from services.answer_shuffler import shuffle_answer_positions

router = APIRouter()
logger = logging.getLogger("ai_service.generate")

PREREQUISITE_TOPIC = "Previous Knowledge Testing"
COMPETENCY_TOPIC = "Competency Based Questions"
COMPREHENSION_TOPIC = "Comprehension Passage"
GRAMMAR_TOPIC = "English Grammar"
LAB_MANUAL_TOPIC = "Lab Manual"


@router.post("/ai/generate", response_model=GenerateResponse)
async def generate_assessment(req: GenerateRequest):
    # ── 1. Resolve context + build prompts ──────────────────────────────────
    _buffer = (req.num_questions - 1) // 5 + 1  # over-generate buffer
    is_prereq = req.topic.strip().lower() == PREREQUISITE_TOPIC.lower()
    is_competency = req.topic.strip().lower() == COMPETENCY_TOPIC.lower()
    is_comprehension = req.topic.strip().lower() == COMPREHENSION_TOPIC.lower()
    is_grammar = req.topic.strip().lower() == GRAMMAR_TOPIC.lower()
    is_lab_manual = req.topic.strip().lower() == LAB_MANUAL_TOPIC.lower()

    if is_grammar:
        logger.info(f"Session {req.session_id}: English grammar mode (grade {req.grade_level})")
        system_prompt = build_grammar_system_prompt(
            grade_level=req.grade_level,
            subject=req.subject,
            chapter=req.chapter,
            board=req.board,
        )
        user_prompt = build_grammar_user_prompt(
            chapter=req.chapter,
            num_questions=req.num_questions + _buffer,
            board=req.board,
            grade_level=req.grade_level,
            topic=req.topic,
            existing_question_stems=req.existing_question_stems or None,
        )
    elif is_comprehension:
        logger.info(f"Session {req.session_id}: comprehension passage mode — AI generates passage (grade {req.grade_level})")
        system_prompt = build_comprehension_system_prompt(
            grade_level=req.grade_level,
            subject=req.subject,
            chapter=req.chapter,
            board=req.board,
        )
        user_prompt = build_comprehension_user_prompt(
            chapter=req.chapter,
            num_questions=req.num_questions + _buffer,
            board=req.board,
            grade_level=req.grade_level,
            existing_question_stems=req.existing_question_stems or None,
        )
    elif is_competency:
        logger.info(f"Session {req.session_id}: competency assessment mode — full chapter, levels 4–5 only")
        context_text = await retrieve_full_chapter(chapter_id=req.chapter_id)
        if not context_text:
            context_text = req.context_text
        if not context_text:
            raise HTTPException(
                status_code=422,
                detail="No context available. Upload a PDF for this chapter first.",
            )
        system_prompt = build_competency_system_prompt(
            grade_level=req.grade_level,
            subject=req.subject,
            chapter=req.chapter,
            board=req.board,
        )
        user_prompt = build_competency_user_prompt(
            chapter=req.chapter,
            num_questions=req.num_questions + _buffer,
            context_text=context_text,
            existing_question_stems=req.existing_question_stems or None,
        )
    elif is_prereq:
        # Previous Knowledge Testing — skip Qdrant, use AI's own curriculum knowledge
        logger.info(f"Session {req.session_id}: previous knowledge testing mode (grade {req.grade_level} → {max(1, req.grade_level - 1)})")
        system_prompt = build_prereq_system_prompt(
            grade_level=req.grade_level,
            subject=req.subject,
            chapter=req.chapter,
            board=req.board,
        )
        user_prompt = build_prereq_user_prompt(
            chapter=req.chapter,
            num_questions=req.num_questions + _buffer,
            board=req.board,
            grade_level=req.grade_level,
            existing_question_stems=req.existing_question_stems or None,
        )
    elif is_lab_manual:
        logger.info(f"Session {req.session_id}: lab manual mode — full experiment content (grade {req.grade_level})")
        context_text = await retrieve_full_chapter(chapter_id=req.chapter_id)
        if not context_text:
            context_text = req.context_text
        if not context_text:
            raise HTTPException(
                status_code=422,
                detail="No context available. Upload the lab manual PDF for this experiment first.",
            )
        system_prompt = build_lab_manual_system_prompt(
            grade_level=req.grade_level,
            subject=req.subject,
            chapter=req.chapter,
            board=req.board,
        )
        user_prompt = build_lab_manual_user_prompt(
            chapter=req.chapter,
            num_questions=req.num_questions + _buffer,
            context_text=context_text,
            existing_question_stems=req.existing_question_stems or None,
        )
    else:
        context_text = await resolve_context(
            chapter_id=req.chapter_id,
            query=req.topic or req.chapter,
            fallback_text=req.context_text,
            log_prefix=f"Session {req.session_id}: ",
        )

        if not context_text:
            raise HTTPException(
                status_code=422,
                detail=(
                    "No context available for generation. "
                    "Upload a PDF for this chapter or provide context_text."
                ),
            )

        system_prompt = build_system_prompt(
            grade_level=req.grade_level,
            subject=req.subject,
            chapter=req.chapter,
            board=req.board,
        )
        user_prompt = build_user_prompt(
            chapter=req.chapter,
            topic=req.topic,
            num_questions=req.num_questions + _buffer,
            context_text=context_text,
            existing_question_stems=req.existing_question_stems or None,
        )

    # ── 2. Single generation call ────────────────────────────────────────────
    try:
        raw = await mcq_service.generate(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )
    except Exception as exc:
        logger.error(f"Generation failed for session {req.session_id}: {exc}")
        raise HTTPException(status_code=502, detail=f"LLM generation failed: {exc}")

    total_time_ms = raw.get("generation_time_ms", 0) or 0

    valid, rejected = validate_questions(raw.get("questions", []))
    seen_hashes: set[str] = {
        question_hash(q["question_text"]) for q in valid if q.get("question_text")
    }
    logger.info(
        f"Session {req.session_id}: {len(valid)} valid, {len(rejected)} rejected"
    )

    # ── 3. Batch fix pass — fix ALL rejected questions in one LLM call ───────
    if rejected:
        fix_items = [
            {
                "index": i,
                "fix_instruction": build_fix_instruction(q.get("rejection_reason", ""), q),
                "question": {k: v for k, v in q.items() if k != "rejection_reason"},
            }
            for i, q in enumerate(rejected)
        ]
        try:
            initial_valid_count = len(valid)
            fix_user_prompt = build_batch_fix_prompt(fix_items)
            fixed_list = await mcq_service.fix_questions_batch(system_prompt, fix_user_prompt)
            for fixed in fixed_list:
                reason = validate_single(fixed, seen_hashes)
                if reason is None:
                    seen_hashes.add(question_hash(fixed["question_text"]))
                    valid.append(fixed)
                else:
                    logger.warning(
                        f"Session {req.session_id}: batch-fixed question still invalid: {reason}"
                    )
            logger.info(
                f"Session {req.session_id}: batch fix — {len(fixed_list)} returned, "
                f"{len(valid) - initial_valid_count} accepted"
            )
        except Exception as exc:
            logger.warning(f"Session {req.session_id}: batch fix failed: {exc}")

    # For competency mode: discard any questions below level 4
    if is_competency:
        valid = [q for q in valid if int(q.get("difficulty_level", 0)) >= 4]

    # Trim to requested count (over-generation buffer may produce extras)
    valid = valid[: req.num_questions]

    # ── 4. Shuffle answer positions ──────────────────────────────────────────
    valid = shuffle_answer_positions(valid)

    # ── 6. Finalize questions (exp_points sync + Gemini image generation) ──
    # Run concurrently — image calls per question are independent I/O
    from services.question_utils import finalize_question
    await asyncio.gather(
        *[finalize_question(
            q,
            log_prefix=f"Session {req.session_id}: ",
            subject=req.subject,
            grade_level=req.grade_level,
        ) for q in valid]
    )

    # ── 7. Build response ────────────────────────────────────────────────────
    warning = None
    if len(valid) < req.num_questions:
        warning = (
            f"Requested {req.num_questions} questions but only "
            f"{len(valid)} could be generated after fix and retry."
        )
        logger.warning(f"Session {req.session_id}: {warning}")

    questions_out = []
    for i, q in enumerate(valid):
        q.setdefault("id", f"q_{uuid.uuid4().hex[:12]}")
        q.setdefault("question_type", "mcq_single")
        q["question_order"] = i + 1
        questions_out.append(GeneratedQuestion(**q))

    return GenerateResponse(
        session_id=req.session_id,
        questions=questions_out,
        generation_time_ms=total_time_ms,
        rejected_count=req.num_questions - len(valid),
        warning=warning,
    )
