"""
The core loop:

  user question
    -> agent plans, tries existing tools (list_schema / run_sql / list_toolkit)
    -> if it hits a capability gap, calls draft_and_validate_udf
    -> on validation failure, sees the error report and may retry (bounded)
    -> on validation success, calls register_udf
    -> answers the original question, now with the new UDF available
    -> the UDF is registered in Exasol permanently: every future run of
       this loop (this session or a brand new process) sees it via
       list_schema / list_toolkit and can just call it with run_sql.

Emits a list of `TraceEvent`s as it goes, so the UI (or CLI) can show a
live plan -> gap detected -> UDF drafted -> validated -> registered ->
answered trace, plus the running toolkit.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Callable

from config import AGENT
from tools.mcp_tools import TOOL_SCHEMAS, dispatch
from agent.model_adapter import get_adapter, ModelResponse

SYSTEM_PROMPT = """\
You are the Self-Extending Analyst, an agent that answers questions against
an Exasol database.

Rules you must follow:
1. Always call list_schema first if you haven't already this conversation,
   so you know what tables/columns actually exist.
2. Prefer answering with plain SQL (run_sql) or an already-registered UDF
   (visible via list_toolkit / list_schema) whenever that's sufficient.
3. Only propose a new UDF when you hit a genuine capability gap - something
   plain SQL and existing UDFs cannot express (e.g. a statistical routine,
   a parsing/NLP step, a custom scoring function).
4. When you do write a new UDF: call draft_and_validate_udf with the full
   function source and a set of test cases you are confident have known-
   correct expected outputs. Do not call register_udf until that reports
   passed=true. If it reports failures, read the report, fix the bug, and
   call draft_and_validate_udf again (you have a limited number of
   attempts).
5. After a UDF is registered, use run_sql to actually call it and answer
   the user's original question with real data - never answer from
   assumption.
6. Give a clear, direct final answer in plain text once you have the data
   you need. Do not call any more tools once you're ready to answer.
"""


@dataclass
class TraceEvent:
    kind: str  # "plan" | "tool_call" | "tool_result" | "gap_detected" | "validated" | "registered" | "answer"
    detail: dict = field(default_factory=dict)


@dataclass
class RunResult:
    answer: str
    trace: list[TraceEvent]
    used_new_udf: bool
    new_udf_name: str | None


def run(question: str, provider: str | None = None, on_event: Callable[[TraceEvent], None] | None = None) -> RunResult:
    adapter = get_adapter(provider)
    trace: list[TraceEvent] = []
    used_new_udf = False
    new_udf_name = None

    def emit(kind: str, **detail):
        ev = TraceEvent(kind=kind, detail=detail)
        trace.append(ev)
        if on_event:
            on_event(ev)

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": question},
    ]

    emit("plan", note="Sending question to model with tool access.")

    repair_attempts = 0
    max_turns = 20  # hard ceiling so a confused loop can't run forever
    for _turn in range(max_turns):
        resp: ModelResponse = adapter.generate(messages, TOOL_SCHEMAS)

        if not resp.tool_calls:
            final_text = resp.text or "(no answer produced)"
            emit("answer", text=final_text)
            return RunResult(answer=final_text, trace=trace, used_new_udf=used_new_udf, new_udf_name=new_udf_name)

        messages.append({
            "role": "assistant",
            "content": resp.text,
            "tool_calls": [
                {"id": tc.id, "name": tc.name, "args": tc.args, "thought_signature": tc.thought_signature}
                for tc in resp.tool_calls
            ],
        })

        for tc in resp.tool_calls:
            emit("tool_call", name=tc.name, args=tc.args)

            if tc.name == "draft_and_validate_udf":
                emit("gap_detected", fn_name=tc.args.get("fn_name"), purpose=tc.args.get("purpose"))

            result = dispatch(tc.name, tc.args)

            if tc.name == "draft_and_validate_udf":
                if result.get("passed"):
                    emit("validated", fn_name=tc.args.get("fn_name"), report=result["report_text"])
                    repair_attempts = 0
                else:
                    repair_attempts += 1
                    emit("validation_failed", fn_name=tc.args.get("fn_name"),
                         report=result["report_text"], attempt=repair_attempts)
                    if repair_attempts >= AGENT.max_udf_repair_attempts:
                        result["note"] = (
                            f"Exceeded max repair attempts ({AGENT.max_udf_repair_attempts}). "
                            "Give up on this UDF approach and try a different strategy, "
                            "or answer with the best available data."
                        )

            if tc.name == "register_udf" and result.get("ok"):
                used_new_udf = True
                new_udf_name = result.get("udf_name")
                emit("registered", udf_name=new_udf_name)

            emit("tool_result", name=tc.name, result=result)

            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "name": tc.name,
                "content": json.dumps(result, default=str),
            })

    final_text = "Reached the maximum number of reasoning steps without a final answer."
    emit("answer", text=final_text)
    return RunResult(answer=final_text, trace=trace, used_new_udf=used_new_udf, new_udf_name=new_udf_name)
