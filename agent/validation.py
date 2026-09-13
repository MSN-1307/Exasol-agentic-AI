"""
No self-written UDF is ever trusted on the LLM's say-so. Before
db.exasol_client.register_udf() is ever called, the candidate source code
must pass every test case here, executed in an isolated subprocess.

A "test case" is: a function name to call, a list of positional args, and
the known-correct expected output (with optional float tolerance).
"""
import json
import math
import subprocess
import sys
import tempfile
import textwrap
import os
from dataclasses import dataclass, field
from typing import Any

from config import AGENT

_RUNNER_TEMPLATE = """
import json, sys

# --- candidate code under test ---
{candidate_code}
# --- end candidate code ---

def _run():
    cases = json.loads({cases_json!r})
    results = []
    for case in cases:
        try:
            fn = globals()[{fn_name!r}]
            out = fn(*case["args"])
            results.append({{"ok": True, "output": out}})
        except Exception as e:
            results.append({{"ok": False, "error": f"{{type(e).__name__}}: {{e}}"}})
    print(json.dumps(results))

_run()
"""


@dataclass
class TestCase:
    args: list
    expected: Any
    tolerance: float = 1e-6
    description: str = ""


@dataclass
class ValidationReport:
    passed: bool
    total: int
    passed_count: int
    details: list = field(default_factory=list)

    def as_text(self) -> str:
        lines = [f"{self.passed_count}/{self.total} test cases passed."]
        for d in self.details:
            status = "PASS" if d["passed"] else "FAIL"
            lines.append(
                f"  [{status}] {d.get('description', '')} "
                f"args={d['args']} expected={d['expected']} got={d.get('actual', d.get('error'))}"
            )
        return "\n".join(lines)


def _values_match(actual, expected, tolerance) -> bool:
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        return math.isclose(actual, expected, rel_tol=tolerance, abs_tol=tolerance)
    return actual == expected


def validate_udf(candidate_code: str, fn_name: str, test_cases: list[TestCase]) -> ValidationReport:
    """
    Runs `fn_name` from `candidate_code` against each TestCase in a
    subprocess with a timeout, and diffs actual vs. expected output.
    """
    cases_payload = json.dumps([{"args": tc.args} for tc in test_cases])

    runner_src = _RUNNER_TEMPLATE.format(
        candidate_code=textwrap.indent(candidate_code, ""),
        cases_json=cases_payload,
        fn_name=fn_name,
    )

    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(runner_src)
        tmp_path = f.name

    try:
        proc = subprocess.run(
            [sys.executable, tmp_path],
            capture_output=True,
            text=True,
            timeout=AGENT.udf_sandbox_timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return ValidationReport(
            passed=False,
            total=len(test_cases),
            passed_count=0,
            details=[{
                "passed": False,
                "args": None,
                "expected": None,
                "error": f"Execution exceeded {AGENT.udf_sandbox_timeout_seconds}s timeout",
                "description": "timeout",
            }],
        )
    finally:
        os.unlink(tmp_path)

    if proc.returncode != 0:
        return ValidationReport(
            passed=False,
            total=len(test_cases),
            passed_count=0,
            details=[{
                "passed": False,
                "args": None,
                "expected": None,
                "error": f"Candidate code crashed at import/parse time:\n{proc.stderr[-2000:]}",
                "description": "syntax/import error",
            }],
        )

    try:
        raw_results = json.loads(proc.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        return ValidationReport(
            passed=False,
            total=len(test_cases),
            passed_count=0,
            details=[{
                "passed": False,
                "args": None,
                "expected": None,
                "error": f"Runner produced no parseable output. stdout={proc.stdout!r} stderr={proc.stderr!r}",
                "description": "runner failure",
            }],
        )

    details = []
    passed_count = 0
    for tc, result in zip(test_cases, raw_results):
        if not result["ok"]:
            details.append({
                "passed": False, "args": tc.args, "expected": tc.expected,
                "error": result["error"], "description": tc.description,
            })
            continue
        matched = _values_match(result["output"], tc.expected, tc.tolerance)
        if matched:
            passed_count += 1
        details.append({
            "passed": matched, "args": tc.args, "expected": tc.expected,
            "actual": result["output"], "description": tc.description,
        })

    return ValidationReport(
        passed=passed_count == len(test_cases),
        total=len(test_cases),
        passed_count=passed_count,
        details=details,
    )
