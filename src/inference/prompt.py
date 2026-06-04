"""
Prompt loading, hashing, message construction, and response parsing.

Design decisions:
- Versioned prompt file: prompts/classify_v1.txt is the prompt artifact.
  Version is in the filename, so cache keys naturally invalidate when you
  bump the prompt version without code changes.
- Prompt hash in cache key: if you edit the prompt file without changing
  the version string, the hash still changes and old cache entries are
  bypassed. Belt and suspenders.
- Parse failure → REPAIR_PROMPT: one correction attempt before giving up.
  Some models (especially smaller ones) occasionally wrap JSON in fences
  despite being told not to. The repair prompt is a gentle course correction.
  If the repair fails too, we record parse_error and move on.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import re

from src.models import Issue, LabelEnum

PROMPT_DIR = pathlib.Path("prompts")
PROMPT_PATH = PROMPT_DIR / "classify_v1.txt"
PROMPT_VERSION = "v1"

# Repair prompt sent as a follow-up if the model produces invalid JSON.
# Intentionally terse — we want structured output, not explanation.
REPAIR_PROMPT = (
    "Your previous response was not valid JSON. "
    "Respond with ONLY this exact JSON schema, no other text:\n"
    '{"reasoning": "brief explanation", "label": "one of: '
    "bug, enhancement, question, documentation, security, other\"}"
)

_PROMPT_CACHE: str | None = None


def load_prompt() -> str:
    """Load the versioned prompt from disk. Cached in module-level variable."""
    global _PROMPT_CACHE
    if _PROMPT_CACHE is None:
        if not PROMPT_PATH.exists():
            raise FileNotFoundError(
                f"Prompt file not found: {PROMPT_PATH}. "
                f"Expected at {PROMPT_PATH.absolute()}"
            )
        _PROMPT_CACHE = PROMPT_PATH.read_text(encoding="utf-8")
    return _PROMPT_CACHE


def get_prompt_hash() -> str:
    """
    SHA-256[:16] of prompt file content.

    Used in cache keys and RunManifest. If you edit the prompt without changing
    the version, the hash changes and old cache entries are bypassed.
    Returns only the first 16 hex chars — enough for collision avoidance while
    keeping filenames readable.
    """
    return hashlib.sha256(load_prompt().encode()).hexdigest()[:16]


def build_messages(issue: Issue) -> list[dict[str, str]]:
    """
    Returns OpenAI-format messages list for classifying one issue.

    The system message is the full prompt (task framing + label definitions + examples).
    The user message is just the issue content — clean separation of instructions from data.
    Body is truncated to 2000 chars (may already be truncated at ingestion time, but
    we guard here too in case an Issue is constructed elsewhere).
    """
    body = (issue.body or "(no description provided)")[:2000]
    user_content = f"Issue #{issue.number}: {issue.title}\n\n{body}"
    return [
        {"role": "system", "content": load_prompt()},
        {"role": "user", "content": user_content},
    ]


class ParseError(Exception):
    """
    Raised when the model response cannot be parsed as valid JSON with a valid label.

    Attributes:
        raw_text: The unparseable response text (logged for debugging)
        reason: Human-readable explanation of why parsing failed
    """

    def __init__(self, reason: str, raw_text: str) -> None:
        super().__init__(reason)
        self.reason = reason
        self.raw_text = raw_text


# Regex to strip markdown code fences (```json ... ``` or ``` ... ```)
_FENCE_RE = re.compile(r"^```(?:json)?\s*\n?(.*?)\n?```$", re.DOTALL)

# Regex to strip DeepSeek/reasoning-model chain-of-thought blocks.
# Reasoning models like deepseek-r1 emit <think>...</think> before the JSON answer.
# We strip these so the JSON parser can find the actual output.
_COT_RE = re.compile(r"<think>.*?</think>\s*", re.DOTALL | re.IGNORECASE)


def parse_response(raw_text: str) -> tuple[LabelEnum, str]:
    """
    Parses model response to extract (label, reasoning).

    Handles:
    - Clean JSON: {"reasoning": "...", "label": "bug"}
    - JSON wrapped in markdown code fences (strips them first)
    - Case-insensitive label matching
    - Whitespace around label values

    Raises ParseError if:
    - Not valid JSON (after fence stripping)
    - Missing "label" key
    - Label not in the 6-class enum (after normalization)

    Design: We strip fences but raise on anything else. We don't silently coerce
    bad output — bad output is a signal about model reliability that the error
    rate metrics should capture. The repair prompt handles the one-shot recovery.
    """
    text = raw_text.strip()

    # Strip reasoning model chain-of-thought blocks (<think>...</think>).
    # DeepSeek-R1 and similar reasoning models emit these before the JSON answer.
    # We remove them so the JSON parser finds the actual output.
    text = _COT_RE.sub("", text).strip()

    # Strip markdown code fences if present
    fence_match = _FENCE_RE.match(text)
    if fence_match:
        text = fence_match.group(1).strip()

    # Parse JSON
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ParseError(
            reason=f"Not valid JSON: {exc}",
            raw_text=raw_text,
        ) from exc

    if not isinstance(data, dict):
        raise ParseError(
            reason=f"Expected JSON object, got {type(data).__name__}",
            raw_text=raw_text,
        )

    # Extract and validate label
    raw_label = data.get("label")
    if raw_label is None:
        raise ParseError(
            reason=f"Missing 'label' key. Keys found: {list(data.keys())}",
            raw_text=raw_text,
        )

    normalized_label = str(raw_label).strip().lower()

    try:
        label = LabelEnum(normalized_label)
    except ValueError:
        valid = [e.value for e in LabelEnum]
        raise ParseError(
            reason=f"Unknown label '{raw_label}'. Valid values: {valid}",
            raw_text=raw_text,
        )

    reasoning = str(data.get("reasoning", "")).strip() or None

    return label, reasoning or ""
