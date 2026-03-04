"""
Mock automated agent feed — writes real Aletheia decisions to mock_logs.json
every 3 seconds, simulating a live production stream.

Run in a second terminal while the Streamlit app is open:
  python scripts/mock_feed.py
"""

import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.mcp_server.gateway import ConsensusGateway

_gateway = ConsensusGateway()

_AGENT_TIER = {
    "API_System": "ops",
    "Ops_Bot":    "ops",
    "Risk_Alpha": "risk",
    "Legal_Bot":  "compliance",
    "Sales_Bot":  "growth",
}

# Rotating queue of realistic background agent intents
_INTENTS = [
    ("Legal_Bot",  "Flag Globex for CCPA Data Privacy Review"),
    ("Sales_Bot",  "Apply 15% discount to Globex renewal"),       # should BLOCK
    ("Ops_Bot",    "Sync CRM records for Initech overnight batch"),
    ("Risk_Alpha", "Place credit hold on Vandelay Industries"),
    ("Sales_Bot",  "Schedule upsell call with Vandelay Industries"), # should BLOCK
    ("API_System", "Update price list for Umbrella Corp"),
    ("Sales_Bot",  "Send renewal offer to Initech"),
    ("Legal_Bot",  "Flag Initech for compliance audit"),
    ("Sales_Bot",  "Apply discount to Initech contract"),          # should BLOCK
    ("Ops_Bot",    "Archive stale accounts older than 180 days"),
]

def _build_proposition(agent: str, intent: str) -> list[dict]:
    words   = re.sub(r"[^a-z0-9 ]", "", intent.lower()).split()
    action  = "_".join(words[:2]) if len(words) >= 2 else (words[0] if words else "act")
    match   = re.search(r"[A-Z][A-Za-z0-9\-]+(?:\s[A-Z][A-Za-z]+)*", intent)
    subject = f"account:{match.group(0).replace(' ', '-')}" if match else "account:unknown"
    return [{
        "action": action[:40], "subject": subject,
        "tier": _AGENT_TIER.get(agent, "growth"),
        "rationale": intent[:120], "raw_text": intent, "confidence": 0.90,
    }]

def main() -> None:
    log_path = Path("mock_logs.json")
    print(f"[mock_feed] Writing to {log_path.resolve()}")
    print("[mock_feed] Ctrl+C to stop\n")

    for i, (agent, intent) in enumerate(_INTENTS):
        ticket = _gateway.submit_mock(
            agent_id=agent,
            plan_description=intent,
            propositions_data=_build_proposition(agent, intent),
        )
        entry = {
            "time":      datetime.now().strftime("%H:%M:%S"),
            "agent":     agent,
            "msg":       intent,
            "status":    ticket["status"].upper(),
            "reason":    ticket.get("veto_reason") or "No conflict detected.",
            "ticket_id": ticket["ticket_id"],
        }
        with log_path.open("a") as f:
            f.write(json.dumps(entry) + "\n")

        symbol = {"APPROVED": "✅", "BLOCKED": "🚫", "MERGED": "🔀"}.get(entry["status"], "?")
        print(f"{symbol}  [{entry['time']}] {agent}: {intent}")
        if entry["status"] == "BLOCKED":
            print(f"   ↳ {entry['reason']}")

        time.sleep(3)

    print("\n[mock_feed] All intents processed.")

if __name__ == "__main__":
    main()
