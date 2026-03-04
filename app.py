import re
import json
import os
import streamlit as st
from datetime import datetime
from src.mcp_server.gateway import ConsensusGateway
from src.deduper.engine import _load_kg

# ── Engine ────────────────────────────────────────────────────────────────────
_gateway = ConsensusGateway()

_AGENT_TIER = {
    "Sales-01":     "growth",
    "Legal-Bot":    "compliance",
    "Finance-Alpha":"risk",
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

def _submit(agent: str, intent: str) -> dict:
    ticket = _gateway.submit_mock(
        agent_id=agent, plan_description=intent,
        propositions_data=_build_proposition(agent, intent),
    )
    return {
        "time":   datetime.now().strftime("%H:%M"),
        "agent":  ticket["agent_id"],
        "msg":    intent,
        "status": ticket["status"].upper(),
        "reason": ticket.get("veto_reason") or ticket.get("merged_plan") or "No conflict detected.",
    }

# ── Page Config ───────────────────────────────────────────────────────────────
st.set_page_config(page_title="Aletheia Governance", layout="wide")

# ── App State ─────────────────────────────────────────────────────────────────
if "manual_logs" not in st.session_state:
    st.session_state.manual_logs = []

st.title("🛡️ Aletheia: Hybrid Governance Control")
st.divider()

col_manual, col_auto = st.columns(2)

# ── LEFT: Manual Sandbox ──────────────────────────────────────────────────────
with col_manual:
    st.subheader("🛠️ Manual Simulation")
    with st.form("sim_form", clear_on_submit=True):
        agent     = st.selectbox("Simulate Agent", ["Sales-01", "Legal-Bot", "Finance-Alpha"])
        intent    = st.text_input("Proposed Action")
        submitted = st.form_submit_button("Run Simulation")

    if submitted:
        if intent:
            log = _submit(agent, intent)
            st.session_state.manual_logs.insert(0, log)
        else:
            st.warning("Enter an intent first.")

    for log in st.session_state.manual_logs[:5]:
        color = "green" if log["status"] == "APPROVED" else ("red" if log["status"] == "BLOCKED" else "orange")
        st.write(f"👤 **{log['agent']}**: {log['msg']} → :{color}[{log['status']}]")
        if log["status"] == "BLOCKED":
            st.caption(f"   ↳ {log['reason']}")

# ── RIGHT: Automated Feed (fragment reruns independently every 2 s) ────────────
@st.fragment(run_every=2)
def automated_feed():
    st.subheader("📡 Automated Production Feed")
    st.caption("Reading live from `mock_logs.json` — refreshes every 2 s.")

    if os.path.exists("mock_logs.json"):
        with open("mock_logs.json", "r") as f:
            lines = [l.strip() for l in f.readlines() if l.strip()]
        for line in reversed(lines[-10:]):
            try:
                log     = json.loads(line)
                a_color = "green" if log["status"] == "APPROVED" else ("red" if log["status"] == "BLOCKED" else "orange")
                st.write(f"🤖 **{log['agent']}**: {log['msg']} → :{a_color}[{log['status']}]")
            except json.JSONDecodeError:
                continue
    else:
        st.info("No automated data yet. In a second terminal run:\n\n`python scripts/mock_feed.py`")

with col_auto:
    automated_feed()
