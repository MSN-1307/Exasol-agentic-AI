"""
Thin live-trace UI.

    streamlit run ui/app.py

Left: ask a question, watch plan -> gap detected -> UDF drafted ->
validated -> registered -> answered stream in live.
Right: the running toolkit — every self-written UDF registered so far,
pulled straight from Exasol's registry table.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st

from agent.agent_loop import run, TraceEvent
from db.exasol_client import list_toolkit
from config import MODEL

st.set_page_config(page_title="The Self-Extending Analyst", layout="wide")
st.title("🧠 The Self-Extending Analyst")
st.caption(f"Model provider: **{MODEL.provider}**  ·  swap it via `MODEL_PROVIDER` in `.env`")

left, right = st.columns([2, 1])

with right:
    st.subheader("🧰 Toolkit (self-written UDFs)")
    if st.button("Refresh toolkit"):
        st.rerun()
    toolkit = list_toolkit()
    if toolkit.get("ok") and toolkit["rows"]:
        for row in toolkit["rows"]:
            udf_name, purpose, signature, created_at, question, status = row
            with st.expander(f"🔧 {udf_name}  ·  {status}"):
                st.write(f"**Signature:** `{signature}`")
                st.write(f"**Purpose:** {purpose}")
                st.write(f"**Created for:** {question}")
                st.write(f"**Created at:** {created_at}")
    elif toolkit.get("ok"):
        st.info("No self-written UDFs yet — the toolkit grows as questions demand new tools.")
    else:
        st.error(f"Could not load toolkit: {toolkit.get('error')}")

with left:
    st.subheader("Ask a question")
    question = st.text_area("Question", placeholder="e.g. What's the median order value per region, excluding refunds?")
    provider_override = st.selectbox("Model provider for this run", ["(use default)", "gemini", "ollama", "groq"])
    go = st.button("Run", type="primary")

    if go and question.strip():
        trace_placeholder = st.container()
        events: list[TraceEvent] = []

        def on_event(ev: TraceEvent):
            events.append(ev)
            with trace_placeholder:
                render_event(ev)

        def render_event(ev: TraceEvent):
            if ev.kind == "plan":
                st.markdown(f"**🗺️ Plan:** {ev.detail.get('note', '')}")
            elif ev.kind == "tool_call":
                st.markdown(f"**🔨 Tool call:** `{ev.detail['name']}`")
                st.json(ev.detail["args"])
            elif ev.kind == "gap_detected":
                st.warning(f"**🕳️ Gap detected** — needs new UDF `{ev.detail['fn_name']}`: {ev.detail['purpose']}")
            elif ev.kind == "validated":
                st.success(f"**✅ Validated** `{ev.detail['fn_name']}`")
                st.code(ev.detail["report"])
            elif ev.kind == "validation_failed":
                st.error(f"**❌ Validation failed** `{ev.detail['fn_name']}` (attempt {ev.detail['attempt']})")
                st.code(ev.detail["report"])
            elif ev.kind == "registered":
                st.success(f"**📦 Registered** `{ev.detail['udf_name']}` — now a permanent tool.")
            elif ev.kind == "tool_result":
                with st.expander(f"Tool result: {ev.detail['name']}"):
                    st.write(ev.detail["result"])
            elif ev.kind == "answer":
                st.markdown("**💬 Answer**")
                st.write(ev.detail["text"])

        provider = None if provider_override == "(use default)" else provider_override
        with st.spinner("Working..."):
            result = run(question, provider=provider, on_event=on_event)

        st.divider()
        if result.used_new_udf:
            st.info(f"This answer required writing and registering a new UDF: **{result.new_udf_name}**")
        else:
            st.info("Answered using existing tools — no new UDF was needed.")
