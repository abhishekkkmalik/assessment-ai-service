"""
Shared post-processing for generated and modified questions.
"""
import logging

logger = logging.getLogger("ai_service.question_utils")

# Experience points awarded to students per difficulty level.
# Levels 1–2 are recall/understanding (low cognitive load) → 5 pts.
# Level 3 (apply) doubles the reward → 10 pts.
# Levels 4–5 (analyze/evaluate) award 15 and 20 pts respectively.
_EXP_BY_DIFFICULTY = {1: 5, 2: 5, 3: 10, 4: 15, 5: 20}


async def finalize_question(
    q: dict,
    log_prefix: str = "",
    subject: str = "",
    grade_level: int = 0,
) -> None:
    """
    In-place post-processing applied to every question before returning to the caller.

    Two operations (order matters):
      1. exp_points sync — overwrites q["exp_points"] from _EXP_BY_DIFFICULTY
         keyed on q["difficulty_level"].  Ensures the reward value stays
         consistent if the LLM changed the difficulty during a modify call.

      2. Image generation — if q["image_prompt"] is a non-empty, non-"null"
         string, calls generate_question_image() to produce a base64-encoded
         PNG diagram.  On any failure (timeout, API error, empty response)
         q["image_base64"] is set to None rather than raising, so a diagram
         failure never blocks the question from being returned.

    Args:
        q:           Question dict — mutated in-place.
        log_prefix:  Optional prefix for log messages (e.g. "Session abc123: ").
        subject:     Subject name passed to the image style selector.
        grade_level: Grade level passed to the image prompt builder.
    """
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
