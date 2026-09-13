"""
CLI entry point.

    python main.py "What's the median order value per region, excluding refunds?"
    python main.py --provider ollama "..."   # override MODEL_PROVIDER for this run
"""
import sys
import argparse

from agent.agent_loop import run, TraceEvent


def _print_event(ev: TraceEvent):
    if ev.kind == "plan":
        print(f"\n[PLAN] {ev.detail.get('note', '')}")
    elif ev.kind == "tool_call":
        print(f"[TOOL CALL] {ev.detail['name']}({ev.detail['args']})")
    elif ev.kind == "gap_detected":
        print(f"[GAP DETECTED] needs new UDF: {ev.detail['fn_name']} — {ev.detail['purpose']}")
    elif ev.kind == "validated":
        print(f"[VALIDATED] {ev.detail['fn_name']}\n{ev.detail['report']}")
    elif ev.kind == "validation_failed":
        print(f"[VALIDATION FAILED] {ev.detail['fn_name']} (attempt {ev.detail['attempt']})\n{ev.detail['report']}")
    elif ev.kind == "registered":
        print(f"[REGISTERED] {ev.detail['udf_name']} is now a permanent tool.")
    elif ev.kind == "tool_result":
        print(f"[TOOL RESULT] {ev.detail['name']} -> {str(ev.detail['result'])[:300]}")
    elif ev.kind == "answer":
        print(f"\n[ANSWER]\n{ev.detail['text']}")


def main():
    parser = argparse.ArgumentParser(description="The Self-Extending Analyst")
    parser.add_argument("question", help="The question to ask the agent")
    parser.add_argument("--provider", default=None, help="Override MODEL_PROVIDER (gemini|ollama|groq)")
    args = parser.parse_args()

    result = run(args.question, provider=args.provider, on_event=_print_event)

    print("\n--- summary ---")
    print(f"Used newly-written UDF: {result.used_new_udf}")
    if result.new_udf_name:
        print(f"New UDF registered: {result.new_udf_name}")


if __name__ == "__main__":
    main()
