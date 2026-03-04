"""
Propositional Chunker — decomposes agent plans into atomic, dedupable Propositions.

Each Proposition represents a single, indivisible intent: one action on one subject
with one priority tier. This granularity is what allows the Consensus Engine to
perform Late Interaction deduplication at the semantic level.
"""

from __future__ import annotations

import hashlib
import json
import os
from enum import Enum
from typing import Optional

import anthropic
from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Priority Tiers (mirrors src/ontology/rules.json)
# ---------------------------------------------------------------------------

class PriorityTier(str, Enum):
    COMPLIANCE = "compliance"   # Tier 1 — Absolute veto power
    RISK       = "risk"         # Tier 2 — Blocks unless cleared by Risk team
    OPS        = "ops"          # Tier 3 — Operational / data integrity
    GROWTH     = "growth"       # Tier 4 — Revenue / expansion (lowest priority)


# ---------------------------------------------------------------------------
# Core Data Models
# ---------------------------------------------------------------------------

class Proposition(BaseModel):
    """
    Atomic unit of agent intent.

    A Proposition is the smallest meaningful action an agent wants to take.
    It is self-contained: given only this object, a human reviewer can understand
    what the agent intends to do, to whom, and why.
    """
    id: str = Field(description="Deterministic SHA-256 hash of (agent_id + action + subject)")
    agent_id: str = Field(description="Identifier of the originating agent")
    tier: PriorityTier = Field(description="Priority tier assigned by the LLM")
    action: str = Field(description="Verb describing what the agent wants to do, e.g. 'apply_discount'")
    subject: str = Field(description="The entity or resource the action targets, e.g. 'account:ACME-Corp'")
    rationale: str = Field(description="One-sentence explanation of why this action is needed")
    raw_text: str = Field(description="The verbatim fragment from the original plan that produced this proposition")
    confidence: float = Field(ge=0.0, le=1.0, description="LLM confidence score for tier assignment")

    @field_validator("id", mode="before")
    @classmethod
    def _auto_id(cls, v: Optional[str], info) -> str:  # noqa: ANN001
        """Auto-generate a deterministic ID if not provided."""
        if v:
            return v
        data = info.data
        seed = f"{data.get('agent_id', '')}:{data.get('action', '')}:{data.get('subject', '')}"
        return hashlib.sha256(seed.encode()).hexdigest()[:16]

    @property
    def tier_rank(self) -> int:
        """Lower rank = higher priority. Compliance=0, Growth=3."""
        return {
            PriorityTier.COMPLIANCE: 0,
            PriorityTier.RISK:       1,
            PriorityTier.OPS:        2,
            PriorityTier.GROWTH:     3,
        }[self.tier]


class ChunkResult(BaseModel):
    """Output of a single chunking operation."""
    agent_id: str
    original_plan: str
    propositions: list[Proposition]
    chunk_model: str = Field(default="claude-sonnet-4-6")


# ---------------------------------------------------------------------------
# LLM Structured-Output Schema
# (used as the tool schema passed to Claude for guaranteed JSON)
# ---------------------------------------------------------------------------

_EXTRACTION_TOOL = {
    "name": "extract_propositions",
    "description": (
        "Extract atomic propositions from an agent plan. "
        "Each proposition must be a single, indivisible action-on-subject pair."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "propositions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "action":     {"type": "string"},
                        "subject":    {"type": "string"},
                        "tier":       {"type": "string", "enum": ["compliance", "risk", "ops", "growth"]},
                        "rationale":  {"type": "string"},
                        "raw_text":   {"type": "string"},
                        "confidence": {"type": "number"},
                    },
                    "required": ["action", "subject", "tier", "rationale", "raw_text", "confidence"],
                },
            }
        },
        "required": ["propositions"],
    },
}

_SYSTEM_PROMPT = """\
You are the Propositional Chunker for the Aletheia Consensus Engine.

Your job: decompose an agent's plan into the MINIMUM set of atomic propositions.
Each proposition = one action + one subject + one priority tier.

Priority Tier Definitions (apply the HIGHEST applicable tier):
- compliance: Anything touching legal obligations, privacy regulations (GDPR, CCPA),
  audit requirements, or regulatory flags. ALWAYS overrides everything else.
- risk: Financial exposure, credit risk, fraud signals, security incidents,
  or any flag placed by a Risk/Legal team review.
- ops: Operational data integrity, system state changes, account configuration,
  or anything that modifies shared infrastructure.
- growth: Revenue actions (discounts, upsells, outreach), marketing campaigns,
  feature enablement for expansion.

Rules:
1. One proposition per action-subject pair. Never bundle two actions.
2. Assign the HIGHEST applicable tier. When in doubt, escalate upward.
3. raw_text must be a verbatim quote from the plan (or the closest paraphrase).
4. confidence is your certainty about the tier assignment (0.0–1.0).
5. Return ONLY propositions. Do not include meta-commentary.
"""


# ---------------------------------------------------------------------------
# Chunker
# ---------------------------------------------------------------------------

class PropositionalChunker:
    """
    Decomposes a free-text agent plan into structured Propositions using
    Claude's tool-use / structured-output capability.
    """

    def __init__(self, model: str = "claude-sonnet-4-6") -> None:
        self.model = model
        self._client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

    def chunk(self, agent_id: str, plan_description: str) -> ChunkResult:
        """
        Decompose `plan_description` into atomic Propositions.

        Args:
            agent_id: Stable identifier for the calling agent.
            plan_description: Free-text description of what the agent wants to do.

        Returns:
            ChunkResult containing all extracted Propositions.
        """
        response = self._client.messages.create(
            model=self.model,
            max_tokens=2048,
            system=_SYSTEM_PROMPT,
            tools=[_EXTRACTION_TOOL],
            tool_choice={"type": "tool", "name": "extract_propositions"},
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Agent ID: {agent_id}\n\n"
                        f"Plan:\n{plan_description}"
                    ),
                }
            ],
        )

        # Extract tool_use block — guaranteed by tool_choice={"type":"tool"}
        tool_block = next(
            block for block in response.content if block.type == "tool_use"
        )
        raw_props: list[dict] = tool_block.input["propositions"]

        propositions = []
        for p in raw_props:
            seed = f"{agent_id}:{p['action']}:{p['subject']}"
            prop_id = hashlib.sha256(seed.encode()).hexdigest()[:16]
            propositions.append(
                Proposition(
                    id=prop_id,
                    agent_id=agent_id,
                    tier=PriorityTier(p["tier"]),
                    action=p["action"],
                    subject=p["subject"],
                    rationale=p["rationale"],
                    raw_text=p["raw_text"],
                    confidence=p["confidence"],
                )
            )

        return ChunkResult(
            agent_id=agent_id,
            original_plan=plan_description,
            propositions=propositions,
            chunk_model=self.model,
        )

    def chunk_mock(self, agent_id: str, plan_description: str, propositions_data: list[dict]) -> ChunkResult:
        """
        Mock version for testing — bypasses LLM call with pre-defined propositions.
        Used in tests/test_conflict.py to avoid API cost during CI.
        """
        propositions = []
        for p in propositions_data:
            seed = f"{agent_id}:{p['action']}:{p['subject']}"
            prop_id = hashlib.sha256(seed.encode()).hexdigest()[:16]
            propositions.append(
                Proposition(
                    id=prop_id,
                    agent_id=agent_id,
                    tier=PriorityTier(p["tier"]),
                    action=p["action"],
                    subject=p["subject"],
                    rationale=p["rationale"],
                    raw_text=p["raw_text"],
                    confidence=p.get("confidence", 0.95),
                )
            )
        return ChunkResult(
            agent_id=agent_id,
            original_plan=plan_description,
            propositions=propositions,
        )
