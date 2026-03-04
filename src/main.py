"""
Aletheia — Interactive CLI

Usage:
  python src/main.py --mode=live    # Uses real Claude LLM for chunking
  python src/main.py --mode=mock    # Uses pre-defined propositions (no API cost)
  python src/main.py --mode=demo    # Runs the Sales vs Legal scenario automatically
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# ── ensure project root is on sys.path when run as a script ──────────────────
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.deduper.chunker import PropositionalChunker
from src.deduper.engine import ConsensusEngine, _KG_PATH, _load_kg
from src.mcp_server.gateway import ConsensusGateway, _format_ticket


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

BANNER = """
╔══════════════════════════════════════════════════════════════╗
║          ALETHEIA  —  Agentic Consensus Engine               ║
║          Priority Invariant: Compliance > Risk > Ops > Growth║
╚══════════════════════════════════════════════════════════════╝
"""

def _check_api_key() -> None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("[ERROR] ANTHROPIC_API_KEY is not set.")
        print("  export ANTHROPIC_API_KEY='sk-ant-...'")
        sys.exit(1)


def _print_kg() -> None:
    entries = _load_kg()
    if not entries:
        print("  (Knowledge Graph is empty)")
        return
    print(f"  {len(entries)} proposition(s) in Knowledge Graph:")
    for p in entries:
        print(f"    [{p.tier.value.upper():12s}] {p.agent_id:30s}  {p.action} → {p.subject}")


def _reset_kg() -> None:
    if _KG_PATH.exists():
        _KG_PATH.unlink()
    _KG_PATH.write_text("[]")
    print("  Knowledge Graph cleared.")


# ─────────────────────────────────────────────────────────────────────────────
# Modes
# ─────────────────────────────────────────────────────────────────────────────

def run_live(gateway: ConsensusGateway) -> None:
    """Interactive REPL — type agent intents, get tickets back."""
    print(BANNER)
    print("  MODE: LIVE  (Claude LLM chunks your plan into propositions)\n")
    print("  Commands:  'kg' = show Knowledge Graph   'reset' = clear KG   'quit' = exit\n")

    while True:
        try:
            agent_id = input("Agent ID  > ").strip()
            if agent_id.lower() in ("quit", "exit", "q"):
                break
            if agent_id.lower() == "kg":
                _print_kg()
                continue
            if agent_id.lower() == "reset":
                _reset_kg()
                continue
            if not agent_id:
                continue

            plan = input("Plan      > ").strip()
            if not plan:
                continue

            print("\n  Chunking plan with Claude...\n")
            ticket = gateway.submit(agent_id=agent_id, plan_description=plan)
            print(_format_ticket(ticket))
            print()

        except KeyboardInterrupt:
            print("\n  Interrupted.")
            break
        except Exception as e:
            print(f"\n  [ERROR] {e}\n")


def run_mock(gateway: ConsensusGateway) -> None:
    """Interactive REPL — manually specify propositions (no LLM call)."""
    print(BANNER)
    print("  MODE: MOCK  (you define propositions directly, no API cost)\n")
    print("  Tiers: compliance | risk | ops | growth\n")
    print("  Commands:  'kg' = show KG   'reset' = clear KG   'quit' = exit\n")

    while True:
        try:
            agent_id = input("Agent ID  > ").strip()
            if agent_id.lower() in ("quit", "exit", "q"):
                break
            if agent_id.lower() == "kg":
                _print_kg()
                continue
            if agent_id.lower() == "reset":
                _reset_kg()
                continue
            if not agent_id:
                continue

            plan = input("Plan desc > ").strip()
            print("  Proposition (JSON list) — e.g.")
            print('  [{"action":"apply_discount","subject":"account:X","tier":"growth","rationale":"..","raw_text":".."}]')
            raw = input("  Props     > ").strip()

            try:
                props = json.loads(raw)
            except json.JSONDecodeError as e:
                print(f"  [ERROR] Invalid JSON: {e}\n")
                continue

            ticket = gateway.submit_mock(
                agent_id=agent_id,
                plan_description=plan,
                propositions_data=props,
            )
            print(_format_ticket(ticket))
            print()

        except KeyboardInterrupt:
            print("\n  Interrupted.")
            break
        except Exception as e:
            print(f"\n  [ERROR] {e}\n")


def run_demo(gateway: ConsensusGateway) -> None:
    """
    Automated demo: Legal Agent → Sales Agent conflict (the primary scenario).
    Runs without any user input, prints the full ticket chain.
    """
    print(BANNER)
    print("  MODE: DEMO  — Sales vs Legal conflict (uses real LLM)\n")

    _reset_kg()

    scenarios = [
        {
            "label": "Step 1 — Legal Agent flags ACME-Corp for Data Privacy Review",
            "agent_id": "legal-compliance-agent",
            "plan": (
                "ACME-Corp has been flagged under a CCPA Data Privacy Review. "
                "All commercial outreach, discounts, and account modifications must be "
                "suspended immediately until the review is closed by the Legal team."
            ),
        },
        {
            "label": "Step 2 — Sales Agent tries to offer a renewal discount",
            "agent_id": "sales-agent-v3",
            "plan": (
                "ACME-Corp is showing high churn signals. I want to apply a 15% renewal "
                "discount, send a re-engagement email, and schedule a call with their VP "
                "of Operations to discuss contract renewal terms."
            ),
        },
        {
            "label": "Step 3 — Ops Agent updates unrelated account (should be Approved)",
            "agent_id": "ops-agent-1",
            "plan": (
                "Update the CRM sync configuration for account NewCo to enable "
                "nightly data export to their data warehouse."
            ),
        },
    ]

    for step in scenarios:
        print(f"  {'─'*56}")
        print(f"  {step['label']}")
        print(f"  Agent : {step['agent_id']}")
        print(f"  Plan  : {step['plan'][:80]}{'...' if len(step['plan']) > 80 else ''}")
        print()
        print("  Sending to Consensus Engine...\n")

        ticket = gateway.submit(agent_id=step["agent_id"], plan_description=step["plan"])
        print(_format_ticket(ticket))
        print()

    print("  Final Knowledge Graph state:")
    _print_kg()
    print()


# ─────────────────────────────────────────────────────────────────────────────
# Entrypoint
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="aletheia",
        description="Aletheia Agentic Consensus Engine",
    )
    parser.add_argument(
        "--mode",
        choices=["live", "mock", "demo"],
        default="live",
        help="live=LLM chunker, mock=manual props, demo=automated scenario",
    )
    args = parser.parse_args()

    if args.mode in ("live", "demo"):
        _check_api_key()

    gateway = ConsensusGateway()

    if args.mode == "live":
        run_live(gateway)
    elif args.mode == "mock":
        run_mock(gateway)
    elif args.mode == "demo":
        run_demo(gateway)


if __name__ == "__main__":
    main()
