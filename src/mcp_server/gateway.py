"""
Aletheia MCP Gateway — exposes the Consensus Engine as an MCP server tool.

Tool surface:
  submit_agent_intent(agent_id, plan_description)
      → ConsensusTicket  { status: "Approved" | "Blocked" | "Merged", ... }

Run as a standalone MCP server:
  python -m src.mcp_server.gateway

Or import ConsensusGateway for programmatic use (e.g. in tests).
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import mcp.server.stdio
import mcp.types as types
from mcp.server import Server

from src.deduper.chunker import PropositionalChunker
from src.deduper.engine import ConsensusEngine, ConsensusTicket

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("aletheia.gateway")

# ---------------------------------------------------------------------------
# MCP Server Setup
# ---------------------------------------------------------------------------

app = Server("aletheia-consensus-engine")
_engine = ConsensusEngine()
_chunker = PropositionalChunker()


# ---------------------------------------------------------------------------
# Tool Definitions
# ---------------------------------------------------------------------------

@app.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="submit_agent_intent",
            description=(
                "Submit an agent's plan to the Aletheia Consensus Engine for arbitration. "
                "Returns a Consensus_Ticket indicating whether the plan is Approved, "
                "Blocked (vetoed by a higher-priority agent), or Merged (deduplicated "
                "with an existing identical intent)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "agent_id": {
                        "type": "string",
                        "description": (
                            "Stable, unique identifier for the agent submitting the intent. "
                            "Example: 'sales-agent-v2', 'legal-compliance-bot'"
                        ),
                    },
                    "plan_description": {
                        "type": "string",
                        "description": (
                            "Free-text description of what the agent intends to do. "
                            "The engine will decompose this into atomic propositions and "
                            "evaluate each against the priority ontology."
                        ),
                    },
                },
                "required": ["agent_id", "plan_description"],
            },
        ),
        types.Tool(
            name="get_knowledge_graph",
            description=(
                "Retrieve all currently approved propositions in the Aletheia "
                "Knowledge Graph. Useful for auditing the current consensus state."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
                "required": [],
            },
        ),
        types.Tool(
            name="reset_knowledge_graph",
            description=(
                "Clear all propositions from the Knowledge Graph. "
                "USE WITH CAUTION — this resets all consensus state. "
                "Intended for testing and development only."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "confirm": {
                        "type": "boolean",
                        "description": "Must be true to proceed with the reset.",
                    }
                },
                "required": ["confirm"],
            },
        ),
    ]


# ---------------------------------------------------------------------------
# Tool Handlers
# ---------------------------------------------------------------------------

@app.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[types.TextContent]:
    """Route MCP tool calls to the appropriate handler."""

    if name == "submit_agent_intent":
        return await _handle_submit_agent_intent(arguments)
    elif name == "get_knowledge_graph":
        return await _handle_get_knowledge_graph()
    elif name == "reset_knowledge_graph":
        return await _handle_reset_knowledge_graph(arguments)
    else:
        raise ValueError(f"Unknown tool: {name}")


async def _handle_submit_agent_intent(args: dict) -> list[types.TextContent]:
    agent_id: str = args["agent_id"]
    plan_description: str = args["plan_description"]

    logger.info("Received intent from agent '%s'", agent_id)

    # 1. Chunk the plan into propositions
    chunk_result = _chunker.chunk(agent_id=agent_id, plan_description=plan_description)
    logger.info(
        "Chunked plan into %d proposition(s): %s",
        len(chunk_result.propositions),
        [p.action for p in chunk_result.propositions],
    )

    # 2. Run through the Consensus Engine
    ticket: ConsensusTicket = _engine.process(chunk_result)

    logger.info(
        "Consensus_Ticket %s — Status: %s",
        ticket["ticket_id"],
        ticket["status"],
    )

    # 3. Format the response
    response = _format_ticket(ticket)
    return [types.TextContent(type="text", text=response)]


async def _handle_get_knowledge_graph() -> list[types.TextContent]:
    from src.deduper.engine import _load_kg
    kg = _load_kg()
    if not kg:
        return [types.TextContent(type="text", text="Knowledge Graph is empty.")]
    entries = [p.model_dump() for p in kg]
    return [types.TextContent(type="text", text=json.dumps(entries, indent=2))]


async def _handle_reset_knowledge_graph(args: dict) -> list[types.TextContent]:
    if not args.get("confirm"):
        return [types.TextContent(
            type="text",
            text="Reset aborted: 'confirm' must be true.",
        )]
    from src.deduper.engine import _KG_PATH
    if _KG_PATH.exists():
        _KG_PATH.unlink()
    return [types.TextContent(type="text", text="Knowledge Graph reset successfully.")]


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def _format_ticket(ticket: ConsensusTicket) -> str:
    """Render a ConsensusTicket as a human-readable summary."""
    status_emoji = {
        "Approved": "✅",
        "Blocked":  "🚫",
        "Merged":   "🔀",
    }.get(ticket["status"], "❓")

    lines = [
        f"╔══ CONSENSUS TICKET ══════════════════════════════════════",
        f"║  Ticket ID : {ticket['ticket_id']}",
        f"║  Agent     : {ticket['agent_id']}",
        f"║  Status    : {status_emoji}  {ticket['status'].upper()}",
        f"╠══════════════════════════════════════════════════════════",
    ]

    if ticket["status"] == "Blocked":
        lines.append(f"║  VETO REASON:")
        lines.append(f"║  {ticket['veto_reason']}")
        if ticket.get("blocking_proposition"):
            bp = ticket["blocking_proposition"]
            lines.append(f"║")
            lines.append(f"║  Blocking Proposition:")
            lines.append(f"║    Agent  : {bp['agent_id']}")
            lines.append(f"║    Tier   : {bp['tier'].upper()}")
            lines.append(f"║    Action : {bp['action']}")
            lines.append(f"║    Subject: {bp['subject']}")

    elif ticket["status"] == "Merged":
        lines.append(f"║  MERGE SUMMARY:")
        for line in (ticket.get("merged_plan") or "").split("\n"):
            lines.append(f"║  {line}")

    elif ticket["status"] == "Approved":
        lines.append(f"║  Approved propositions:")
        for p in ticket["propositions"]:
            lines.append(f"║    [{p['tier'].upper()}] {p['action']} → {p['subject']}")

    lines.append(f"╚══════════════════════════════════════════════════════════")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Programmatic API (used by tests without spinning up MCP transport)
# ---------------------------------------------------------------------------

class ConsensusGateway:
    """
    Thin wrapper around PropositionalChunker + ConsensusEngine for direct
    programmatic use (tests, CLI scripts, FastAPI endpoints).
    """

    def __init__(
        self,
        chunker: PropositionalChunker | None = None,
        engine: ConsensusEngine | None = None,
    ) -> None:
        self._chunker = chunker or PropositionalChunker()
        self._engine  = engine  or ConsensusEngine()

    def submit(self, agent_id: str, plan_description: str) -> ConsensusTicket:
        """
        Submit an agent intent and return a ConsensusTicket.
        Uses the live LLM chunker — for tests use submit_mock().
        """
        chunk_result = self._chunker.chunk(agent_id, plan_description)
        return self._engine.process(chunk_result)

    def submit_mock(
        self,
        agent_id: str,
        plan_description: str,
        propositions_data: list[dict],
    ) -> ConsensusTicket:
        """
        Submit an intent with pre-defined propositions (bypasses LLM call).
        Designed for deterministic unit tests.
        """
        chunk_result = self._chunker.chunk_mock(agent_id, plan_description, propositions_data)
        return self._engine.process(chunk_result)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

async def main() -> None:
    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        await app.run(
            read_stream,
            write_stream,
            app.create_initialization_options(),
        )


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
