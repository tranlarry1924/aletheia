import re
import json
import time
from datetime import datetime
from src.mcp_server.gateway import ConsensusGateway  # high-level API wrapping ConsensusEngine

_gateway = ConsensusGateway()

_AGENT_TIER = {
    "Sales-Bot":  "growth",
    "Risk-Alpha": "risk",
}

def _build_proposition(agent: str, intent: str) -> list[dict]:
    words  = re.sub(r"[^a-z0-9 ]", "", intent.lower()).split()
    action = "_".join(words[:2]) if len(words) >= 2 else (words[0] if words else "act")
    match  = re.search(r"[A-Z][A-Za-z0-9\-]+(?:\s[A-Z][A-Za-z]+)*", intent)
    subject = f"account:{match.group(0).replace(' ', '-')}" if match else "account:unknown"
    return [{
        "action": action[:40], "subject": subject,
        "tier": _AGENT_TIER.get(agent, "growth"),
        "rationale": intent[:120], "raw_text": intent, "confidence": 0.90,
    }]

def run_mock_test():
    print("🤖 Starting Mock Agent Automated Test...")

    automated_intents = [
        {"agent": "Sales-Bot",  "intent": "Increase limit for Globex",  "priority": "High"},
        {"agent": "Risk-Alpha", "intent": "Audit request for Initech",   "priority": "Standard"},
    ]

    for task in automated_intents:
        ticket = _gateway.submit_mock(
            agent_id=task["agent"],
            plan_description=task["intent"],
            propositions_data=_build_proposition(task["agent"], task["intent"]),
        )

        log_entry = {
            "time":      datetime.now().strftime("%H:%M:%S"),
            "agent":     task["agent"],
            "msg":       task["intent"],
            "status":    ticket["status"].upper(),
            "reason":    ticket.get("veto_reason") or "No conflict detected.",
            "ticket_id": ticket["ticket_id"],
            "source":    "AUTOMATED_MOCK",
        }

        with open("mock_logs.json", "a") as f:
            f.write(json.dumps(log_entry) + "\n")

        print(f"✅ Processed {task['agent']} intent via KG: {ticket['status']}")
        time.sleep(1.5)

if __name__ == "__main__":
    run_mock_test()
