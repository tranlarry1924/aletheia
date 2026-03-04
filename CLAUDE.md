# Aletheia — Agentic Consensus Engine

## Project Purpose
Aletheia solves "Agent Sprawl" by providing a consensus layer that intercepts, deduplicates, and arbitrates conflicting agent intents before they execute in the real world.

## Tech Stack
- **Runtime**: Python 3.12+
- **Orchestration**: LangGraph (state machine for consensus pipeline)
- **MCP**: Anthropic MCP SDK (gateway for agent tool calls)
- **Vector Store**: Qdrant (local mode, `data/vectors/`)
- **Schema Validation**: Pydantic v2
- **LLM**: Anthropic Claude (claude-sonnet-4-6) via `anthropic` SDK

## Core Invariant
**Compliance > Risk > Data Integrity > Performance**

All conflict resolution MUST respect this priority order. A lower-priority intent can NEVER override a higher-priority veto.

## Architecture
```
Agent (MCP Tool Call)
    │
    ▼
MCP Gateway (src/mcp_server/gateway.py)
    │  submit_agent_intent(agent_id, plan_description)
    ▼
Propositional Chunker (src/deduper/chunker.py)
    │  Decomposes plan into atomic Proposition objects
    ▼
Consensus Engine (src/deduper/engine.py)  [LangGraph]
    │  Ingest → Semantic Check → Priority Veto
    ▼
Priority Resolver (src/ontology/resolver.py)
    │  Applies 4-tier ontology from src/ontology/rules.json
    ▼
Consensus_Ticket { status: Approved | Blocked | Merged }
```

## Directory Layout
```
src/
  deduper/
    chunker.py     # LLM-based propositional chunker
    engine.py      # LangGraph consensus state machine
  ontology/
    rules.json     # 4-tier priority hierarchy definitions
    resolver.py    # Conflict resolution logic
  mcp_server/
    gateway.py     # MCP server exposing submit_agent_intent
data/
  vectors/         # Qdrant local storage (auto-managed)
tests/
  test_conflict.py # Conflict simulation test suite
```

## Key Data Models
- `Proposition`: Atomic unit of agent intent (id, agent_id, tier, action, subject, raw_text)
- `ConsensusTicket`: Resolution result (ticket_id, status, propositions, veto_reason, merged_plan)
- `ConflictState`: LangGraph state dict flowing through the pipeline

## Development Rules
1. Never bypass the Priority Invariant — Compliance vetoes always win.
2. All LLM calls use structured output (Pydantic) — no free-form JSON parsing.
3. Vector similarity threshold for "duplicate" detection: cosine ≥ 0.85.
4. MCP tools must be idempotent — same input always produces same ticket category.
5. Tests must cover the Sales vs Legal conflict scenario explicitly.
