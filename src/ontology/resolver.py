"""
Priority Resolver — applies the 4-tier ontology to conflicting agent intents.

Resolution outcomes:
  Approved  — No conflicts; incoming propositions may proceed.
  Blocked   — A higher-priority proposition vetoes one or more incoming intents.
  Merged    — Semantic duplicate at same or lower tier; deduplicate into canonical form.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional

from src.deduper.chunker import Proposition, PriorityTier

_RULES_PATH = Path(__file__).parent / "rules.json"


# ---------------------------------------------------------------------------
# Data Classes
# ---------------------------------------------------------------------------

@dataclass
class ResolutionResult:
    status: Literal["Approved", "Blocked", "Merged"]
    veto_reason: Optional[str] = None
    merged_plan: Optional[str] = None
    blocking_proposition: Optional[Proposition] = None
    canonical_propositions: list[Proposition] = field(default_factory=list)
    applied_rule_id: Optional[str] = None


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------

class PriorityResolver:
    """
    Stateless resolver that evaluates incoming propositions against existing
    KG propositions using the rules defined in rules.json.

    The core invariant: Compliance > Risk > Ops > Growth.
    A lower-rank (higher-priority) existing proposition ALWAYS vetoes a
    higher-rank (lower-priority) incoming proposition on the same subject.
    """

    def __init__(self) -> None:
        self._rules = json.loads(_RULES_PATH.read_text())
        self._tier_rank: dict[str, int] = {
            t["name"]: t["rank"] for t in self._rules["tiers"]
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def resolve(
        self,
        incoming: list[Proposition],
        existing: list[Proposition],
        semantic_duplicates: list[tuple[Proposition, Proposition, float]],
    ) -> ResolutionResult:
        """
        Evaluate incoming propositions against existing KG entries.

        Args:
            incoming:           New propositions from the calling agent.
            existing:           All propositions currently in the KG.
            semantic_duplicates: Pairs (incoming_prop, existing_prop, similarity_score)
                                 flagged by the Semantic Check node.

        Returns:
            ResolutionResult with status and supporting detail.
        """

        # --- Step 1: Subject-scoped conflict detection ---
        # Build a map: subject → highest-priority existing proposition
        subject_to_existing: dict[str, Proposition] = {}
        for ep in existing:
            subject = ep.subject
            if subject not in subject_to_existing:
                subject_to_existing[subject] = ep
            elif ep.tier_rank < subject_to_existing[subject].tier_rank:
                subject_to_existing[subject] = ep

        # --- Step 2: Check each incoming proposition for vetoes ---
        for incoming_prop in incoming:
            subject = incoming_prop.subject
            if subject in subject_to_existing:
                existing_prop = subject_to_existing[subject]
                veto = self._check_conflict_rules(incoming_prop, existing_prop)
                if veto:
                    return ResolutionResult(
                        status="Blocked",
                        veto_reason=veto["message"],
                        blocking_proposition=existing_prop,
                        applied_rule_id=veto["id"],
                    )

        # --- Step 3: Check for merge candidates ---
        if semantic_duplicates:
            # All duplicates that aren't blocked become merges
            canonical = self._build_merged_propositions(incoming, semantic_duplicates)
            merged_summary = self._summarise_merge(incoming, semantic_duplicates)
            return ResolutionResult(
                status="Merged",
                merged_plan=merged_summary,
                canonical_propositions=canonical,
            )

        # --- Step 4: No conflicts, no duplicates → Approved ---
        return ResolutionResult(
            status="Approved",
            canonical_propositions=incoming,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _check_conflict_rules(
        self,
        incoming: Proposition,
        existing: Proposition,
    ) -> Optional[dict]:
        """
        Return the matching conflict rule dict if a veto applies, else None.

        A veto applies when:
        - existing.tier_rank < incoming.tier_rank   (existing is higher priority)
        - The pair's tiers match a conflict rule with policy "block"
        """
        if existing.tier_rank >= incoming.tier_rank:
            # Existing is same or lower priority — no veto
            return None

        for rule in self._rules["conflict_rules"]:
            if rule["policy"] != "block":
                continue
            if (
                rule["blocker_tier"] == existing.tier.value
                and rule["blocked_tier"] == incoming.tier.value
            ):
                return rule

        # Tiers differ but no explicit rule — apply the general invariant
        if existing.tier_rank < incoming.tier_rank:
            return {
                "id": "CR-GENERAL",
                "message": (
                    f"Proposition blocked by {existing.tier.value.upper()} priority rule. "
                    f"Agent '{existing.agent_id}' has a '{existing.action}' action on "
                    f"'{existing.subject}' that takes precedence over "
                    f"the incoming '{incoming.action}' intent."
                ),
                "policy": "block",
            }

        return None

    def _build_merged_propositions(
        self,
        incoming: list[Proposition],
        semantic_duplicates: list[tuple[Proposition, Proposition, float]],
    ) -> list[Proposition]:
        """
        For merge scenarios: prefer the existing (already-approved) proposition
        as canonical, and include any incoming propositions that are NOT duplicates.
        """
        duplicate_incoming_ids = {inc.id for inc, _, _ in semantic_duplicates}
        canonical = [
            existing for _, existing, _ in semantic_duplicates
        ]
        # De-duplicate canonical list
        seen = {p.id for p in canonical}
        unique_canonical = []
        for p in canonical:
            if p.id in seen:
                unique_canonical.append(p)
                seen.discard(p.id)

        # Add non-duplicate incoming propositions as new canonical entries
        for prop in incoming:
            if prop.id not in duplicate_incoming_ids:
                unique_canonical.append(prop)

        return unique_canonical

    def _summarise_merge(
        self,
        incoming: list[Proposition],
        semantic_duplicates: list[tuple[Proposition, Proposition, float]],
    ) -> str:
        """Human-readable summary of what was merged."""
        lines = ["Merged plan (canonical propositions retained from Knowledge Graph):"]
        for inc, exc, score in semantic_duplicates:
            lines.append(
                f"  • [{inc.action} / {inc.subject}] "
                f"(similarity={score:.2f}) → using existing entry "
                f"from agent '{exc.agent_id}'"
            )
        return "\n".join(lines)
