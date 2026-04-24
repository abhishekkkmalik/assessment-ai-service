"""
Build system and user prompts for MCQ generation and modification.
"""
from __future__ import annotations


_MOBILE_FORMAT_RULES = """
MOBILE DISPLAY RULES — strictly required (the app has NO LaTeX renderer):
  FORBIDDEN — never output:
    • LaTeX commands: \\frac{}{} \\sqrt{} \\int \\sum \\alpha \\beta \\pi \\times \\cdot \\sin \\cos \\log \\lim
    • Dollar delimiters: $...$ or $$...$$ or \\(...\\) or \\[...\\]
    • Caret for power: x^2 or x^{2}
    • Underscore for subscript: H_2O or x_{1}
    • Any backslash before a word: \\alpha, \\theta, \\Delta, etc.

  USE INSTEAD:
    Powers     : ⁰ ¹ ² ³ ⁴ ⁵  (e.g. x², cm², 10³)
    Subscripts : ₀ ₁ ₂ ₃ ₄  (e.g. H₂O, CO₂, x₁)
    Roots      : √ ∛  (e.g. √2, √(x+1))
    Fractions  : write as a/b  (e.g. 1/2, (x+1)/(x-1))
    Greek      : α β γ δ ε θ λ μ π σ φ ω Δ Σ Ω
    Operators  : × ÷ ± ≤ ≥ ≠ ≈ ∞ ∫ ∑ ∂
    Other      : ° · → ← ⇒ ∈ ∩ ∪

  This rule applies to ALL fields: question_text, option text, hint, explanation.
"""

_BLOOM_MAPPING = """
Difficulty ↔ Bloom's Taxonomy mapping (follow strictly):
  Level 1 — REMEMBER: recall facts, definitions, dates
    Stems: "What is...", "Name the...", "Define...", "Which of the following is..."
  Level 2 — UNDERSTAND: explain concepts in own words
    Stems: "Which best describes...", "What does X mean...", "Why does X happen..."
  Level 3 — APPLY: use knowledge in new situations
    Stems: "If X occurs, what happens...", "Calculate...", "How would you use..."
  Level 4 — ANALYZE: break down, compare, contrast
    Stems: "What is the relationship between...", "Compare X and Y...", "Why is X different from Y..."
  Level 5 — EVALUATE / CREATE: judge, design, propose
    Stems: "Which solution is most appropriate...", "Justify...", "What would you recommend..."
"""

_SOURCE_INDEPENDENCE_RULES = """
Source-independence rules — CRITICAL (applies to ALL fields: question_text, options, hint, explanation):
  Questions and explanations must read as STANDALONE educational content.
  The student must NEVER know that a PDF, textbook, or passage was involved.

  FORBIDDEN phrases (any variation of these):
    • "the text mentions / states / provides / says / explicitly states"
    • "in activity X.X", "in section X.X", "in exercise X.X"
    • "table X.X shows / clearly states / indicates"
    • "the table shows", "consult the table", "refer to the table"
    • "the passage states", "according to the passage"
    • "as mentioned in the chapter", "the chapter states"
    • "as given in the text", "as described above", "the above text"
    • Any numbered activity, figure, table, or exercise reference (e.g. "Activity 9.2", "Fig. 3.1")

  INSTEAD — write the fact, concept, or data directly into the question or explanation:
    ✗ "The text states that photosynthesis occurs in the chloroplast."
    ✓ "Photosynthesis occurs in the chloroplast."
    ✗ "Table 9.1 shows the boiling points of these liquids."
    ✓ "The boiling points of these liquids are: water — 100°C, ethanol — 78°C."
    ✗ "As mentioned in Activity 9.2, the solution turns blue."
    ✓ "When starch is present, iodine solution turns blue."
"""

_HINT_RULES = """
Hint quality rules (ALL must be satisfied — no exceptions):
  1. Must guide the student's thinking process, not give away the answer.
  2. Must NEVER contain any word from the correct answer option text (after removing stop words).
  3. Must NEVER eliminate wrong options (do not hint "it is NOT A or B").
  4. Should reference a concept, principle, or process rather than the answer itself.
  5. Must be FULLY SELF-CONTAINED — strictly forbidden to reference:
       • Theorem, Lemma, Corollary, Property, or Rule by number (e.g. "Theorem 6.6", "Rule 2.1")
       • Textbook pages, chapters, sections, figures, tables, or activities by number
       • Phrases like "Refer to...", "See page...", "As stated in...", "According to Theorem...",
         "the text says...", "as mentioned in the chapter..."
     Instead, state the underlying principle or key idea directly in the hint text.
     A student with no textbook must be able to use this hint.
"""

_EXPLANATION_RULES = """
Explanation quality rules (ALL must be satisfied — no exceptions):
  1. Must clearly justify WHY the correct answer is right and WHY each distractor is wrong.
  2. Must be FULLY SELF-CONTAINED — strictly forbidden to reference:
       • Theorem, Lemma, Corollary, Property, or Rule by number (e.g. "Theorem 6.6")
       • Textbook pages, chapters, sections, figures, tables, or activities by number
       • Phrases like "Refer to...", "See page...", "As stated in...", "According to Theorem...",
         "the text states...", "as shown in the table...", "Activity X.X shows..."
     Instead, explain the reasoning and logic inline — embed any needed facts directly.
  3. Must use the same mobile-safe formatting rules as all other fields (no LaTeX).
"""

_SELF_VERIFICATION = """
Self-verification (do this before submitting):
  For each question, verify:
  ✓ Every claim is supported by the provided chapter text
  ✓ question_text contains NO reference to the source material (no "the text", "activity X", "table X")
  ✓ hint contains NO reference to the source material, theorem numbers, or page references
  ✓ explanation contains NO reference to the source material, theorem numbers, or page references
  ✓ Hint passes all hint rules above (no answer tokens)
  ✓ All distractors are plausible but clearly wrong to a knowledgeable student
  ✓ Bloom's category matches the stem pattern for the difficulty level
  ✓ No two questions test the same specific fact or concept
  If any check fails — fix the question before submitting.
"""

_ANSWER_POSITION_RULES = """
Answer position rules:
  • Distribute the correct answer across positions A, B, C, D evenly across all questions.
  • Do NOT place the correct answer in the same position for more than 2 consecutive questions.
  • Vary the position intentionally — the pattern should not be predictable.
"""

_COMPETENCY_DIFFICULTY_RULES = """
Difficulty rules for Competency Assessment (STRICT — no exceptions):
  • Generate ONLY difficulty levels 4 and 5. Levels 1, 2, and 3 are FORBIDDEN.
  • Level 4 — ANALYZE: break down, compare, contrast relationships within the chapter.
    Stems: "What is the relationship between...", "Compare...", "Why is X different from Y..."
  • Level 5 — EVALUATE / CREATE: judge, design, propose, justify using chapter knowledge.
    Stems: "Which solution is most appropriate...", "Justify...", "What would you recommend...", "Design..."
  • Every question must require the student to go beyond recall — analysis or evaluation only.
  • Do NOT generate any question that can be answered by simple recall or recognition.
"""

_QUESTION_TYPE_RULES = """
Question type rules — choose the BEST type for each question based on the content:
  mcq_single   : Exactly ONE correct answer. Use for factual, conceptual, and analytical questions.
                 Provide exactly 4 options. Mark exactly one is_correct=true.

  mcq_multiple : TWO OR MORE correct answers. Use when the content has multiple valid aspects.
                 Provide exactly 4 options. Mark 2 or more as is_correct=true (can be all 4).
                 Question text MUST include "Select all that apply."

  rearrange    : Student arranges items in the correct sequence or order.
                 Use for processes, steps, timelines, or logical sequences.
                 Provide 4–6 items. ALL options must have is_correct=true.
                 Each option MUST have correct_order (1-based integer for its position in correct sequence).
                 Return options in SCRAMBLED order — NOT in the correct sequence.
                 Question text MUST ask the student to arrange/order the items.
"""

_DIFFICULTY_DISTRIBUTION_RULES = """
Difficulty distribution rules:
  • Assess the topic's breadth and cognitive depth before deciding the distribution.
  • A narrow, factual topic (e.g. definitions, dates) should lean toward levels 1–2.
  • A conceptual or applied topic should lean toward levels 3–4.
  • A complex, evaluative topic (e.g. analysis, design) should include levels 4–5.
  • For any n, choose a distribution that honestly reflects what the topic supports — do not force an even spread if the topic does not warrant it.
  • Avoid clustering all questions at one level unless the topic is genuinely limited in scope.
"""

_IMAGE_RULES = """
Image/diagram rules (only when a visual genuinely helps the student understand the question):
  • Set the image_prompt field to a clear 2–3 sentence description of what to show.
  • Works for ANY subject — geometry, physics, biology, chemistry, history maps, etc.
  • The image must show the PROBLEM SETUP only — NEVER the answer.
  • CRITICAL — the description must NOT imply or include the answer:
      - If the question asks "what is angle X?", describe the shape with angle X marked as "?".
      - If the question asks about a process outcome, describe only the initial state.
      - If the question asks to identify a structure, show the structure with the target part unlabelled.
  • Include all relevant labels, measurements, and given values that are part of the problem.
  • Example: "A bar magnet with S on the left and N on the right. Magnetic field lines curve from
    N to S outside the magnet. The direction of field lines inside the magnet is marked with '?'."
  • Only add an image when it genuinely aids comprehension — do not add one for purely text-based questions.
"""


# ── Shared per-question JSON schema — single source of truth for generate + modify ──────────────

QUESTION_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "question_text": {"type": "string"},
        "question_type": {
            "type": "string",
            "enum": ["mcq_single", "mcq_multiple", "rearrange"],
        },
        "options": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "option_text": {"type": "string"},
                    "is_correct": {"type": "boolean"},
                    "correct_order": {
                        "type": "integer",
                        "description": "1-based position in correct sequence. Required for rearrange type.",
                    },
                },
                "required": ["option_text", "is_correct"],
            },
            "minItems": 4,
            "maxItems": 6,
        },
        "hint": {"type": "string"},
        "explanation": {"type": "string"},
        "difficulty_level": {"type": "integer", "minimum": 1, "maximum": 5},
        "image_prompt": {
            "type": "string",
            "description": (
                "Optional. A concise 2-3 sentence description of a diagram to show with this question. "
                "Describe ONLY the problem setup — never include or imply the answer. "
                "Works for any subject (geometry, biology, chemistry, physics, etc.)."
            ),
        },
    },
    "required": [
        "question_text", "question_type", "options",
        "hint", "explanation", "difficulty_level",
    ],
}

# Task descriptions for each predefined modification type.
# CUSTOM is intentionally absent — it uses the user's instruction directly.
MODIFY_TYPE_TASKS = {
    "REPHRASE": "Rephrase the question text and options using different wording. Keep the same correct answer.",
    "INCREASE_DIFFICULTY": "Increase the difficulty level by one step. Deepen the cognitive demand (e.g. from recall to application). Update difficulty_level accordingly.",
    "DECREASE_DIFFICULTY": "Decrease the difficulty level by one step. Simplify the cognitive demand. Update difficulty_level accordingly.",
    "CHANGE_OPTIONS": "Rewrite the distractor options to make them more plausible and challenging, without changing the correct answer.",
    "REGENERATE": "Generate a completely new question on the same topic and difficulty level.",
}


def build_system_prompt(grade_level: int, subject: str = "", chapter: str = "", board: str = "CBSE") -> str:
    if grade_level <= 5:
        grade_note = "Use concrete, simple language. Short sentences. No jargon."
    elif grade_level <= 8:
        grade_note = "Use moderate academic language. Some subject-specific vocabulary is fine."
    elif grade_level <= 10:
        grade_note = "Use academic language appropriate for secondary school students."
    else:
        grade_note = "Use college-preparatory academic language with full subject terminology."

    parts = [f"Board: {board}", f"Grade: {grade_level}"]
    if subject:
        parts.append(f"Subject: {subject}")
    if chapter:
        parts.append(f"Chapter: {chapter}")
    context_line = " | ".join(parts)

    return f"""You are an expert educational assessment designer specializing in Bloom's Taxonomy and Item Response Theory (IRT).

You create and refine high-quality multiple-choice questions (MCQs) for educational content.

Curriculum context: {context_line}
Grade calibration: {grade_note}

{_MOBILE_FORMAT_RULES}

{_SOURCE_INDEPENDENCE_RULES}

{_BLOOM_MAPPING}

{_DIFFICULTY_DISTRIBUTION_RULES}

{_QUESTION_TYPE_RULES}

{_ANSWER_POSITION_RULES}

{_HINT_RULES}

{_EXPLANATION_RULES}

{_IMAGE_RULES}

{_SELF_VERIFICATION}

Output format: return your response as structured JSON matching the provided schema. No markdown, no code fences, no plain text."""


def build_user_prompt(
    chapter: str,
    num_questions: int,
    context_text: str,
    topic: str = "",
    existing_question_stems: list[str] | None = None,
) -> str:
    dedup_section = ""
    if existing_question_stems:
        stems_list = "\n".join(f"  - {s}" for s in existing_question_stems)
        dedup_section = f"\nAlready-asked questions (do NOT repeat these topics):\n{stems_list}\n"

    topic_line = f"\nTopic (sub-topic focus): {topic}" if topic else ""

    return f"""Chapter: {chapter}{topic_line}
Number of questions to generate: {num_questions}
{dedup_section}
Chapter content (use ONLY the information below to create questions):
---
{context_text}
---

Generate exactly {num_questions} MCQ question(s) focused strictly on the topic "{topic}" using only the content provided above. Mix question types (mcq_single, mcq_multiple, rearrange) where appropriate. Where a diagram genuinely aids a question, set image_prompt to a clear description of what to show. Decide the difficulty distribution based on the topic's nature before generating. Return all questions as a JSON array."""


def build_prereq_system_prompt(grade_level: int, subject: str = "", chapter: str = "", board: str = "CBSE") -> str:
    prereq_grade = max(1, grade_level - 1)

    if prereq_grade <= 5:
        grade_note = "Use concrete, simple language. Short sentences. No jargon."
    elif prereq_grade <= 8:
        grade_note = "Use moderate academic language. Some subject-specific vocabulary is fine."
    elif prereq_grade <= 10:
        grade_note = "Use academic language appropriate for secondary school students."
    else:
        grade_note = "Use college-preparatory academic language with full subject terminology."

    parts = [f"Board: {board}", f"Current Grade: {grade_level}", f"Prerequisite Grade: {prereq_grade}"]
    if subject:
        parts.append(f"Subject: {subject}")
    if chapter:
        parts.append(f"Chapter: {chapter}")
    context_line = " | ".join(parts)

    return f"""You are an expert educational assessment designer specialising in prerequisite and foundational knowledge assessment.

Your task is to generate MCQs that check whether a Class {grade_level} student has the foundational knowledge from Class {prereq_grade} needed to successfully study this chapter.

Curriculum context: {context_line}
Grade calibration (target prerequisite level — Class {prereq_grade}): {grade_note}

You do NOT have a textbook excerpt for this task. Use your knowledge of the {board} curriculum to identify:
  • Concepts and skills taught at Class {prereq_grade} level for this chapter's topic
  • Foundational understanding a student must have before advancing to Class {grade_level} content
  • Common prerequisite gaps that cause students to struggle with this chapter

Questions must be pitched at Class {prereq_grade} difficulty — not the current Class {grade_level} level.

{_MOBILE_FORMAT_RULES}

{_SOURCE_INDEPENDENCE_RULES}

{_BLOOM_MAPPING}

{_DIFFICULTY_DISTRIBUTION_RULES}

{_QUESTION_TYPE_RULES}

{_ANSWER_POSITION_RULES}

{_HINT_RULES}

{_EXPLANATION_RULES}

{_IMAGE_RULES}

{_SELF_VERIFICATION}

Output format: return your response as structured JSON matching the provided schema. No markdown, no code fences, no plain text."""


def build_prereq_user_prompt(
    chapter: str,
    num_questions: int,
    board: str = "CBSE",
    grade_level: int = 8,
    existing_question_stems: list[str] | None = None,
) -> str:
    prereq_grade = max(1, grade_level - 1)

    dedup_section = ""
    if existing_question_stems:
        stems_list = "\n".join(f"  - {s}" for s in existing_question_stems)
        dedup_section = f"\nAlready-asked questions (do NOT repeat these topics):\n{stems_list}\n"

    return f"""Chapter: {chapter}
Topic: Previous Knowledge Testing
Current Grade: {grade_level} | Prerequisite grade to assess: {prereq_grade}
Number of questions to generate: {num_questions}
{dedup_section}
Generate exactly {num_questions} MCQ question(s) that test the foundational knowledge a Class {grade_level} student should already have from Class {prereq_grade} before studying "{chapter}". Use your knowledge of the {board} curriculum to determine what prerequisite concepts are expected at Class {prereq_grade} level. Questions must be pitched at Class {prereq_grade} difficulty — not the current Class {grade_level} level. Mix question types (mcq_single, mcq_multiple, rearrange) where appropriate. Where a diagram genuinely aids a question, set image_prompt to a clear description of what to show. Return all questions as a JSON array."""


def build_competency_system_prompt(grade_level: int, subject: str = "", chapter: str = "", board: str = "CBSE") -> str:
    if grade_level <= 5:
        grade_note = "Use concrete, simple language. Short sentences. No jargon."
    elif grade_level <= 8:
        grade_note = "Use moderate academic language. Some subject-specific vocabulary is fine."
    elif grade_level <= 10:
        grade_note = "Use academic language appropriate for secondary school students."
    else:
        grade_note = "Use college-preparatory academic language with full subject terminology."

    parts = [f"Board: {board}", f"Grade: {grade_level}"]
    if subject:
        parts.append(f"Subject: {subject}")
    if chapter:
        parts.append(f"Chapter: {chapter}")
    context_line = " | ".join(parts)

    return f"""You are an expert educational assessment designer specialising in higher-order thinking and competency-based assessment.

Your task is to generate advanced MCQs that assess a student's deep understanding and analytical ability across the ENTIRE chapter — not recall of specific facts.

Curriculum context: {context_line}
Grade calibration: {grade_note}

{_MOBILE_FORMAT_RULES}

{_SOURCE_INDEPENDENCE_RULES}

{_BLOOM_MAPPING}

{_COMPETENCY_DIFFICULTY_RULES}

{_QUESTION_TYPE_RULES}

{_ANSWER_POSITION_RULES}

{_HINT_RULES}

{_EXPLANATION_RULES}

{_IMAGE_RULES}

{_SELF_VERIFICATION}

Output format: return your response as structured JSON matching the provided schema. No markdown, no code fences, no plain text."""


def build_competency_user_prompt(
    chapter: str,
    num_questions: int,
    context_text: str,
    existing_question_stems: list[str] | None = None,
) -> str:
    dedup_section = ""
    if existing_question_stems:
        stems_list = "\n".join(f"  - {s}" for s in existing_question_stems)
        dedup_section = f"\nAlready-asked questions (do NOT repeat these topics):\n{stems_list}\n"

    return f"""Chapter: {chapter}
Assessment Type: Competency Assessment (whole chapter, levels 4–5 only)
Number of questions to generate: {num_questions}
{dedup_section}
Chapter content (use ONLY the information below to create questions):
---
{context_text}
---

Generate exactly {num_questions} higher-order MCQ question(s) covering the FULL breadth of this chapter. Every question must be difficulty level 4 (Analyze) or 5 (Evaluate/Create) — do NOT generate any level 1, 2, or 3 questions. Questions must require the student to analyse relationships, evaluate arguments, or apply concepts in novel ways — not recall facts. Mix question types (mcq_single, mcq_multiple, rearrange) where appropriate. Return all questions as a JSON array."""


def build_batch_fix_prompt(items: list[dict]) -> str:
    """
    Build a prompt to fix all rejected questions in a single LLM call.

    items: list of {index, fix_instruction, question}
    """
    import json
    return (
        "You will receive a list of MCQ questions that each have a specific issue. "
        "Fix each question according to its individual fix_instruction. "
        "Apply ONLY the specified fix — do not change anything else. "
        "Return a JSON object with a 'questions' array containing the fixed questions "
        "in the same order as the input (same length).\n\n"
        f"Questions to fix:\n{json.dumps(items, indent=2)}"
    )


# Modify prompts only need enough context to verify one question's correctness —
# not the entire chapter. Keep this small to avoid Gemini JSON truncation with
# guided generation (response_schema + large input causes incomplete output).
_MODIFY_CONTEXT_LIMIT = 3000


def build_modify_user_prompt(
    question: dict,
    modification_type: str,
    instruction: str,
    context_text: str = "",
) -> str:
    import json

    # CUSTOM: use the user's instruction directly.
    # All predefined types: use the task description from MODIFY_TYPE_TASKS.
    if modification_type == "CUSTOM":
        task = instruction or "Improve the question quality."
    else:
        task = MODIFY_TYPE_TASKS.get(modification_type, modification_type)

    context_section = ""
    if context_text:
        truncated = context_text[:_MODIFY_CONTEXT_LIMIT]
        if len(context_text) > _MODIFY_CONTEXT_LIMIT:
            truncated += "\n[...content truncated for brevity...]"
        context_section = (
            f"\n\nChapter content (verify correctness against this):\n---\n{truncated}\n---"
        )

    return (
        f"Modification task: {task}\n\n"
        f"Original question:\n{json.dumps(question, indent=2)}"
        f"{context_section}\n\n"
        "Apply the modification and return the updated question as a JSON object."
    )
