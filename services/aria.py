"""
services/aria.py
ARIA — Adaptive Recording Intelligence Analyst
Implements the three-mode chunked intelligence pipeline:
  Mode A: Chunk Brief (per chunk, sequential — needs prior state capsule)
  Mode B: Rollup Brief (every 4-5 chunks)
  Mode C: Master Brief (final deliverable)
"""

import logging
import re
import json
from typing import Optional

logger = logging.getLogger(__name__)

from services.token_budget import (
    BUDGET,
    truncate_chunk,
    truncate_capsule,
    fit_rollup_briefs,
    adaptive_chunk_size,
    log_budget_stats,
)

ROLLUP_EVERY = 5   # default — overridden dynamically by adaptive_chunk_size


# ── ARIA System Prompt (shared across all modes) ──────────────────────────────

ARIA_SYSTEM = """You are ARIA — Adaptive Recording Intelligence Analyst.

You are processing a long meeting, lecture, or session that has been
split into sequential text chunks for pipeline processing. Your
persona, analytical depth, and output standard are identical across
every chunk, rollup, and final brief.

CORE RULES:
  — Never hallucinate. If unclear, write [UNCLEAR — possible: X].
  — Never be generic. Every line is grounded in what was actually said.
  — Never lose a name, decision, task, or open question between modes.
  — Translate non-English content inline. Preserve the original.
  — Use [INFERRED] when drawing a conclusion not explicitly stated.
  — Tone: senior analyst. Precise. No fluff. No bullet-point soup."""


# ── MODE A: Chunk Brief ───────────────────────────────────────────────────────

MODE_A_PROMPT = """You are running MODE A — CHUNK BRIEF.

CHUNK INFO:
  Chunk       : {chunk_n} of {total_chunks}
  Rollup Batch: Batch {batch_n} (chunks {batch_start}–{batch_end})

PRIOR STATE CAPSULE (context from previous chunk):
{prior_state_capsule}

CURRENT CHUNK TRANSCRIPT:
<chunk>
{transcript}
</chunk>

Produce the following output exactly:

▌CHUNK {chunk_n} BRIEF

CHUNK IDENTITY
  Chunk       : {chunk_n} of {total_chunks} | Batch {batch_n}
  Speakers    : [Names or SPEAKER_N tags active in this chunk]
  Language(s) : [EN / HI / other — detect from content]
  Tone        : [Focused / Tense / Exploratory / Winding down / etc.]
  Thread      : [FRESH START / CONTINUES: topic name / MID-THOUGHT]

CHUNK SUMMARY
  [4–6 sentences of prose. No bullets. Grounded in this chunk only.
  Open with what started the chunk. Close with how it ended.
  Use speaker names where known. Translate non-English inline.]

STRUCTURED EXTRACTIONS

  TOPICS
    • [Topic] — [★ NEW / ↩ CONTINUED / ✓ CLOSED] — [1-line description]

  DECISIONS
    D-{chunk_n}.1: [What] — [Who] — [FIRM / TENTATIVE / IMPLIED]
    (write NONE if no decisions this chunk)

  ACTION ITEMS
    A-{chunk_n}.1: [Verb-first task] | Owner: [Name/UNASSIGNED] | Deadline: [stated/unstated] | Priority: [H/M/L]
    (write NONE if no action items this chunk)

  OPEN QUESTIONS
    Q-{chunk_n}.1: "[Question as asked]" — [Who] — [OPEN / DEFERRED]
    (write NONE if no open questions)

  ENTITIES (first appearance this session)
    • [Name / Tool / Project / Company] — context
    (write NONE if no new entities)

  FLAGS
    🚩 RISK    : [blocker or concern raised — or NONE]
    ⚠️ TENSION : [disagreement or pushback — or NONE]
    💡 IDEA    : [idea surfaced, not pursued — or NONE]
    🔁 REPEAT  : [topic resurfacing — or NONE]

CONTINUITY NOTE
  [2–3 sentences. What must the next chunk know? Name the active topic,
  last speaker, and any open thread.]

▌STATE CAPSULE — END OF CHUNK {chunk_n}

=== ARIA STATE CAPSULE — CHUNK {chunk_n} | BATCH {batch_n} ===
LAST_CHUNK     : {chunk_n}
BATCH          : {batch_n} | Chunks {batch_start}–{chunk_n} processed

SPEAKERS:
  [Name] | [Role] | [CONFIRMED / LIKELY / INFERRED]

TOPICS:
  [Topic A] → OPEN | last chunk {chunk_n}
  [Topic B] → CLOSED | resolved chunk {chunk_n}

DECISIONS (cumulative this batch):
  D-{chunk_n}.N: [Decision] | [Owner] | Chunk {chunk_n}

ACTIONS (cumulative this batch):
  A-{chunk_n}.N: [Task] | [Owner] | [Deadline] | Chunk {chunk_n}

OPEN QUESTIONS:
  Q-{chunk_n}.N: "[Question]" | [Who] | OPEN

ENTITIES:
  [Name/Tool] | first seen chunk {chunk_n}

EMOTIONAL_CONTEXT:
  [1–2 lines: tone, active tensions to carry forward]

LAST_TOPIC    : [Active topic when chunk ended]
LAST_SPEAKER  : [Name or SPEAKER_N]
MID_SENTENCE  : [YES / NO]
=== END STATE CAPSULE ==="""


# ── MODE B: Rollup Brief ──────────────────────────────────────────────────────

MODE_B_PROMPT = """You are running MODE B — ROLLUP BRIEF.

BATCH INFO:
  Batch Number   : {batch_n}
  Chunks Covered : {batch_start} to {batch_end}
  Total Chunks   : {total_chunks}

CHUNK BRIEFS FROM THIS BATCH:
<chunk_briefs>
{chunk_briefs}
</chunk_briefs>

PRIOR ROLLUP STATE CAPSULE (from end of previous batch):
{prior_rollup_capsule}

Synthesize the above into a Rollup Brief. Write as if you attended
this segment of the meeting yourself. Do not repeat per-chunk details.

▌BATCH {batch_n} ROLLUP BRIEF
  Chunks {batch_start}–{batch_end}

BATCH SUMMARY
  [6–8 sentences of prose. Capture the arc — how did this batch open,
  what was the major thrust, how did it close? Synthesize across chunks.]

WHAT WAS ESTABLISHED IN THIS BATCH

  TOPICS COVERED
    • [Topic] — [OPENED / PROGRESSED / CLOSED] — [2-line recap]

  DECISIONS CONFIRMED (deduplicated from all chunks in batch)
    D-{batch_n}.N: [Decision] | [Owner] | Confidence: [FIRM/TENTATIVE]
    (write NONE if no decisions this batch)

  ACTION ITEMS CONFIRMED (deduplicated, upgraded by later mentions)
    A-{batch_n}.N: [Task] | [Owner] | Deadline: [X] | Priority: [H/M/L]
    (write NONE if no action items this batch)

  OPEN QUESTIONS CARRIED FORWARD
    Q-{batch_n}.N: "[Question]" | [Who] | still OPEN
    (write NONE if no open questions)

  KEY NAMES / ENTITIES INTRODUCED THIS BATCH
    • [Name / Tool / Project] — context
    (write NONE if no new entities)

  BATCH FLAGS
    🚩 [Risk worth escalating to Master Brief — or NONE]
    ⚠️ [Tension thread — active or resolved? — or NONE]
    💡 [Idea worth surfacing in final brief — or NONE]

BATCH NARRATIVE ARC
  [3 sentences. What was the emotional and strategic trajectory?
  What shifted? What is unresolved?]

▌ROLLUP STATE CAPSULE — END OF BATCH {batch_n}

=== ARIA ROLLUP CAPSULE — BATCH {batch_n} | CHUNKS {batch_start}–{batch_end} ===
BATCHES_DONE   : {batch_n}
CHUNKS_DONE    : {batch_end}

SPEAKERS (all confirmed so far):
  [Name] | [Role] | [Confidence]

ALL TOPICS STATUS:
  [Topic A] → CLOSED (batch {batch_n})
  [Topic B] → OPEN (last discussed batch {batch_n})

ALL DECISIONS (session-cumulative):
  D-N: [Decision] | [Owner] | Batch {batch_n}

ALL ACTIONS (session-cumulative):
  A-N: [Task] | [Owner] | [Deadline] | Batch {batch_n}

ALL OPEN QUESTIONS:
  Q-N: "[Question]" | [Who] | [Status]

ALL ENTITIES:
  [Name/Tool] | first batch {batch_n}

SESSION EMOTIONAL ARC SO FAR:
  [2–3 lines: how the meeting has felt, key tensions, momentum shifts]

LAST_ACTIVE_TOPIC : [Topic]
LAST_SPEAKER      : [Name]
=== END ROLLUP CAPSULE ==="""


# ── MODE C: Master Brief ──────────────────────────────────────────────────────

OVERALL_SUMMARY_PROMPT = """You are ARIA — Adaptive Recording Intelligence Analyst.

Produce a concise OVERALL SUMMARY of the entire meeting based on the rollup briefs below.

ROLLUP BRIEFS:
<rollup_briefs>
{rollup_briefs}
</rollup_briefs>

Output ONLY the following JSON (no markdown fences, no extra text):
{{
  "overall_summary": "3-5 sentence high-level abstraction of the entire meeting. Plain prose. No bullets.",
  "overall_bullets": [
    "Bullet point 1 — key theme or outcome",
    "Bullet point 2 — key theme or outcome",
    "Bullet point 3 — key theme or outcome",
    "Bullet point 4 — key theme or outcome (optional)",
    "Bullet point 5 — key theme or outcome (optional)"
  ]
}}

Rules:
- overall_summary: 3-5 sentences, high-level abstraction, no jargon soup
- overall_bullets: 3-5 bullets, each grounded in what was actually discussed
- Return exact JSON only — no preamble, no trailing text"""


MODE_C_PROMPT = """You are running MODE C — MASTER BRIEF (ARIA Final Intelligence Report).

ALL ROLLUP BRIEFS:
<rollup_briefs>
{rollup_briefs}
</rollup_briefs>

FINAL ROLLUP STATE CAPSULE:
{final_rollup_capsule}

Produce the complete ARIA Intelligence Brief. Do not truncate any section.
Do not reference chunks or batches in the output — write as if you
attended the full session from start to finish.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ARIA INTELLIGENCE BRIEF
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

SECTION 0 — SESSION IDENTITY CARD
  Meeting Title  : [infer from content or write UNTITLED]
  Date / Time    : [if mentioned, else UNSPECIFIED]
  Duration       : [approximate, based on content volume]
  Participants   : [all confirmed speakers with roles]
  Format         : [standup / planning / review / lecture / interview / other]
  Language(s)    : [all languages detected]
  Recorded by    : ARIA v1.0 — Automated Intelligence Analysis

SECTION 1 — EXECUTIVE SUMMARY
  [8–12 sentences of dense, senior-analyst prose. No bullets.
  Open with the purpose and stakes of the meeting.
  Cover the major arc, key decisions, critical action items.
  Close with what this meeting means going forward.
  A reader who only reads this section must leave fully informed.]

SECTION 2 — KEYWORD CLOUD & TOPIC MAP
  TOP KEYWORDS (by frequency and significance):
    [comma-separated list of 15–25 keywords from the session]

  TOPIC MAP (sequence of major topics):
    [Topic A] → [Topic B] → [Topic C] → [Topic D] → ...
    [Show the flow of the meeting as a topic chain]

SECTION 3 — KEY TOPICS: DEEP EXPLANATIONS
  [One card per major topic. Minimum 3 topics, no artificial maximum.]

  ┌─────────────────────────────────────────────────────┐
  │ TOPIC: [Name]                                       │
  │ Status: [RESOLVED / ONGOING / DEFERRED / FLAGGED]   │
  │ Discussed by: [Speaker names]                       │
  ├─────────────────────────────────────────────────────┤
  │ [4–6 sentences explaining what was actually said,   │
  │  what position each speaker took, what was agreed   │
  │  or left open. Grounded entirely in what was said.] │
  └─────────────────────────────────────────────────────┘

SECTION 4 — KEY LEARNINGS & INSIGHTS
  [Numbered list of 5–10 substantive insights from this session.
  Each item: 2–3 sentences. Not generic — specific to this meeting.]
  1. [Insight] — [elaboration]
  2. [Insight] — [elaboration]
  ...

SECTION 5 — DECISIONS MADE
  [Every decision from the session, deduplicated and final.]
  D-N: [Decision stated clearly]
       Decided by  : [Name(s)]
       Confidence  : [FIRM / TENTATIVE / IMPLIED]
       Impact      : [1 sentence on what this changes]

SECTION 6 — ACTION ITEMS & TASK REGISTER
  [Every task from the session. Owner-sorted if multiple owners.]
  A-N: [Verb-first task description]
       Owner       : [Name / UNASSIGNED]
       Deadline    : [Date / "by next meeting" / UNSTATED]
       Priority    : [HIGH / MEDIUM / LOW]
       Context     : [1 sentence — why this task exists]

SECTION 7 — MINUTES OF MEETING (MOM)
  [Chronological narrative of the full meeting. Prose only.
  No bullets. Write in past tense. Cover every topic in sequence.
  Use speaker names. 10–20 sentences depending on meeting length.
  This is the official record of what happened.]

SECTION 8 — FLAGS, RISKS & UNRESOLVED THREADS
  🚩 RISKS & BLOCKERS
     [Each risk: what it is, who raised it, current status]

  ⚠️ TENSIONS & DISAGREEMENTS
     [Each tension: what it was about, who was involved, resolved or not]

  ❓ UNRESOLVED QUESTIONS
     [Each open question: verbatim or close paraphrase, owner, status]

  🔁 RECURRING THEMES
     [Topics that came up more than once — signals unresolved root cause]

SECTION 9 — SENTIMENT & ENGAGEMENT ANALYSIS
  Overall Tone    : [Collaborative / Tense / Exploratory / Mixed]
  Energy Arc      : [How the meeting felt at start → middle → end]
  Most Engaged    : [Speaker who drove the most content]
  Notable Moments : [Any clear shift in tone, energy, or direction]
  ARIA Assessment : [2–3 sentences — ARIA's read on the meeting's health]

SECTION 10 — GLOSSARY OF TERMS, JARGON & ACRONYMS
  [Every technical term, acronym, or jargon used — with definition.]
  [TERM] : [plain-English definition based on context]
  (write NONE if no jargon detected)

SECTION 11 — ARIA'S ANALYST NOTE
  [3–5 sentences in ARIA's voice. What matters most from this session?
  What should the reader pay attention to that might not be obvious?
  What is ARIA's honest read on the state of things after this meeting?
  This is ARIA speaking directly — precise, candid, no hedging.]

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
End of ARIA Intelligence Brief
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"""


# ── LLM call wrapper ──────────────────────────────────────────────────────────

def _llm(prompt: str, max_tokens: int = 2000, label: str = "",
         provider: str = "groq", model: str = "llama-3.3-70b-versatile",
         groq_key: str = None, anthropic_key: str = None,
         openai_key: str = None, together_key: str = None,
         mistral_key: str = None) -> str:
    from services.llm_clients import call_llm
    return call_llm(
        prompt=prompt,
        system=ARIA_SYSTEM,
        max_tokens=max_tokens,
        label=label,
        provider=provider,
        model=model,
        groq_key=groq_key,
        anthropic_key=anthropic_key,
        openai_key=openai_key,
        together_key=together_key,
        mistral_key=mistral_key,
    )


def _kwargs(provider, model, groq_key, anthropic_key,
            openai_key, together_key, mistral_key) -> dict:
    return dict(provider=provider, model=model, groq_key=groq_key,
                anthropic_key=anthropic_key, openai_key=openai_key,
                together_key=together_key, mistral_key=mistral_key)


# ── MODE A ────────────────────────────────────────────────────────────────────

def aria_chunk_brief(
    transcript: str,
    chunk_n: int,
    total_chunks: int,
    batch_n: int,
    batch_start: int,
    batch_end: int,
    prior_state_capsule: str = "NONE",
    provider: str = "groq",
    model: str = "llama-3.3-70b-versatile",
    groq_key: str = None,
    anthropic_key: str = None,
    openai_key: str = None,
    together_key: str = None,
    mistral_key: str = None,
) -> tuple[str, str]:
    """
    Run ARIA Mode A on a single transcript chunk.
    Returns (chunk_brief_text, state_capsule_text).
    Must be called sequentially — state capsule feeds next chunk.
    """
    if not transcript.strip():
        empty = f"[CHUNK {chunk_n} — empty transcript]"
        return empty, prior_state_capsule

    # Token budget: truncate inputs before building prompt
    transcript = truncate_chunk(transcript)
    prior_state_capsule = truncate_capsule(prior_state_capsule)
    log_budget_stats(f"Mode A chunk {chunk_n}", len(transcript), BUDGET["chunk_input_chars"])

    prompt = MODE_A_PROMPT.format(
        chunk_n=chunk_n,
        total_chunks=total_chunks,
        batch_n=batch_n,
        batch_start=batch_start,
        batch_end=batch_end,
        prior_state_capsule=prior_state_capsule,
        transcript=transcript,
    )

    result = _llm(prompt, max_tokens=BUDGET["mode_a_max_tokens"],
                  label=f"Mode A — Chunk {chunk_n}/{total_chunks}",
                  **_kwargs(provider, model, groq_key, anthropic_key,
                            openai_key, together_key, mistral_key))

    if not result:
        logger.warning(f"ARIA Mode A returned empty for chunk {chunk_n}")
        return f"[CHUNK {chunk_n} — brief unavailable]", prior_state_capsule

    # Extract state capsule from result
    capsule = _extract_capsule(result, "STATE CAPSULE")
    logger.info(f"ARIA Mode A complete — chunk {chunk_n}/{total_chunks}")
    return result, capsule


# ── MODE B ────────────────────────────────────────────────────────────────────

def aria_rollup_brief(
    chunk_briefs: list[str],
    batch_n: int,
    batch_start: int,
    batch_end: int,
    total_chunks: int,
    prior_rollup_capsule: str = "NONE",
    provider: str = "groq",
    model: str = "llama-3.3-70b-versatile",
    groq_key: str = None,
    anthropic_key: str = None,
    openai_key: str = None,
    together_key: str = None,
    mistral_key: str = None,
) -> tuple[str, str]:
    """
    Run ARIA Mode B on a batch of chunk briefs.
    Returns (rollup_brief_text, rollup_capsule_text).
    """
    # Token budget: fit briefs within Mode B input limit
    combined_briefs = fit_rollup_briefs(chunk_briefs, BUDGET["mode_b_input_chars"])
    prior_rollup_capsule = truncate_capsule(prior_rollup_capsule)
    log_budget_stats(f"Mode B batch {batch_n}", len(combined_briefs), BUDGET["mode_b_input_chars"])

    prompt = MODE_B_PROMPT.format(
        batch_n=batch_n,
        batch_start=batch_start,
        batch_end=batch_end,
        total_chunks=total_chunks,
        chunk_briefs=combined_briefs,
        prior_rollup_capsule=prior_rollup_capsule,
    )

    result = _llm(prompt, max_tokens=BUDGET["mode_b_max_tokens"],
                  label=f"Mode B — Batch {batch_n} (Chunks {batch_start}–{batch_end})",
                  **_kwargs(provider, model, groq_key, anthropic_key,
                            openai_key, together_key, mistral_key))

    if not result:
        logger.warning(f"ARIA Mode B returned empty for batch {batch_n}")
        return f"[BATCH {batch_n} rollup unavailable]", prior_rollup_capsule

    capsule = _extract_capsule(result, "ROLLUP CAPSULE")
    logger.info(f"ARIA Mode B complete — batch {batch_n} (chunks {batch_start}–{batch_end})")
    return result, capsule


# ── MODE C ────────────────────────────────────────────────────────────────────

def aria_master_brief(
    rollup_briefs: list[str],
    final_rollup_capsule: str,
    s0_briefs: list[str] = None,
    provider: str = "groq",
    model: str = "llama-3.3-70b-versatile",
    groq_key: str = None,
    anthropic_key: str = None,
    openai_key: str = None,
    together_key: str = None,
    mistral_key: str = None,
) -> dict:
    """
    Run ARIA Mode C — produces the Overall Summary (S0) FIRST, then the full Master Brief.

    S0 is generated from s0_briefs, which is chosen by run_aria_pipeline using the same
    tier logic as the rest of the pipeline:
      ≤6 chunks  → raw chunk briefs (fed directly, no rollup needed)
      7–20       → Mode B rollup briefs (one per batch of chunks)
      21+        → meta-rollups (rollups of rollups)
    This mirrors exactly how Mode C receives its input, ensuring S0 covers the full meeting
    at the appropriate level of compression for its length.

    Falls back to rollup_briefs if s0_briefs is not provided.
    """
    # ── Check if rollup briefs exceed Mode C context window ──
    # If so, run an extra Mode B compression pass rather than truncating.
    combined_rollups = fit_rollup_briefs(rollup_briefs)
    mode_c_limit = BUDGET["mode_c_input_chars"]

    if len(combined_rollups) > mode_c_limit:
        logger.warning(
            f"Rollup briefs ({len(combined_rollups):,} chars) exceed Mode C limit "
            f"({mode_c_limit:,} chars) — running extra compression pass"
        )
        # Batch the rollup briefs through Mode B again to compress further
        compression_batch_size = max(2, len(rollup_briefs) // 2)
        compressed = []
        for i in range(0, len(rollup_briefs), compression_batch_size):
            batch = rollup_briefs[i:i + compression_batch_size]
            batch_n = (i // compression_batch_size) + 1
            compressed_brief, _ = aria_rollup_brief(
                chunk_briefs=batch,
                batch_n=batch_n,
                batch_start=i + 1,
                batch_end=min(i + compression_batch_size, len(rollup_briefs)),
                total_chunks=len(rollup_briefs),
                prior_rollup_capsule="NONE",
                provider=provider, model=model,
                groq_key=groq_key, anthropic_key=anthropic_key,
                openai_key=openai_key, together_key=together_key,
                mistral_key=mistral_key,
            )
            compressed.append(compressed_brief)
        combined_rollups = fit_rollup_briefs(compressed)
        logger.info(
            f"Extra compression pass complete — "
            f"{len(rollup_briefs)} rollups → {len(compressed)} ({len(combined_rollups):,} chars)"
        )

    log_budget_stats("Mode C", len(combined_rollups), mode_c_limit)
    llm_kwargs = _kwargs(provider, model, groq_key, anthropic_key,
                         openai_key, together_key, mistral_key)

    # ── S0: Overall Summary — generated FIRST using the tier-appropriate brief source ──
    # s0_briefs is set by run_aria_pipeline to match the same tier used for Mode C:
    #   ≤6 chunks → chunk_briefs, 7-20 → rollup_briefs, 21+ → meta_rollups
    logger.info("ARIA generating Overall Summary (S0)...")
    overall_summary = ""
    overall_bullets: list[str] = []
    try:
        s0_source = s0_briefs if s0_briefs else rollup_briefs
        s0_input = fit_rollup_briefs(s0_source)
        log_budget_stats(
            f"S0 overall summary ({len(s0_source)} briefs in source)",
            len(s0_input), BUDGET["s0_input_chars"]
        )
        s0_prompt = OVERALL_SUMMARY_PROMPT.format(rollup_briefs=s0_input)
        s0_raw = _llm(s0_prompt, max_tokens=BUDGET["s0_max_tokens"],
                      label="Mode C — S0 Overall Summary", **llm_kwargs)
        if s0_raw:
            import json as _json, re as _re
            clean = _re.sub(r"^```(?:json)?\s*", "", s0_raw.strip())
            clean = _re.sub(r"\s*```$", "", clean).strip()
            s0_data = _json.loads(clean)
            overall_summary = s0_data.get("overall_summary", "")
            overall_bullets = s0_data.get("overall_bullets", [])
            logger.info("ARIA Overall Summary (S0) generated successfully")
    except Exception as e:
        logger.warning(f"ARIA S0 generation failed: {e} — continuing to master brief")

    # ── Mode C: Full Master Brief ──
    prompt = MODE_C_PROMPT.format(
        rollup_briefs=combined_rollups,
        final_rollup_capsule=final_rollup_capsule,
    )

    result = _llm(prompt, max_tokens=4000, label="Mode C — Master Brief", **llm_kwargs)

    if not result:
        logger.error("ARIA Mode C returned empty — master brief unavailable")
        empty = _empty_master()
        empty["overall_summary"] = overall_summary
        empty["overall_bullets"] = overall_bullets
        return empty

    logger.info("ARIA Mode C complete — master brief generated")
    parsed = _parse_master_brief(result)
    parsed["overall_summary"] = overall_summary
    parsed["overall_bullets"] = overall_bullets
    return parsed


# ── Full pipeline orchestrator ────────────────────────────────────────────────

def run_aria_pipeline(
    chunk_transcripts: list[str],
    provider: str = "groq",
    model: str = "llama-3.3-70b-versatile",
    groq_key: str = None,
    anthropic_key: str = None,
    openai_key: str = None,
    together_key: str = None,
    mistral_key: str = None,
) -> dict:
    """
    Full ARIA pipeline:
    1. Mode A sequentially on each chunk (needs prior state capsule)
    2. Mode B on every ROLLUP_EVERY chunks
    3. Mode C on all rollup briefs

    Token budget rule:
    - ≤6 chunks: skip Mode B, feed directly to Mode C
    - 7-20 chunks: rollup every 5, then Mode C
    - 21+ chunks: rollup every 5, then rollup the rollups, then Mode C
    """
    total = len(chunk_transcripts)
    kwargs = dict(provider=provider, model=model, groq_key=groq_key,
                  anthropic_key=anthropic_key, openai_key=openai_key,
                  together_key=together_key, mistral_key=mistral_key)

    # Adaptive batch size — reduces total LLM calls on long meetings
    rollup_every = adaptive_chunk_size(total)
    logger.info(
        f"ARIA pipeline starting — {total} chunks, rollup_every={rollup_every}, "
        f"provider={provider}, model={model}"
    )

    # ── Step 1: Mode A — sequential chunk briefs ──
    chunk_briefs = []
    state_capsule = "NONE"

    for i, transcript in enumerate(chunk_transcripts):
        chunk_n    = i + 1
        batch_n    = (i // rollup_every) + 1
        batch_start = ((batch_n - 1) * rollup_every) + 1
        batch_end   = min(batch_n * rollup_every, total)

        brief, state_capsule = aria_chunk_brief(
            transcript=transcript,
            chunk_n=chunk_n,
            total_chunks=total,
            batch_n=batch_n,
            batch_start=batch_start,
            batch_end=batch_end,
            prior_state_capsule=state_capsule,
            **kwargs,
        )
        chunk_briefs.append(brief)

    # ── Step 2: Mode B — rollup briefs (skip if ≤6 chunks) ──
    if total <= 6:
        # ≤6 chunks: no Mode B needed — chunk briefs feed Mode C and S0 directly
        logger.info("≤6 chunks — skipping Mode B, chunk briefs feed Mode C and S0 directly")
        rollup_briefs = [fit_rollup_briefs(chunk_briefs, BUDGET["mode_b_input_chars"])]
        final_capsule = state_capsule
        # S0 tier: extract just the CHUNK SUMMARY prose from each chunk brief
        # (not the full brief — decisions, flags, state capsule are noise for S0)
        s0_briefs = extract_chunk_summaries_for_s0(chunk_briefs)

    else:
        rollup_briefs = []
        rollup_capsule = "NONE"
        batches = [chunk_briefs[i:i+rollup_every]
                   for i in range(0, len(chunk_briefs), rollup_every)]

        for b_idx, batch_briefs in enumerate(batches):
            batch_n     = b_idx + 1
            batch_start = b_idx * rollup_every + 1
            batch_end   = min(batch_start + len(batch_briefs) - 1, total)

            rollup, rollup_capsule = aria_rollup_brief(
                chunk_briefs=batch_briefs,
                batch_n=batch_n,
                batch_start=batch_start,
                batch_end=batch_end,
                total_chunks=total,
                prior_rollup_capsule=rollup_capsule,
                **kwargs,
            )
            rollup_briefs.append(rollup)

        final_capsule = rollup_capsule
        # S0 tier (7–20 chunks): Mode B rollup briefs — one per batch, covers all chunks
        s0_briefs = rollup_briefs

        # ≥21 chunks: rollup the rollups
        if total >= 21 and len(rollup_briefs) > 4:
            logger.info(f"≥21 chunks — running second-level rollup on {len(rollup_briefs)} rollups")
            meta_rollups = []
            meta_capsule = "NONE"
            meta_batches = [rollup_briefs[i:i+rollup_every]
                            for i in range(0, len(rollup_briefs), rollup_every)]
            for m_idx, meta_batch in enumerate(meta_batches):
                meta, meta_capsule = aria_rollup_brief(
                    chunk_briefs=meta_batch,
                    batch_n=m_idx + 1,
                    batch_start=m_idx * rollup_every + 1,
                    batch_end=min((m_idx + 1) * rollup_every, len(rollup_briefs)),
                    total_chunks=len(rollup_briefs),
                    prior_rollup_capsule=meta_capsule,
                    **kwargs,
                )
                meta_rollups.append(meta)
            rollup_briefs = meta_rollups
            final_capsule = meta_capsule
            # S0 tier (21+ chunks): meta-rollups — mirrors the same source as Mode C
            s0_briefs = meta_rollups

    # ── Step 3: Mode C — master brief ──
    # s0_briefs uses the same tier as Mode C input so S0 mirrors pipeline coverage:
    #   ≤6  → chunk_briefs   (direct, no compression)
    #   7–20 → rollup_briefs  (Mode B output, one per batch)
    #   21+  → meta_rollups   (second-level rollups)
    master = aria_master_brief(
        rollup_briefs=rollup_briefs,
        final_rollup_capsule=final_capsule,
        s0_briefs=s0_briefs,
        **kwargs,
    )

    master["chunk_briefs"] = chunk_briefs
    master["rollup_briefs"] = rollup_briefs
    return master


# ── Helpers ───────────────────────────────────────────────────────────────────

def _extract_capsule(text: str, capsule_type: str) -> str:
    """Extract the state/rollup capsule block from ARIA output."""
    pattern = rf"=== ARIA {capsule_type}.*?=== END {capsule_type} ==="
    match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
    if match:
        return match.group(0)
    # Fallback — return last 800 chars as context
    return text[-800:] if len(text) > 800 else text


def _extract_chunk_summary(brief: str) -> str:
    """
    Extract only the CHUNK SUMMARY prose block from a Mode A chunk brief.
    This is the 4-6 sentence prose section — the only part S0 needs.
    Falls back to the first 400 chars of the brief if section not found.
    """
    # Match everything between CHUNK SUMMARY header and the next major section
    match = re.search(
        r"CHUNK SUMMARY[^\n]*\n(.*?)(?=\nSTRUCTURED|\nCONTINUITY|\n===)",
        brief,
        re.DOTALL,
    )
    if match:
        summary = match.group(1).strip()
        if summary:
            return summary
    return brief[:400].strip()


def extract_chunk_summaries_for_s0(chunk_briefs: list[str]) -> list[str]:
    """
    For the ≤6 chunk tier: extract just the CHUNK SUMMARY prose from each
    chunk brief instead of feeding the full structured brief to S0.
    Rollup and meta-rollup briefs are already synthesised — use them as-is.
    """
    summaries = []
    for i, brief in enumerate(chunk_briefs):
        extracted = _extract_chunk_summary(brief)
        label = f"[Chunk {i+1} Summary]\n{extracted}"
        summaries.append(label)
        logger.debug(f"S0 chunk {i+1}: extracted {len(extracted)} chars from {len(brief)} char brief")
    return summaries


def _parse_master_brief(raw: str) -> dict:
    """
    Parse the Mode C output into a structured dict.
    Extracts each section by its header.
    """
    sections = {}

    section_patterns = {
        "session_identity":   r"SECTION 0[^█]*?(?=SECTION 1|$)",
        "executive_summary":  r"SECTION 1[^█]*?(?=SECTION 2|$)",
        "keyword_map":        r"SECTION 2[^█]*?(?=SECTION 3|$)",
        "key_topics":         r"SECTION 3[^█]*?(?=SECTION 4|$)",
        "key_learnings":      r"SECTION 4[^█]*?(?=SECTION 5|$)",
        "decisions":          r"SECTION 5[^█]*?(?=SECTION 6|$)",
        "action_items":       r"SECTION 6[^█]*?(?=SECTION 7|$)",
        "minutes_of_meeting": r"SECTION 7[^█]*?(?=SECTION 8|$)",
        "flags_risks":        r"SECTION 8[^█]*?(?=SECTION 9|$)",
        "sentiment":          r"SECTION 9[^█]*?(?=SECTION 10|$)",
        "glossary":           r"SECTION 10[^█]*?(?=SECTION 11|$)",
        "analyst_note":       r"SECTION 11[^█]*?(?=━|$)",
    }

    for key, pattern in section_patterns.items():
        match = re.search(pattern, raw, re.DOTALL | re.IGNORECASE)
        sections[key] = match.group(0).strip() if match else ""

    # Extract overview for backward compatibility
    exec_summary = sections.get("executive_summary", "")
    overview = exec_summary[:1000] if exec_summary else raw[:1000]

    return {
        "overview":     overview,
        "master_brief": raw,
        "sections":     sections,
        # Backward-compatible fields for existing API/frontend
        "key_points":   _extract_list(sections.get("key_learnings", "")),
        "decisions":    _extract_list(sections.get("decisions", "")),
        "action_items": _extract_list(sections.get("action_items", "")),
        "next_steps":   [],
    }


def _extract_list(section_text: str) -> list:
    """Extract numbered or bulleted items from a section."""
    if not section_text:
        return []
    lines = section_text.split("\n")
    items = []
    for line in lines:
        line = line.strip()
        # Match lines starting with number, bullet, D-, A-, Q-
        if re.match(r"^(\d+\.|•|-|[DAQ]-\d)", line) and len(line) > 5:
            # Clean prefix
            clean = re.sub(r"^(\d+\.|•|-|[DAQ]-[\d.]+:?)\s*", "", line).strip()
            if clean:
                items.append(clean)
    return items[:20]  # Cap at 20 items


def _empty_master() -> dict:
    return {
        "overall_summary": "",
        "overall_bullets": [],
        "overview":     "ARIA master brief unavailable.",
        "master_brief": "",
        "sections":     {},
        "key_points":   [],
        "decisions":    [],
        "action_items": [],
        "next_steps":   [],
        "chunk_briefs":  [],
        "rollup_briefs": [],
    }