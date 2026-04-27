"""
Shared post-processing for generated and modified questions.
"""
import logging

logger = logging.getLogger("ai_service.question_utils")

_EXP_BY_DIFFICULTY = {1: 5, 2: 5, 3: 10, 4: 15, 5: 20}


async def finalize_question(
    q: dict,
    log_prefix: str = "",
    subject: str = "",
    grade_level: int = 0,
) -> None:
    """
    In-place post-processing applied to every question before returning to the caller.

    - Updates exp_points to match current difficulty_level.
    - Calls Gemini 3.1 Flash Image to generate image_base64 if image_prompt is present.
    """
    # For rearrange questions, sort options by correct_order so the response is in correct sequence
    if q.get("question_type") == "rearrange":
        options = q.get("options") or []
        q["options"] = sorted(options, key=lambda o: o.get("correct_order", 0))

    # Keep exp_points in sync with difficulty (modify may change difficulty without updating exp_points)
    diff = q.get("difficulty_level")
    if diff in _EXP_BY_DIFFICULTY:
        q["exp_points"] = _EXP_BY_DIFFICULTY[diff]

    # Generate diagram via Gemini 3.1 Flash Image if image_prompt is present
    image_prompt = (q.get("image_prompt") or "").strip()
    if not image_prompt or image_prompt == "null":
        q["image_base64"] = None
        return

    question_text = q.get("question_text", "")
    correct_answers = [
        opt["option_text"]
        for opt in q.get("options", [])
        if opt.get("is_correct")
    ]

    try:
        from services.imagen_client import generate_question_image
        q["image_base64"] = await generate_question_image(
            image_prompt=image_prompt,
            question_text=question_text,
            correct_answers=correct_answers,
            subject=subject,
            grade_level=grade_level,
        )
        if q["image_base64"] is None:
            logger.warning(f"{log_prefix}Gemini image returned no image for prompt: {image_prompt[:80]}")
    except Exception as exc:
        logger.warning(f"{log_prefix}Gemini image generation failed: {exc}")
        q["image_base64"] = None
