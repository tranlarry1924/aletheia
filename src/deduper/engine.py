"""
Consensus Engine — LangGraph state machine that arbitrates conflicting agent intents.

Pipeline:
  INGEST → SEMANTIC_CHECK → PRIORITY_VETO → EMIT_TICKET

- INGEST:          Normalise incoming propositions into the graph state.
- SEMANTIC_CHECK:  Late Interaction check — does a near-duplicate already exist in
                   the Knowledge Graph?  Cosine similarity >= 0.85 triggers merge.
- PRIORITY_VETO:   Apply 4-tier ontology.  A higher-priority proposition blocks or
                   transforms a lower-priority one.
- EMIT_TICKET:     Serialise final ConsensusTicket.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Literal, Optional, TypedDict

from langgraph.graph import END, StateGraph

from src.deduper.chunker import ChunkResult, Proposition, PriorityTier
from src.ontology.resolver import PriorityResolver, ResolutionResult


# ---------------------------------------------------------------------------
# Ticket model
# ---------------------------------------------------------------------------

class ConsensusTicket(TypedDict):
    ticket_id: str
    status: Literal["Approved", "Blocked", "Merged"]
    agent_id: str
    propositions: list[dict]          # serialised Proposition dicts
    veto_reason: Optional[str]
    merged_plan: Optional[str]
    blocking_proposition: Optional[dict]
    timestamp: float


# ---------------------------------------------------------------------------
# LangGraph State
# ---------------------------------------------------------------------------

class ConflictState(TypedDict):
    # Input
    incoming: ChunkResult

    # Populated during SEMANTIC_CHECK
    existing_propositions: list[Proposition]   # from KG / vector store
    semantic_duplicates: list[tuple[Proposition, Proposition, float]]  # (new, existing, score)

    # Populated during PRIORITY_VETO
    resolution: Optional[ResolutionResult]

    # Output
    ticket: Optional[ConsensusTicket]
    error: Optional[str]


# ---------------------------------------------------------------------------
# Mock Knowledge Graph
# Simulates a persistent store of already-approved agent propositions.
# In production this would be a Qdrant collection with dense embeddings.
# ---------------------------------------------------------------------------

_KG_PATH = Path(__file__).parent.parent.parent / "data" / "knowledge_graph.json"


def _load_kg() -> list[Proposition]:
    """Load persisted Knowledge Graph entries."""
    if not _KG_PATH.exists():
        return []
    raw = json.loads(_KG_PATH.read_text())
    return [Proposition(**p) for p in raw]


def _save_to_kg(propositions: list[Proposition]) -> None:
    """Persist approved propositions to the Knowledge Graph."""
    _KG_PATH.parent.mkdir(parents=True, exist_ok=True)
    existing = _load_kg()
    # deduplicate by id
    ids = {p.id for p in existing}
    new = [p for p in propositions if p.id not in ids]
    all_props = existing + new
    _KG_PATH.write_text(json.dumps([p.model_dump() for p in all_props], indent=2))


# ---------------------------------------------------------------------------
# Late Interaction Similarity (mock cosine — no GPU required)
# In production: use Qdrant's ColBERT / maxsim late-interaction retrieval.
# ---------------------------------------------------------------------------

def _action_subject_similarity(a: Proposition, b: Proposition) -> float:
    """
    Lightweight token-overlap similarity used in lieu of dense embeddings for
    the mock/test path.  Production replaces this with Qdrant ColBERT search.

    Returns 1.0 for exact (action, subject) match, partial overlap otherwise.
    """
    if a.action == b.action and a.subject == b.subject:
        return 1.0

    def tokens(s: str) -> set[str]:
        return set(s.lower().replace("_", " ").replace("-", " ").split())

    act_a, act_b = tokens(a.action), tokens(b.action)
    sub_a, sub_b = tokens(a.subject), tokens(b.subject)

    def jaccard(x: set, y: set) -> float:
        return len(x & y) / len(x | y) if x | y else 0.0

    return (jaccard(act_a, act_b) + jaccard(sub_a, sub_b)) / 2.0


SIMILARITY_THRESHOLD = 0.85


# ---------------------------------------------------------------------------
# Node Functions
# ---------------------------------------------------------------------------

def node_ingest(state: ConflictState) -> ConflictState:
    """
    INGEST node: validate and enrich incoming ChunkResult.
    Loads existing KG entries for downstream comparison.
    """
    state["existing_propositions"] = _load_kg()
    state["semantic_duplicates"] = []
    state["resolution"] = None
    state["ticket"] = None
    state["error"] = None
    return state


def node_semantic_check(state: ConflictState) -> ConflictState:
    """
    SEMANTIC_CHECK node: Late Interaction deduplication.

    For each incoming proposition, compare against every KG entry using the
    action-subject similarity function.  Matches above SIMILARITY_THRESHOLD
    are flagged as semantic duplicates that may need merging or blocking.
    """
    duplicates: list[tuple[Proposition, Proposition, float]] = []

    for incoming_prop in state["incoming"].propositions:
        for existing_prop in state["existing_propositions"]:
            score = _action_subject_similarity(incoming_prop, existing_prop)
            if score >= SIMILARITY_THRESHOLD:
                duplicates.append((incoming_prop, existing_prop, score))

    state["semantic_duplicates"] = duplicates
    return state


def node_priority_veto(state: ConflictState) -> ConflictState:
    """
    PRIORITY_VETO node: apply the 4-tier ontology.

    The resolver checks:
    1. Are any existing KG propositions at a higher tier than the incoming ones?
    2. Do the actions conflict (e.g. 'apply_discount' vs 'freeze_account')?
    If so, the higher-priority proposition wins — BLOCK or MERGE the incoming plan.
    """
    resolver = PriorityResolver()
    resolution = resolver.resolve(
        incoming=state["incoming"].propositions,
        existing=state["existing_propositions"],
        semantic_duplicates=state["semantic_duplicates"],
    )
    state["resolution"] = resolution
    return state


def node_emit_ticket(state: ConflictState) -> ConflictState:
    """
    EMIT_TICKET node: build the final ConsensusTicket.

    Approved:  No conflicts — persist new propositions to KG.
    Blocked:   Higher-priority veto — do not persist; return veto reason.
    Merged:    Semantic duplicate with same or lower tier — deduplicate and persist
               the canonical version.
    """
    resolution: ResolutionResult = state["resolution"]

    ticket: ConsensusTicket = {
        "ticket_id": f"TICKET-{uuid.uuid4().hex[:8].upper()}",
        "status": resolution.status,
        "agent_id": state["incoming"].agent_id,
        "propositions": [p.model_dump() for p in state["incoming"].propositions],
        "veto_reason": resolution.veto_reason,
        "merged_plan": resolution.merged_plan,
        "blocking_proposition": (
            resolution.blocking_proposition.model_dump()
            if resolution.blocking_proposition else None
        ),
        "timestamp": time.time(),
    }

    if resolution.status == "Approved":
        _save_to_kg(state["incoming"].propositions)
    elif resolution.status == "Merged":
        _save_to_kg(resolution.canonical_propositions or state["incoming"].propositions)

    state["ticket"] = ticket
    return state


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------

def _route_after_ingest(state: ConflictState) -> str:
    if state.get("error"):
        return "emit_ticket"
    return "semantic_check"


def _route_after_semantic(state: ConflictState) -> str:
    # Always proceed to priority veto regardless of duplicate findings;
    # the veto node decides the final status.
    return "priority_veto"


# ---------------------------------------------------------------------------
# Graph Construction
# ---------------------------------------------------------------------------

def build_consensus_graph() -> Any:
    """Compile and return the LangGraph consensus state machine."""
    builder = StateGraph(ConflictState)

    builder.add_node("ingest",         node_ingest)
    builder.add_node("semantic_check", node_semantic_check)
    builder.add_node("priority_veto",  node_priority_veto)
    builder.add_node("emit_ticket",    node_emit_ticket)

    builder.set_entry_point("ingest")

    builder.add_conditional_edges("ingest", _route_after_ingest, {
        "semantic_check": "semantic_check",
        "emit_ticket":    "emit_ticket",
    })
    builder.add_conditional_edges("semantic_check", _route_after_semantic, {
        "priority_veto": "priority_veto",
    })
    builder.add_edge("priority_veto", "emit_ticket")
    builder.add_edge("emit_ticket",   END)

    return builder.compile()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class ConsensusEngine:
    """High-level entry point used by the MCP Gateway."""

    def __init__(self) -> None:
        self._graph = build_consensus_graph()

    def process(self, chunk_result: ChunkResult) -> ConsensusTicket:
        """
        Run a ChunkResult through the full consensus pipeline.

        Args:
            chunk_result: Output of PropositionalChunker.chunk()

        Returns:
            ConsensusTicket with status Approved | Blocked | Merged.
        """
        initial_state: ConflictState = {
            "incoming": chunk_result,
            "existing_propositions": [],
            "semantic_duplicates": [],
            "resolution": None,
            "ticket": None,
            "error": None,
        }
        final_state = self._graph.invoke(initial_state)
        return final_state["ticket"]
