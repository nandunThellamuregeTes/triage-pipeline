"""Stage 1.5 -- Challenge Loop (iterative hypothesis validation via adversarial review).

Agent 2 (Challenge/Devil's Advocate) tries to DISPROVE Agent 1's hypothesis.
If Agent 2 finds credible flaws, Agent 1 regenerates with feedback.
Loop repeats up to max_loops times, then escalates to human if no convergence.
"""
from __future__ import annotations

import json
import time
from difflib import SequenceMatcher
from typing import Optional

from ..llm.base import LLMClient, ToolBudgetExhausted, ToolCall
from ..schemas import (
    ChallengeLoopIteration,
    ChallengeResult,
    InvestigationResult,
    TicketFacts,
)
from ..tools.code_search import CodeSearchTool, redact_secrets

STAGE_NAME = "stage1_5_challenge"
DEFAULT_MAX_LOOPS = 3
DEFAULT_CONFIDENCE_BOOST_BASE = 0.05

TOOLS = [
    {
        "name": "search_code",
        "description": "Keyword/regex search across the repository via ripgrep.",
        "input_schema": {
            "type": "object",
            "required": ["query"],
            "properties": {
                "query": {"type": "string"},
                "max_hits": {"type": "integer", "default": 20},
            },
        },
    },
    {
        "name": "read_excerpt",
        "description": "Bounded excerpt (+/- ~15 lines) around a line number in a file.",
        "input_schema": {
            "type": "object",
            "required": ["path", "line"],
            "properties": {
                "path": {"type": "string"},
                "line": {"type": "integer"},
            },
        },
    },
]

AGENT2_CHALLENGE_OUTPUT_SCHEMA = {
    "type": "object",
    "required": ["can_disprove", "confidence_in_disproof"],
    "properties": {
        "can_disprove": {"type": "boolean"},
        "flaws_found": {"type": "array", "items": {"type": "string"}},
        "alternative_hypotheses": {"type": "array", "items": {"type": "string"}},
        "codebase_findings": {"type": "array", "items": {"type": "string"}},
        "confidence_in_disproof": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "clarifying_questions": {"type": "array", "items": {"type": "string"}},
    },
}

AGENT1_REGEN_OUTPUT_SCHEMA = {
    "type": "object",
    "required": ["root_cause_hypothesis", "overall_confidence"],
    "properties": {
        "root_cause_hypothesis": {"type": "string"},
        "overall_confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "justification_for_changes": {"type": "string"},
    },
}


def _agent2_system_prompt() -> str:
    """System prompt for Agent 2 (Challenge/Devil's Advocate)."""
    return """\
You are Agent 2 ("Challenge") in the bug-triage pipeline's iterative challenge loop.

Your role is ADVERSARIAL: assume Agent 1's hypothesis is WRONG. Try to DISPROVE it.

You have access to the same tools as Agent 1: `search_code()` and `read_excerpt()`.
You can read the codebase and verify claims independently.

Your goal:
1. Re-read the ticket symptoms independently (ignore Agent 1's framing).
2. Review Agent 1's hypothesis and reasoning.
3. Look for FLAWS, gaps, contradictions, or unsupported assumptions.
4. Spot-check Agent 1's code claims against the actual codebase.
5. Identify alternative explanations that could also explain the symptoms.
6. Rate your confidence in whether Agent 1's hypothesis is actually WRONG (0.0-1.0).

CONFIDENCE SCALE:
Express confidence_in_disproof as a decimal from 0.0 to 1.0:
  - 1.0 = fully confident hypothesis is WRONG
  - 0.75 = quite confident in the flaws
  - 0.5 = hypothesis has merit, but flaws found
  - 0.25 = very uncertain, hypothesis might be right
  - 0.0 = cannot disprove it at all
DO NOT use 0-100 scale (e.g., do not output 80 or 75). Always output as 0.xx.

Output:
- can_disprove: boolean -- did you find credible flaws? (should correlate with confidence > 0.7)
- flaws_found: list of specific gaps or logical errors in Agent 1's reasoning
- alternative_hypotheses: other plausible root causes
- codebase_findings: specific file:line references you verified
- confidence_in_disproof: 0.0-1.0 -- how confident are you that Agent 1's hypothesis is flawed?
- clarifying_questions: specific questions Agent 1 should address

Be aggressive. Assume the hypothesis is wrong until proven otherwise.
Respond with structured JSON matching the given schema exactly.
"""


def _agent1_regen_system_prompt() -> str:
    """System prompt for Agent 1 when regenerating after Agent 2 feedback."""
    return """\
You are Agent 1 ("Analyst") in the bug-triage pipeline's challenge loop.

You previously generated a hypothesis about the root cause of a bug.
Agent 2 (Challenge) has reviewed your hypothesis and provided feedback.

Your task:
1. Read Agent 2's feedback carefully.
2. Address Agent 2's specific concerns:
   - If they found flaws in your reasoning, acknowledge and refine.
   - If they found alternative hypotheses, explain why yours is more plausible.
   - If they spot-checked your code claims, confirm or correct them.
3. Regenerate your hypothesis if needed, incorporating the feedback.
4. Explain your reasoning for any changes you made.

CONFIDENCE SCALE:
Express overall_confidence as a decimal from 0.0 to 1.0:
  - 1.0 = fully confident in this hypothesis
  - 0.75 = quite confident
  - 0.5 = moderately confident, alternatives remain plausible
  - 0.25 = uncertain, significant alternative explanations exist
  - 0.0 = not confident at all
DO NOT use 0-100 scale (e.g., do not output 80 or 75). Always output as 0.xx.

Output:
- root_cause_hypothesis: your (possibly refined) hypothesis
- overall_confidence: 0.0-1.0 your confidence in this hypothesis
- justification_for_changes: explain what feedback you incorporated and why

Respond with structured JSON matching the given schema exactly.
"""


def _make_tool_executor(code_search: CodeSearchTool):
    """Create a tool executor for both Agent 1 and Agent 2."""
    def executor(call: ToolCall):
        if call.name == "search_code":
            hits = code_search.search(
                call.arguments.get("query", ""), max_hits=call.arguments.get("max_hits", 20)
            )
            return [{"path": h.path, "line": h.line, "snippet": h.snippet} for h in hits]
        if call.name == "read_excerpt":
            return code_search.read_excerpt(call.arguments["path"], int(call.arguments["line"]))
        raise ValueError(f"unknown tool: {call.name}")

    return executor


def _string_similarity(a: str, b: str) -> float:
    """Compute string similarity ratio (0-1) using difflib."""
    return SequenceMatcher(None, a, b).ratio()


def _detect_convergence(
    prev_hypothesis: str, new_hypothesis: str, threshold: float = 0.85
) -> bool:
    """
    Detect if hypothesis has improved.
    Returns True if new hypothesis is similar to previous (likely no improvement).
    """
    similarity = _string_similarity(prev_hypothesis, new_hypothesis)
    return similarity < threshold  # True = improved (low similarity = different = improved)


def _normalize_confidence(value: float) -> float:
    """
    Normalize confidence to 0-1 scale.
    Handles cases where LLM outputs 0-100 instead of 0-1.
    """
    conf = float(value)
    # If > 1, assume it was meant to be 0-100 and convert
    if conf > 1.0:
        conf = conf / 100.0
    # Clamp to valid range
    return max(0.0, min(1.0, conf))


def run_challenge_loop(
    llm_client: LLMClient,
    ticket_facts: TicketFacts,
    ticket_title: str,
    repo_root: str,
    initial_investigation: InvestigationResult,
    initial_confidence: float,
    model: str,
    max_loops: int = DEFAULT_MAX_LOOPS,
) -> tuple[InvestigationResult, float, list[ChallengeLoopIteration], bool]:
    """
    Run the iterative challenge loop.

    Args:
        llm_client: LLM client
        ticket_facts: extracted ticket facts
        ticket_title: ticket title
        repo_root: repository root path
        initial_investigation: Agent 1's initial investigation result
        initial_confidence: Agent 1's initial confidence
        model: model to use
        max_loops: maximum number of challenge iterations

    Returns:
        (refined_investigation, final_confidence, challenge_history, escalated_to_human)
    """
    loop_start_time = time.monotonic()
    loop_timings: list[dict] = []  # Track per-iteration timing
    
    code_search = CodeSearchTool(repo_root)
    challenge_history: list[ChallengeLoopIteration] = []

    current_hypothesis = initial_investigation.root_cause_hypothesis
    current_confidence = initial_confidence
    current_investigation = initial_investigation
    escalated = False

    for loop_num in range(1, max_loops + 1):
        iter_start_time = time.monotonic()
        print(f"[stage1.5] Challenge loop iteration {loop_num}/{max_loops}...")

        # --- AGENT 2: Challenge the hypothesis ---
        agent2_content = json.dumps(
            {
                "ticket_title": ticket_title,
                "symptoms": ticket_facts.symptoms,
                "repro_steps": ticket_facts.repro_steps,
                "stack_trace_or_error": ticket_facts.stack_trace_or_error,
                "agent1_hypothesis": current_hypothesis,
                "agent1_reasoning": initial_investigation.root_cause_hypothesis,
                "agent1_confidence": current_confidence,
                "affected_areas": [
                    {
                        "path": ca.path,
                        "line": ca.line,
                        "confidence": ca.confidence,
                        "reason": ca.reason,
                    }
                    for ca in current_investigation.candidate_areas
                ],
            },
            indent=2,
        )

        try:
            agent2_raw = llm_client.structured_call(
                stage=f"{STAGE_NAME}_agent2_loop{loop_num}",
                model=model,
                system_prompt=_agent2_system_prompt(),
                user_content=agent2_content,
                output_schema=AGENT2_CHALLENGE_OUTPUT_SCHEMA,
                tools=TOOLS,
                tool_executor=_make_tool_executor(code_search),
                max_tool_turns=10,  # lower than Agent 1 since Agent 2 is focused
                cwd=repo_root,
            )
        except ToolBudgetExhausted as exc:
            # If Agent 2 runs out of budget, treat as "can't disprove" (hypothesis accepted)
            agent2_raw = {
                "can_disprove": False,
                "confidence_in_disproof": 0.3,
                "flaws_found": [],
                "alternative_hypotheses": [],
                "codebase_findings": [],
                "clarifying_questions": ["(Unable to fully investigate due to tool budget)"],
            }

        agent2_verdict = ChallengeResult(
            can_disprove=bool(agent2_raw.get("can_disprove", False)),
            flaws_found=list(agent2_raw.get("flaws_found", [])),
            alternative_hypotheses=list(agent2_raw.get("alternative_hypotheses", [])),
            codebase_findings=list(agent2_raw.get("codebase_findings", [])),
            confidence_in_disproof=_normalize_confidence(agent2_raw.get("confidence_in_disproof", 0.0)),
            clarifying_questions=list(agent2_raw.get("clarifying_questions", [])),
        )

        print(
            f"[stage1.5] Agent 2 verdict: can_disprove={agent2_verdict.can_disprove}, "
            f"confidence={agent2_verdict.confidence_in_disproof:.0%}"
        )

        # --- DECISION: Proceed or accept hypothesis? ---
        agent1_regen_hypothesis: Optional[str] = None
        convergence_status: Optional[str] = None

        if agent2_verdict.can_disprove and agent2_verdict.confidence_in_disproof > 0.7:
            # Agent 2 found credible flaws → Agent 1 regenerates
            print(f"[stage1.5] Flaws found. Asking Agent 1 to regenerate...")

            agent1_regen_content = json.dumps(
                {
                    "ticket_title": ticket_title,
                    "symptoms": ticket_facts.symptoms,
                    "repro_steps": ticket_facts.repro_steps,
                    "your_previous_hypothesis": current_hypothesis,
                    "your_previous_confidence": current_confidence,
                    "agent2_feedback": {
                        "flaws_found": agent2_verdict.flaws_found,
                        "alternative_hypotheses": agent2_verdict.alternative_hypotheses,
                        "codebase_findings": agent2_verdict.codebase_findings,
                        "clarifying_questions": agent2_verdict.clarifying_questions,
                    },
                },
                indent=2,
            )

            try:
                agent1_regen_raw = llm_client.structured_call(
                    stage=f"{STAGE_NAME}_agent1_regen_loop{loop_num}",
                    model=model,
                    system_prompt=_agent1_regen_system_prompt(),
                    user_content=agent1_regen_content,
                    output_schema=AGENT1_REGEN_OUTPUT_SCHEMA,
                    tools=TOOLS,
                    tool_executor=_make_tool_executor(code_search),
                    max_tool_turns=8,
                    cwd=repo_root,
                )
            except ToolBudgetExhausted:
                agent1_regen_raw = {
                    "root_cause_hypothesis": current_hypothesis,  # keep previous
                    "overall_confidence": current_confidence,
                    "justification_for_changes": "Regeneration was interrupted (tool budget).",
                }

            agent1_regen_hypothesis = agent1_regen_raw.get("root_cause_hypothesis", "")
            new_confidence = _normalize_confidence(agent1_regen_raw.get("overall_confidence", current_confidence))

            # Check convergence (has hypothesis improved?)
            improved = _detect_convergence(current_hypothesis, agent1_regen_hypothesis)
            convergence_status = "improved" if improved else "no_change"

            print(f"[stage1.5] Convergence: {convergence_status}, new_confidence={new_confidence:.0%}")

            current_hypothesis = agent1_regen_hypothesis
            current_confidence = new_confidence

        else:
            # Agent 2 could not disprove → Hypothesis is valid
            print(
                f"[stage1.5] Hypothesis withstands challenge "
                f"(confidence_in_disproof={agent2_verdict.confidence_in_disproof:.0%})"
            )
            convergence_status = "converged"

            # Boost confidence based on Agent 2's confidence
            boost = DEFAULT_CONFIDENCE_BOOST_BASE
            if agent2_verdict.confidence_in_disproof > 0.8:
                # High confidence that Agent 2 couldn't disprove → higher boost
                boost = 0.10
            elif agent2_verdict.confidence_in_disproof < 0.5:
                # Low confidence in disproof → smaller boost
                boost = 0.02

            current_confidence = min(current_confidence + boost, 0.95)  # cap at 0.95
            print(f"[stage1.5] Confidence boosted to {current_confidence:.0%} (+{boost:.0%})")

        # Record this loop iteration
        iteration = ChallengeLoopIteration(
            loop_num=loop_num,
            agent2_verdict=agent2_verdict,
            agent1_regenerated_hypothesis=agent1_regen_hypothesis,
            convergence_status=convergence_status,
            confidence_before=current_confidence - (boost if convergence_status == "converged" else 0),
            confidence_after=current_confidence,
        )
        challenge_history.append(iteration)

        # Record iteration timing
        iter_elapsed = time.monotonic() - iter_start_time
        loop_timings.append({
            "loop_num": loop_num,
            "elapsed_seconds": iter_elapsed,
            "status": convergence_status,
        })

        # --- Termination checks ---
        if convergence_status == "converged":
            # Hypothesis is accepted, exit loop
            print(f"[stage1.5] Hypothesis accepted after {loop_num} loop(s).")
            break

        if convergence_status == "no_change" and loop_num >= 2:
            # No progress after regeneration, escalate
            print(
                f"[stage1.5] No convergence after {loop_num} loop(s). "
                "Escalating to human review."
            )
            escalated = True
            break

        if loop_num >= max_loops:
            # Hit max loops without convergence
            print(
                f"[stage1.5] Reached max loops ({max_loops}) without full convergence. "
                "Escalating to human review."
            )
            escalated = True
            break

    # Update investigation with final hypothesis and confidence
    final_investigation = InvestigationResult(
        candidate_areas=current_investigation.candidate_areas,
        root_cause_hypothesis=current_hypothesis,
        overall_confidence=current_confidence,
        fix_proposals=current_investigation.fix_proposals,
        blast_radius=current_investigation.blast_radius,
        affected_areas=current_investigation.affected_areas,
        impact_notes=current_investigation.impact_notes,
        story_point_factors=current_investigation.story_point_factors,
        degraded=current_investigation.degraded,
        degraded_reason=current_investigation.degraded_reason,
    )

    # Print per-iteration timing breakdown
    total_loop_time = time.monotonic() - loop_start_time
    if loop_timings:
        print("\n[stage1.5] Challenge loop timing breakdown:")
        for timing in loop_timings:
            print(
                f"  Loop {timing['loop_num']}: {timing['elapsed_seconds']:.1f}s "
                f"({timing['status']})"
            )
        print(f"[stage1.5] Total challenge loop time: {total_loop_time:.1f}s")

    return final_investigation, current_confidence, challenge_history, escalated
