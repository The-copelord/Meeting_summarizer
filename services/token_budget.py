"""
services/token_budget.py
Token budget management — prevents overloading LLM context windows
by enforcing per-call caps, adaptive truncation, and batch sizing.
"""

import logging
import re
from typing import List

logger = logging.getLogger(__name__)

# ── Token estimation ──────────────────────────────────────────────────────────
# Rough rule: 1 token ≈ 4 chars for English / Latin script
# We use a conservative ratio (3.5) to avoid under-counting.
CHARS_PER_TOKEN = 3.5


def estimate_tokens(text: str) -> int:
    """Estimate token count from character length."""
    return max(1, int(len(text) / CHARS_PER_TOKEN))


# ── Per-mode token budgets ────────────────────────────────────────────────────
# Input context limits — stay well under provider limits
# (Groq llama-3.3-70b: 32k context; Anthropic Haiku: 200k; OpenAI gpt-4o: 128k)
# Conservative shared limit used so the same budget works on Groq.

BUDGET = {
    # input chars allowed per call
    "chunk_input_chars":   8_000,   # ~2300 tokens per transcript chunk
    "mode_a_context_chars": 3_000,  # prior state capsule
    "mode_b_input_chars":  20_000,  # briefs fed into rollup
    "mode_c_input_chars":  24_000,  # rollup briefs fed into master brief
    "s0_input_chars":      20_000,  # overall summary — fed from tier-appropriate briefs
    # max_tokens for LLM response
    "mode_a_max_tokens":   1_200,
    "mode_b_max_tokens":   2_000,
    "mode_c_max_tokens":   4_000,
    "s0_max_tokens":         600,
    "chunk_summary_max_tokens": 500,
    "final_summary_max_tokens": 1_200,
}


def truncate(text: str, max_chars: int, label: str = "") -> str:
    """Hard-truncate text to max_chars, appending a marker."""
    if len(text) <= max_chars:
        return text
    truncated = text[:max_chars]
    # Try to cut at a sentence boundary
    last_period = truncated.rfind(". ", max_chars - 400, max_chars)
    if last_period > 0:
        truncated = truncated[:last_period + 1]
    suffix = f"\n[... truncated for token budget — {label} ...]" if label else "\n[... truncated ...]"
    logger.debug(f"Truncated {label}: {len(text)} → {len(truncated)} chars")
    return truncated + suffix


def truncate_capsule(capsule: str) -> str:
    """Trim a state capsule to the allowed context chars."""
    return truncate(capsule, BUDGET["mode_a_context_chars"], "state capsule")


def truncate_chunk(transcript: str) -> str:
    """Trim a transcript chunk to the allowed input chars."""
    return truncate(transcript, BUDGET["chunk_input_chars"], "transcript chunk")


def fit_rollup_briefs(briefs: List[str], max_chars: int = 0) -> str:
    """
    Join briefs with separators. Never truncates content — all briefs are
    preserved in full. The max_chars parameter is kept for call-site
    compatibility but is no longer used to trim.
    If the content exceeds a context window, the caller (aria.py) is
    responsible for running an extra compression pass instead of cutting.
    """
    separator = "\n---\n"
    combined = separator.join(briefs)
    if max_chars and len(combined) > max_chars:
        logger.warning(
            f"[TokenBudget] Combined briefs ({len(combined):,} chars) exceed "
            f"limit ({max_chars:,} chars) — caller should compress further, not truncate"
        )
    return combined


def adaptive_chunk_size(total_chunks: int) -> int:
    """
    Return the number of chunks to roll up per Mode B batch.
    Larger meetings get larger batches to reduce total LLM calls.
    """
    if total_chunks <= 6:
        return total_chunks   # skip Mode B entirely
    if total_chunks <= 15:
        return 4
    if total_chunks <= 30:
        return 5
    return 6   # very long meetings


def log_budget_stats(stage: str, input_chars: int, max_chars: int):
    pct = (input_chars / max_chars * 100) if max_chars else 0
    level = logging.WARNING if pct > 90 else logging.DEBUG
    logger.log(level, f"[TokenBudget] {stage}: {input_chars:,} chars ({pct:.0f}% of {max_chars:,} limit)")
