"""
tests/test_conflict.py — Conflict Simulation Suite for Aletheia

Scenario:
  - Legal Agent submits a "Data Privacy Review" (Risk/Compliance tier) on account ACME-Corp.
  - Sales Agent then tries to apply a 15% discount to the same account (Growth tier).
  - Expected outcome: Sales Agent is BLOCKED. The Legal proposition is preserved in the KG.

Additional scenarios covered:
  - Duplicate intent detection (Merged)
  - Clean approval with no conflicts (Approved)
  - Multi-proposition plan with mixed tiers
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Allow running from the repo root: python -m tests.test_conflict
sys.path.insert(0, str(Path(__file__).parent.parent))

# Reset KG before tests to ensure isolation
from src.deduper.engine import _KG_PATH

def _reset_kg() -> None:
    if _KG_PATH.exists():
        _KG_PATH.unlink()


from src.mcp_server.gateway import ConsensusGateway

GATEWAY = ConsensusGateway()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _print_ticket(label: str, ticket: dict) -> None:
    status = ticket["status"]
    symbols = {"Approved": "✅", "Blocked": "🚫", "Merged": "🔀"}
    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"  Ticket : {ticket['ticket_id']}")
    print(f"  Agent  : {ticket['agent_id']}")
    print(f"  Status : {symbols.get(status, '?')} {status}")
    if ticket.get("veto_reason"):
        print(f"  Veto   : {ticket['veto_reason']}")
    if ticket.get("merged_plan"):
        print(f"  Merge  :\n{ticket['merged_plan']}")
    if ticket.get("blocking_proposition"):
        bp = ticket["blocking_proposition"]
        print(f"  Blocker: [{bp['tier'].upper()}] {bp['agent_id']} → {bp['action']} on {bp['subject']}")
    print(f"{'='*60}")


def _assert(condition: bool, message: str) -> None:
    if condition:
        print(f"  [PASS] {message}")
    else:
        print(f"  [FAIL] {message}")
        raise AssertionError(message)


# ---------------------------------------------------------------------------
# Test Cases
# ---------------------------------------------------------------------------

def test_legal_blocks_sales_discount():
    """
    PRIMARY SCENARIO:
    Legal Agent flags ACME-Corp for Data Privacy Review (Risk tier).
    Sales Agent tries to apply_discount on ACME-Corp (Growth tier).
    Expected: Sales Agent BLOCKED.
    """
    print("\n" + "█"*60)
    print("  TEST 1: Legal Compliance Veto Blocks Sales Discount")
    print("█"*60)

    _reset_kg()

    # Step 1: Legal Agent submits its compliance/risk proposition first
    legal_ticket = GATEWAY.submit_mock(
        agent_id="legal-compliance-agent",
        plan_description=(
            "Account ACME-Corp has been flagged for a Data Privacy Review under CCPA. "
            "All outbound commercial activity must be suspended until review completes."
        ),
        propositions_data=[
            {
                "action":    "flag_data_privacy_review",
                "subject":   "account:ACME-Corp",
                "tier":      "compliance",
                "rationale": "CCPA data privacy review mandated; all commercial activity suspended.",
                "raw_text":  "Account ACME-Corp has been flagged for a Data Privacy Review under CCPA.",
                "confidence": 0.98,
            }
        ],
    )

    _print_ticket("Legal Agent — Data Privacy Review Submission", legal_ticket)
    _assert(legal_ticket["status"] == "Approved", "Legal agent's compliance flag should be Approved")

    # Step 2: Sales Agent tries to apply a discount to the same account
    sales_ticket = GATEWAY.submit_mock(
        agent_id="sales-agent-v3",
        plan_description=(
            "Offer ACME-Corp a 15% renewal discount to prevent churn. "
            "Account shows high churn risk — apply discount and schedule a follow-up call."
        ),
        propositions_data=[
            {
                "action":    "apply_discount",
                "subject":   "account:ACME-Corp",
                "tier":      "growth",
                "rationale": "15% renewal discount to prevent churn on high-value account.",
                "raw_text":  "Offer ACME-Corp a 15% renewal discount to prevent churn.",
                "confidence": 0.95,
            },
            {
                "action":    "schedule_outreach",
                "subject":   "account:ACME-Corp",
                "tier":      "growth",
                "rationale": "Follow-up call to discuss renewal terms.",
                "raw_text":  "Schedule a follow-up call.",
                "confidence": 0.92,
            },
        ],
    )

    _print_ticket("Sales Agent — Discount & Outreach Attempt (should be BLOCKED)", sales_ticket)

    _assert(sales_ticket["status"] == "Blocked", "Sales discount must be BLOCKED by compliance hold")
    _assert(
        sales_ticket.get("blocking_proposition") is not None,
        "Ticket must identify the blocking proposition",
    )
    _assert(
        sales_ticket["blocking_proposition"]["agent_id"] == "legal-compliance-agent",
        "Blocker must be the legal-compliance-agent",
    )
    _assert(
        sales_ticket["blocking_proposition"]["tier"] == "compliance",
        "Blocker tier must be 'compliance'",
    )

    # Step 3: Verify merged data — Legal's proposition is still in KG, Sales' is NOT
    from src.deduper.engine import _load_kg
    kg_entries = _load_kg()
    kg_agents = {p.agent_id for p in kg_entries}

    _assert("legal-compliance-agent" in kg_agents, "Legal agent's proposition must persist in KG")
    _assert("sales-agent-v3" not in kg_agents, "Sales agent's blocked proposition must NOT be in KG")

    print("\n  [PASS] Sales Agent correctly blocked. Legal hold preserved in Knowledge Graph.")


def test_duplicate_intent_merges():
    """
    Two Sales Agents independently decide to apply a discount to the same account.
    Expected: second submission is MERGED (deduplicated).
    """
    print("\n" + "█"*60)
    print("  TEST 2: Duplicate Intent Detection (Merge)")
    print("█"*60)

    _reset_kg()

    # First agent — gets Approved
    t1 = GATEWAY.submit_mock(
        agent_id="sales-agent-us",
        plan_description="Apply 10% discount to account:Beta-LLC for Q1 renewal.",
        propositions_data=[
            {
                "action":    "apply_discount",
                "subject":   "account:Beta-LLC",
                "tier":      "growth",
                "rationale": "Q1 renewal incentive.",
                "raw_text":  "Apply 10% discount to account:Beta-LLC for Q1 renewal.",
                "confidence": 0.95,
            }
        ],
    )
    _print_ticket("Sales Agent US — First Discount Submission", t1)
    _assert(t1["status"] == "Approved", "First discount should be Approved")

    # Second agent — exact same action+subject → Merged
    t2 = GATEWAY.submit_mock(
        agent_id="sales-agent-emea",
        plan_description="Offer account:Beta-LLC a discount to close Q1 deal.",
        propositions_data=[
            {
                "action":    "apply_discount",
                "subject":   "account:Beta-LLC",
                "tier":      "growth",
                "rationale": "Closing discount for Q1.",
                "raw_text":  "Offer account:Beta-LLC a discount to close Q1 deal.",
                "confidence": 0.93,
            }
        ],
    )
    _print_ticket("Sales Agent EMEA — Duplicate Discount Submission (should be MERGED)", t2)
    _assert(t2["status"] == "Merged", "Duplicate discount must be MERGED")
    _assert(t2.get("merged_plan") is not None, "Merged ticket must include a merge summary")

    print("\n  [PASS] Duplicate intent correctly deduplicated into a single canonical proposition.")


def test_clean_approval_no_conflict():
    """
    An Ops agent updates account config with no competing propositions.
    Expected: Approved.
    """
    print("\n" + "█"*60)
    print("  TEST 3: Clean Approval (No Conflict)")
    print("█"*60)

    _reset_kg()

    ticket = GATEWAY.submit_mock(
        agent_id="ops-agent-1",
        plan_description="Update CRM sync settings for account:NewCo to enable nightly batch export.",
        propositions_data=[
            {
                "action":    "update_account_config",
                "subject":   "account:NewCo",
                "tier":      "ops",
                "rationale": "Enable nightly CRM batch export as requested by customer.",
                "raw_text":  "Update CRM sync settings for account:NewCo to enable nightly batch export.",
                "confidence": 0.97,
            }
        ],
    )

    _print_ticket("Ops Agent — Config Update (should be Approved)", ticket)
    _assert(ticket["status"] == "Approved", "Ops config update with no conflicts should be Approved")
    print("\n  [PASS] Clean approval works correctly.")


def test_risk_blocks_growth_below_compliance():
    """
    Risk Agent places a credit hold on account:Globex.
    Growth Agent tries to schedule an upsell campaign on the same account.
    Expected: Growth agent BLOCKED by Risk tier.
    """
    print("\n" + "█"*60)
    print("  TEST 4: Risk Tier Blocks Growth Action")
    print("█"*60)

    _reset_kg()

    # Risk agent places hold
    risk_ticket = GATEWAY.submit_mock(
        agent_id="risk-agent-credit",
        plan_description="Place credit hold on account:Globex due to 90-day overdue invoice.",
        propositions_data=[
            {
                "action":    "place_hold",
                "subject":   "account:Globex",
                "tier":      "risk",
                "rationale": "90-day overdue invoice — credit hold required by Risk policy.",
                "raw_text":  "Place credit hold on account:Globex due to 90-day overdue invoice.",
                "confidence": 0.99,
            }
        ],
    )
    _print_ticket("Risk Agent — Credit Hold", risk_ticket)
    _assert(risk_ticket["status"] == "Approved", "Risk hold should be Approved")

    # Growth agent tries upsell
    growth_ticket = GATEWAY.submit_mock(
        agent_id="growth-agent-upsell",
        plan_description="Add account:Globex to the Enterprise upsell campaign.",
        propositions_data=[
            {
                "action":    "add_to_campaign",
                "subject":   "account:Globex",
                "tier":      "growth",
                "rationale": "Enterprise tier upsell opportunity identified.",
                "raw_text":  "Add account:Globex to the Enterprise upsell campaign.",
                "confidence": 0.91,
            }
        ],
    )
    _print_ticket("Growth Agent — Upsell Campaign (should be BLOCKED)", growth_ticket)
    _assert(growth_ticket["status"] == "Blocked", "Growth action must be BLOCKED by risk hold")
    print("\n  [PASS] Risk tier correctly blocks growth action.")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_all_tests() -> None:
    tests = [
        test_legal_blocks_sales_discount,
        test_duplicate_intent_merges,
        test_clean_approval_no_conflict,
        test_risk_blocks_growth_below_compliance,
    ]

    passed = 0
    failed = 0

    for test_fn in tests:
        try:
            test_fn()
            passed += 1
        except AssertionError as e:
            failed += 1
            print(f"\n  !! ASSERTION FAILED: {e}")
        except Exception as e:
            failed += 1
            print(f"\n  !! UNEXPECTED ERROR in {test_fn.__name__}: {e}")
            import traceback
            traceback.print_exc()

    print("\n" + "="*60)
    print(f"  RESULTS: {passed} passed, {failed} failed out of {len(tests)} tests")
    print("="*60)

    if failed:
        sys.exit(1)


if __name__ == "__main__":
    run_all_tests()
