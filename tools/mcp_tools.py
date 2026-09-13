"""
The "MCP layer" from the blueprint: a small set of callable tools the
agent's function-calling loop can invoke. Each tool has a JSON-schema
declaration (for the model adapter) and a dispatcher that actually runs
it against Exasol.

Tools exposed:
  list_schema      - introspect tables/columns
  run_sql          - execute a SELECT (or safe DML) against the schema
  draft_udf_ddl    - turn candidate Python source into CREATE ... SCRIPT DDL
                     (does NOT execute it — validation must pass first)
  register_udf     - install a validated UDF + log it in the registry
  run_udf          - call an already-registered UDF via a generated SELECT
  list_toolkit      - list every self-written UDF registered so far
"""
from __future__ import annotations

import re
from typing import Any

from db import exasol_client as db

# ---------------------------------------------------------------- schemas --

TOOL_SCHEMAS: list[dict] = [
    {
        "name": "list_schema",
        "description": (
            "List all tables and columns available in the working Exasol schema, "
            "so you know what data exists before deciding you need a new tool."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "run_sql",
        "description": (
            "Execute a SQL statement (SELECT, or a call to an already-registered UDF) "
            "against the Exasol schema and return the result rows."
        ),
        "parameters": {
            "type": "object",
            "properties": {"sql": {"type": "string", "description": "The SQL statement to run."}},
            "required": ["sql"],
        },
    },
    {
        "name": "list_toolkit",
        "description": "List every self-written UDF registered so far: name, purpose, signature, when it was created.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "draft_and_validate_udf",
        "description": (
            "Propose a new Python UDF to fill a capability gap. Provide the full Python "
            "source of a single function, its name, a short purpose, the parameter types "
            "and return type, and a list of test cases with known-correct expected outputs. "
            "This runs the candidate in a sandbox against the test cases and reports pass/fail "
            "— it does NOT install anything yet. Only call register_udf after this reports 100% pass."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "fn_name": {"type": "string"},
                "purpose": {"type": "string"},
                "source_code": {
                    "type": "string",
                    "description": "Full Python source defining exactly one top-level function named fn_name.",
                },
                "param_types": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["DOUBLE", "VARCHAR", "INTEGER", "BOOLEAN"]},
                    "description": "Exasol SQL types of the function's parameters, in order.",
                },
                "return_type": {
                    "type": "string",
                    "enum": ["DOUBLE", "VARCHAR", "INTEGER", "BOOLEAN"],
                },
                "test_cases": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "args": {"type": "array", "items": {}},
                            "expected": {},
                            "description": {"type": "string"},
                        },
                        "required": ["args", "expected"],
                    },
                },
            },
            "required": ["fn_name", "purpose", "source_code", "param_types", "return_type", "test_cases"],
        },
    },
    {
        "name": "register_udf",
        "description": (
            "Install a UDF into Exasol and log it in the permanent registry. "
            "Only call this after draft_and_validate_udf reported a 100% pass rate for the "
            "exact same fn_name/source_code — pass the validation_token you were given back."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "validation_token": {
                    "type": "string",
                    "description": "The token returned by a successful draft_and_validate_udf call.",
                },
                "created_for_question": {"type": "string"},
            },
            "required": ["validation_token", "created_for_question"],
        },
    },
]


# --------------------------------------------------------------- UDF DDL --

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def build_udf_ddl(fn_name: str, source_code: str, param_types: list[str], return_type: str) -> str:
    """
    Wraps a validated Python function as an Exasol CREATE PYTHON3 SCALAR
    SCRIPT. Exasol requires an `run(ctx)` entrypoint reading ctx.<param>
    for scalar scripts in some dialects; the widely-supported emit style
    used here is the simple scalar UDF signature (`def <name>(args...):`)
    supported by Exasol's Python3 UDF flavor.
    """
    if not _IDENT_RE.match(fn_name):
        raise ValueError(f"Invalid UDF name: {fn_name}")

    param_list = ", ".join(f"p{i} {t}" for i, t in enumerate(param_types))
    call_args = ", ".join(f"p{i}" for i in range(len(param_types)))

    ddl = f"""
CREATE OR REPLACE PYTHON3 SCALAR SCRIPT {db.EXASOL.schema}.{fn_name} ({param_list})
RETURNS {return_type} AS

{source_code}

def run(ctx):
    return {fn_name}({', '.join(f'ctx.p{i}' for i in range(len(param_types)))})
/
""".strip()
    return ddl


# --------------------------------------------------------------- dispatch --

# Holds the most recent successful validation per fn_name so register_udf
# can't install something that was never actually checked.
_VALIDATION_CACHE: dict[str, dict] = {}


def dispatch(name: str, args: dict) -> Any:
    from agent.validation import TestCase, validate_udf
    import uuid

    if name == "list_schema":
        return db.introspect_schema()

    if name == "run_sql":
        return db.run_sql(args["sql"])

    if name == "list_toolkit":
        return db.list_toolkit()

    if name == "draft_and_validate_udf":
        cases = [
            TestCase(args=tc["args"], expected=tc["expected"], description=tc.get("description", ""))
            for tc in args["test_cases"]
        ]
        report = validate_udf(args["source_code"], args["fn_name"], cases)
        token = str(uuid.uuid4())
        result = {
            "passed": report.passed,
            "report_text": report.as_text(),
            "validation_token": token if report.passed else None,
        }
        if report.passed:
            _VALIDATION_CACHE[token] = {
                "fn_name": args["fn_name"],
                "purpose": args["purpose"],
                "source_code": args["source_code"],
                "param_types": args["param_types"],
                "return_type": args["return_type"],
                "test_report": report.as_text(),
            }
        return result

    if name == "register_udf":
        cached = _VALIDATION_CACHE.get(args["validation_token"])
        if not cached:
            return {"ok": False, "error": "Unknown or unvalidated token. Call draft_and_validate_udf first."}
        ddl = build_udf_ddl(cached["fn_name"], cached["source_code"], cached["param_types"], cached["return_type"])
        signature = f"{cached['fn_name']}({', '.join(cached['param_types'])}) -> {cached['return_type']}"
        result = db.register_udf(
            udf_name=cached["fn_name"],
            purpose=cached["purpose"],
            signature=signature,
            source_code=cached["source_code"],
            test_report=cached["test_report"],
            created_for_question=args["created_for_question"],
            udf_ddl=ddl,
        )
        return result

    raise ValueError(f"Unknown tool: {name}")
