"""
LLM provider for MCQ generation and modification.
Single provider: Gemini 2.5 Flash via Vertex AI.

Architecture:
  GeminiProvider      — thin wrapper around the Vertex AI GenerativeModel SDK.
                        Each method builds a combined prompt, calls the model
                        with a strict JSON response schema, and parses the result.
  MCQGenerationService — lazy-initialised singleton that owns one GeminiProvider
                         instance and exposes the three operations used by routers:
                         generate(), fix_questions_batch(), and modify().
"""
from __future__ import annotations

import asyncio
import json
import logging
import time

from config import settings
from services.prompt_builder import QUESTION_ITEM_SCHEMA

logger = logging.getLogger("ai_service.llm")


class GeminiProvider:
    """
    Direct Vertex AI wrapper for Gemini 2.5 Flash.

    Initialised once per process via MCQGenerationService._get_provider().
    All three methods offload the blocking Vertex AI SDK call to a thread
    executor so they can be awaited without blocking the event loop.
    """

    def __init__(self):
        import vertexai
        vertexai.init(
            project=settings.google_cloud_project,
            location=settings.google_cloud_location_gemini,
        )
        self.model_name = settings.gemini_model

    async def generate_mcqs(self, system_prompt: str, user_prompt: str) -> dict:
        """
        Call Gemini to generate a batch of MCQ questions.

        Combines system and user prompts into a single string (Vertex AI does
        not support the system-role parameter in the Python SDK).  Uses a
        constrained JSON schema (response_mime_type + response_schema) to
        guarantee a parseable {"questions": [...]} response.

        Returns:
            {"questions": [<question dict>, ...]}

        Raises:
            ValueError: if Gemini returns non-JSON or the response is truncated.
        """
        from vertexai.generative_models import GenerativeModel, GenerationConfig

        gemini_schema = {
            "type": "object",
            "properties": {"questions": {"type": "array", "items": QUESTION_ITEM_SCHEMA}},
            "required": ["questions"],
        }

        combined_prompt = (
            f"{system_prompt}\n\n"
            "Return your response as a JSON object with a 'questions' array. "
            "No markdown, no code fences, only the raw JSON object.\n\n"
            f"{user_prompt}"
        )

        def _call():
            model = GenerativeModel(self.model_name)
            return model.generate_content(
                combined_prompt,
                generation_config=GenerationConfig(
                    temperature=0.3,
                    max_output_tokens=65536,
                    response_mime_type="application/json",
                    response_schema=gemini_schema,
                ),
            )

        response = await asyncio.get_event_loop().run_in_executor(None, _call)
        try:
            data = json.loads(response.text)
        except json.JSONDecodeError as e:
            finish_reason = (
                getattr(response.candidates[0], "finish_reason", "unknown")
                if response.candidates else "unknown"
            )
            logger.error(
                f"Gemini JSON parse failed (finish_reason={finish_reason}): {e} "
                f"— response length={len(response.text)}"
            )
            raise ValueError(f"Gemini returned invalid JSON (finish_reason={finish_reason}): {e}")
        return {"questions": data.get("questions", [])}

    async def fix_mcqs_batch(self, system_prompt: str, user_prompt: str) -> list[dict]:
        """
        Call Gemini to fix a batch of structurally invalid questions.

        The user_prompt contains each rejected question alongside its specific
        fix instruction.  Temperature is set lower (0.2) than generation (0.3)
        to encourage targeted, minimal edits rather than creative rewrites.

        Returns:
            List of fixed question dicts (same length and order as the input batch).

        Raises:
            ValueError: if Gemini returns non-JSON or the response is truncated.
        """
        from vertexai.generative_models import GenerativeModel, GenerationConfig

        batch_schema = {
            "type": "object",
            "properties": {"questions": {"type": "array", "items": QUESTION_ITEM_SCHEMA}},
            "required": ["questions"],
        }

        combined = (
            f"{system_prompt}\n\n{user_prompt}\n\n"
            "Return the fixed questions as a JSON object with a 'questions' array."
        )

        def _call():
            model = GenerativeModel(self.model_name)
            return model.generate_content(
                combined,
                generation_config=GenerationConfig(
                    temperature=0.2,
                    max_output_tokens=65536,
                    response_mime_type="application/json",
                    response_schema=batch_schema,
                ),
            )

        response = await asyncio.get_event_loop().run_in_executor(None, _call)
        try:
            data = json.loads(response.text)
        except json.JSONDecodeError as e:
            finish_reason = (
                getattr(response.candidates[0], "finish_reason", "unknown")
                if response.candidates else "unknown"
            )
            logger.error(
                f"Gemini batch fix JSON parse failed (finish_reason={finish_reason}): {e} "
                f"— response length={len(response.text)}"
            )
            raise ValueError(f"Gemini batch fix returned invalid JSON (finish_reason={finish_reason}): {e}")
        return data.get("questions", [])

    async def modify(self, system: str, user: str) -> dict:
        """
        Call Gemini to apply a single modification to one question.

        Uses a slightly higher temperature (0.4) than generation to allow
        creative rephrasing while still respecting the JSON schema constraint.
        The schema is QUESTION_ITEM_SCHEMA (single object, not an array).

        Returns:
            A single question dict as returned by Gemini.

        Raises:
            ValueError: if Gemini returns non-JSON or the response is truncated.
        """
        from vertexai.generative_models import GenerativeModel, GenerationConfig

        combined = f"{system}\n\n{user}\n\nReturn only the modified question as a raw JSON object."

        def _call():
            model = GenerativeModel(self.model_name)
            return model.generate_content(
                combined,
                generation_config=GenerationConfig(
                    temperature=0.4,
                    max_output_tokens=65536,
                    response_mime_type="application/json",
                    response_schema=QUESTION_ITEM_SCHEMA,
                ),
            )

        response = await asyncio.get_event_loop().run_in_executor(None, _call)
        try:
            data = json.loads(response.text)
        except json.JSONDecodeError as e:
            finish_reason = (
                getattr(response.candidates[0], "finish_reason", "unknown")
                if response.candidates else "unknown"
            )
            logger.error(
                f"Gemini modify JSON parse failed (finish_reason={finish_reason}): {e} "
                f"— response length={len(response.text)}"
            )
            raise ValueError(f"Gemini modify returned invalid JSON (finish_reason={finish_reason}): {e}")
        return data


class MCQGenerationService:
    """
    Service layer over GeminiProvider used by all routers.

    Lazily initialises GeminiProvider on the first call so that Vertex AI is
    not contacted during import or startup (avoids slow startup if GCP
    credentials are not yet available).  Measures and logs wall-clock latency
    for every LLM call.

    Singleton instance `mcq_service` is created at module level and imported
    directly by the routers.
    """

    def __init__(self):
        self._provider: GeminiProvider | None = None

    def _get_provider(self) -> GeminiProvider:
        """Lazily create and cache the GeminiProvider instance."""
        if self._provider is None:
            self._provider = GeminiProvider()
        return self._provider

    async def generate(self, system_prompt: str, user_prompt: str) -> dict:
        """
        Generate MCQ questions and return them with timing metadata.

        Returns:
            {"questions": [...], "generation_time_ms": int}
        """
        start = time.monotonic()
        result = await self._get_provider().generate_mcqs(system_prompt, user_prompt)
        elapsed_ms = int((time.monotonic() - start) * 1000)
        logger.info(f"Gemini generation succeeded ({len(result['questions'])} questions) in {elapsed_ms}ms")
        result["generation_time_ms"] = elapsed_ms
        return result

    async def fix_questions_batch(self, system_prompt: str, user_prompt: str) -> list[dict]:
        """
        Send rejected questions back to Gemini for targeted fixes in one call.

        Returns:
            List of fixed question dicts.
        """
        start = time.monotonic()
        fixed = await self._get_provider().fix_mcqs_batch(system_prompt, user_prompt)
        elapsed_ms = int((time.monotonic() - start) * 1000)
        logger.info(f"Batch fix returned {len(fixed)} questions in {elapsed_ms}ms")
        return fixed

    async def modify(self, system_prompt: str, user_prompt: str) -> dict:
        """
        Modify a single question via Gemini.
        Prompt building and response post-processing are done by the caller.
        Returns the raw parsed question dict from Gemini.
        """
        start = time.monotonic()
        result = await self._get_provider().modify(system_prompt, user_prompt)
        elapsed_ms = int((time.monotonic() - start) * 1000)
        logger.info(f"Gemini modify succeeded in {elapsed_ms}ms")
        return result


# Singleton used by routers
mcq_service = MCQGenerationService()
