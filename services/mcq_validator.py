"""
MCQ validation: schema checks, dedup, and hint quality verification.
"""
import hashlib
import re

_STOP_WORDS = {
    'a', 'an', 'the', 'is', 'are', 'was', 'were', 'be', 'been', 'being',
    'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'could',
    'should', 'may', 'might', 'shall', 'can', 'to', 'of', 'in', 'on', 'at',
    'by', 'for', 'with', 'as', 'it', 'its', 'this', 'that', 'and', 'or',
    'but', 'if', 'then', 'so', 'not', 'no', 'nor',
}

# Matches hints that are bare textbook citations with no conceptual content.
_TEXTBOOK_REF_RE = re.compile(
    r"""
    \brefer\s+to\b                                          # "Refer to ..."
    | \b(theorem|lemma|corollary|axiom|example|exercise
         |section|chapter|figure|table|page|formula
         |property|rule|definition)\s+\d+                  # "Theorem 6.6", "Example 3"
    | \bsee\s+(theorem|lemma|example|section|chapter
               |figure|table|page|exercise)\b              # "See Theorem", "See page"
    | \bcheck\s+(the\s+)?(textbook|book|notes
                  |section|chapter)\b                      # "Check the textbook"
    """,
    re.IGNORECASE | re.VERBOSE,
)


def hint_is_textbook_reference(hint: str) -> bool:
    """Return True if the hint is a bare textbook/theorem citation with no conceptual content."""
    return bool(_TEXTBOOK_REF_RE.search(hint))



def normalize_text(text: str) -> str:
    return re.sub(r'\s+', ' ', text.strip().lower())


def question_hash(question_text: str) -> str:
    return hashlib.sha256(normalize_text(question_text).encode()).hexdigest()


def _tokenize(text: str) -> set[str]:
    words = re.findall(r"[a-z']+", text.lower())
    return {w for w in words if w not in _STOP_WORDS and len(w) > 2}


def hint_leaks_answer(hint: str, correct_option_texts: list[str]) -> bool:
    """
    Returns True only when the hint meaningfully reveals the correct answer.

    Rules:
    - Single-word overlap is NOT enough — requires 2+ overlapping content words,
      OR a single overlap where that word is the dominant/sole noun in the answer
      (i.e. the answer is 1–2 words long and the overlap is its core word).
    - Short generic words (len <= 3) are ignored even if not in stop words.
    - This avoids rejecting hints that merely share topic-domain vocabulary.
    """
    hint_tokens = _tokenize(hint)
    # Drop very short tokens (e.g. "law", "ion") — too common in CBSE content
    hint_tokens = {w for w in hint_tokens if len(w) > 3}

    for option_text in correct_option_texts:
        answer_tokens = _tokenize(option_text)
        answer_tokens = {w for w in answer_tokens if len(w) > 3}
        overlap = hint_tokens & answer_tokens

        if not overlap:
            continue

        # Short answer (1-2 meaningful words) — even one overlap is a leak
        if len(answer_tokens) <= 2 and overlap:
            return True

        # Longer answer — require 2+ overlapping words to flag as a leak
        if len(overlap) >= 2:
            return True

    return False


def build_fix_instruction(reason: str, q: dict) -> str:
    """Return a specific, targeted fix instruction for any rejection reason."""
    options = q.get("options") or []
    n = len(options)

    if reason == "Hint reveals the correct answer":
        return (
            "Rewrite only the hint field so it does not contain any word from the correct answer. "
            "Guide the student's thinking without naming or implying the correct option. "
            "Keep everything else identical."
        )
    if reason == "Hint is a bare textbook reference":
        return (
            "Rewrite only the hint field. The current hint is a bare textbook citation "
            "(e.g. 'Refer to Theorem 6.6') which is useless without the book. "
            "Replace it with a self-contained hint that names and briefly explains the "
            "relevant concept or principle in plain language, without giving away the answer. "
            "Keep everything else identical."
        )
    if reason == "No correct answer marked":
        return "Mark exactly one option as is_correct=true. Do not change any option text."
    if reason in ("difficulty_level must be 1–5", "difficulty_level must be an integer"):
        return "Set difficulty_level to 3. Keep everything else identical."
    if reason == "MCQ questions must have exactly 4 options":
        if n < 4:
            return (
                f"This question has {n} option(s). Add {4 - n} more distinct, plausible distractor "
                f"option(s) with is_correct=false to bring the total to exactly 4."
            )
        return "Remove extra options so the question has exactly 4 options total. Keep the correct answer."
    if reason == "options must be a list with at least 2 items":
        return "Add 3 more distinct, plausible distractor options with is_correct=false so the total is 4."
    if reason == "Each option must have option_text":
        return "Add a non-empty option_text string to every option that is missing it."
    if reason == "Each option must have is_correct":
        return "Add is_correct (true or false) to every option that is missing it."
    if reason == "mcq_single must have exactly one correct answer":
        return "Set is_correct=true on exactly one option and is_correct=false on all others."
    if "rearrange" in reason.lower() or "correct_order" in reason.lower():
        return (
            "Fix the rearrange question: all options must have is_correct=true and a unique "
            "correct_order integer (1-based, contiguous from 1 to N)."
        )
    if reason == "Missing required field: question_text":
        return "Add a clear, complete question_text string relevant to the options and topic."
    if reason == "Duplicate question (hash match)":
        return (
            "Rephrase the question_text and all options using completely different wording "
            "while keeping the same topic, difficulty, and correct answer."
        )
    return f"Fix the following issue with this question: {reason}"


def validate_single(q: dict, seen_hashes: set[str]) -> str | None:
    """
    Validate a single question against seen_hashes.
    Returns rejection reason string, or None if valid.
    Mutates seen_hashes on success — add the hash after calling this.
    """
    return _check_question(q, seen_hashes)

def _check_question(q: dict, seen_hashes: set[str]) -> str | None:
    # Required fields
    if not q.get('question_text'):
        return "Missing required field: question_text"

    question_type = q.get('question_type', 'mcq_single')
    options = q.get('options', [])

    if not isinstance(options, list) or len(options) < 2:
        return "options must be a list with at least 2 items"

    # Each option needs option_text and is_correct
    for opt in options:
        if not isinstance(opt, dict):
            return "Each option must be an object"
        if 'option_text' not in opt:
            return "Each option must have option_text"
        if 'is_correct' not in opt:
            return "Each option must have is_correct"

    if question_type == 'rearrange':
        # For rearrange: all options must be correct and have correct_order
        for opt in options:
            if not opt.get('is_correct'):
                return "All rearrange options must have is_correct=true"
            if 'correct_order' not in opt or not isinstance(opt.get('correct_order'), int):
                return "All rearrange options must have correct_order (integer)"
        n = len(options)
        if not (4 <= n <= 6):
            return "Rearrange questions must have 4–6 options"
        orders = sorted(opt['correct_order'] for opt in options)
        if orders != list(range(1, n + 1)):
            return "Rearrange correct_order values must be contiguous from 1 to N"
    else:
        # mcq_single / mcq_multiple: exactly 4 options, at least one correct
        if len(options) != 4:
            return "MCQ questions must have exactly 4 options"
        correct_options = [o for o in options if o.get('is_correct')]
        if not correct_options:
            return "No correct answer marked"
        if question_type == 'mcq_single' and len(correct_options) > 1:
            return "mcq_single must have exactly one correct answer"

    # Difficulty range
    try:
        diff = int(q.get('difficulty_level', 0))
    except (TypeError, ValueError):
        return "difficulty_level must be an integer"
    if not 1 <= diff <= 5:
        return "difficulty_level must be 1–5"

    # Dedup
    h = question_hash(q.get('question_text', ''))
    if h in seen_hashes:
        return "Duplicate question (hash match)"

    # Hint quality (skip for rearrange — all options are correct)
    if question_type != 'rearrange':
        hint = q.get('hint', '')
        if hint:
            correct_options = [o for o in options if o.get('is_correct')]
            correct_texts = [o.get('option_text', '') for o in correct_options]
            if hint_leaks_answer(hint, correct_texts):
                return "Hint reveals the correct answer"
            if hint_is_textbook_reference(hint):
                return "Hint is a bare textbook reference"

    return None

def validate_questions(
    raw_questions: list[dict],
    seen_hashes: set[str] | None = None,
) -> tuple[list[dict], list[dict]]:
    seen_hashes = seen_hashes or set()
    valid: list[dict] = []
    rejected: list[dict] = []

    for q in raw_questions:
        reason = _check_question(q, seen_hashes)
        if reason:
            q['rejection_reason'] = reason
            rejected.append(q)
        else:
            seen_hashes.add(question_hash(q.get('question_text', '')))
            valid.append(q)

    return valid, rejected
