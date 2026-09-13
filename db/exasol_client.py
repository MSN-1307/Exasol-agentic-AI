"""
Thin wrapper around pyexasol giving the rest of the system three things:
  1. run_sql(sql) -> rows/columns, for arbitrary queries and DDL
  2. introspect_schema() -> tables/columns, for the agent's planning step
  3. register_udf(...) -> writes UDF DDL + a registry row, atomically-ish

No business logic about *when* to call these lives here — that's the
agent loop's job. This module only knows how to talk to Exasol.
"""
import os
import sys
import uuid
import textwrap
from datetime import datetime, timezone
from typing import Any

import pyexasol

from config import EXASOL


def get_connection():
    return pyexasol.connect(
        dsn=EXASOL.dsn,
        user=EXASOL.user,
        # For Exasol SaaS this is a Personal Access Token, not a password.
        password=EXASOL.password,
        # Deliberately NOT pinning `schema=` here: on a brand-new SaaS
        # database, EXASOL.schema (e.g. AGENT_DEMO) doesn't exist yet —
        # pinning it at login fails with "schema not found" before
        # --init/--seed ever get a chance to create it. Every statement
        # in schema.sql / seed_data.sql is fully schema-qualified
        # (AGENT_REGISTRY.xxx, AGENT_DEMO.xxx), so no active schema is
        # required for this project to work.
        compression=True,
        # Exasol SaaS terminates TLS with a certificate from a public CA,
        # so no fingerprint/self-signed handling is needed here — just
        # make sure encryption is on (pyexasol defaults to True from
        # v0.24.0, set explicitly so it doesn't depend on that default).
        encryption=True,
    )


def run_sql(sql: str) -> dict[str, Any]:
    """
    Execute arbitrary SQL (query or DDL). Returns a dict with columns/rows
    for SELECTs, or a row-count/status for DDL/DML.
    """
    conn = get_connection()
    try:
        stmt = conn.execute(sql)
        if stmt.result_type == "resultSet":
            columns = [c for c in stmt.columns()]
            rows = stmt.fetchall()
            return {"ok": True, "columns": columns, "rows": rows, "row_count": len(rows)}
        else:
            return {"ok": True, "columns": [], "rows": [], "row_count": stmt.rowcount()}
    except Exception as e:
        return {"ok": False, "error": str(e)}
    finally:
        conn.close()


def introspect_schema(schema: str = None) -> dict[str, Any]:
    """
    Returns table/column metadata for the agent's planning step, so it
    knows what data actually exists before deciding it needs a new tool.
    """
    schema = schema or EXASOL.schema
    sql = f"""
        SELECT column_table, column_name, column_type
        FROM exa_all_columns
        WHERE column_schema = '{schema.upper()}'
        ORDER BY column_table, column_ordinal_position
    """
    result = run_sql(sql)
    if not result["ok"]:
        return result

    tables: dict[str, list[dict]] = {}
    for row in result["rows"]:
        table, col, coltype = row
        tables.setdefault(table, []).append({"column": col, "type": coltype})
    return {"ok": True, "schema": schema, "tables": tables}


def list_toolkit() -> dict[str, Any]:
    """Every self-written UDF currently registered — the visible toolkit."""
    sql = """
        SELECT udf_name, purpose, signature, created_at, created_for_question, status
        FROM agent_registry.udf_catalog
        ORDER BY created_at DESC
    """
    return run_sql(sql)


def register_udf(
    udf_name: str,
    purpose: str,
    signature: str,
    source_code: str,
    test_report: str,
    created_for_question: str,
    udf_ddl: str,
) -> dict[str, Any]:
    """
    Executes the CREATE PYTHON SCALAR/SET SCRIPT DDL to actually install
    the UDF in Exasol, then logs it in the registry table. Only ever
    called after validation.py has confirmed the candidate passes all
    known test cases.
    """
    ddl_result = run_sql(udf_ddl)
    if not ddl_result["ok"]:
        return {"ok": False, "stage": "ddl", "error": ddl_result["error"]}

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    escaped_source = source_code.replace("'", "''")
    escaped_report = test_report.replace("'", "''")
    escaped_purpose = purpose.replace("'", "''")
    escaped_question = created_for_question.replace("'", "''")

    log_sql = f"""
        MERGE INTO agent_registry.udf_catalog t
        USING (SELECT '{udf_name}' AS udf_name FROM dual) s
        ON t.udf_name = s.udf_name
        WHEN MATCHED THEN UPDATE SET
            purpose = '{escaped_purpose}',
            signature = '{signature}',
            source_code = '{escaped_source}',
            test_report = '{escaped_report}',
            created_at = TIMESTAMP '{now}',
            created_for_question = '{escaped_question}',
            status = 'REGISTERED'
        WHEN NOT MATCHED THEN INSERT (
            udf_name, purpose, signature, source_code, test_report,
            created_at, created_for_question, status
        ) VALUES (
            '{udf_name}', '{escaped_purpose}', '{signature}', '{escaped_source}',
            '{escaped_report}', TIMESTAMP '{now}', '{escaped_question}', 'REGISTERED'
        )
    """
    log_result = run_sql(log_sql)
    if not log_result["ok"]:
        return {"ok": False, "stage": "registry_log", "error": log_result["error"]}

    return {"ok": True, "udf_name": udf_name}


def log_question(question: str, used_new_udf: bool, udf_name: str, answer_summary: str) -> dict[str, Any]:
    qid = str(uuid.uuid4())
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    sql = f"""
        INSERT INTO agent_registry.question_log
        (question_id, question_text, used_new_udf, udf_name, answer_summary, asked_at)
        VALUES (
            '{qid}', '{question.replace("'", "''")}', {str(used_new_udf).upper()},
            {f"'{udf_name}'" if udf_name else 'NULL'},
            '{answer_summary.replace("'", "''")}', TIMESTAMP '{now}'
        )
    """
    return run_sql(sql)


def _run_sql_file(path: str):
    with open(path) as f:
        full_sql = f.read()

    # naive split on semicolons is fine here — our .sql files have no
    # semicolons inside string literals.
    statements = [s.strip() for s in full_sql.split(";") if s.strip()]
    for stmt in statements:
        result = run_sql(stmt)
        if not result["ok"]:
            print(f"FAILED: {stmt[:80]}...\n  {result['error']}", file=sys.stderr)
            sys.exit(1)
        print(f"OK: {stmt.splitlines()[0][:80]}")


def init_schema():
    """Runs db/schema.sql against the configured Exasol instance."""
    schema_path = os.path.join(os.path.dirname(__file__), "schema.sql")
    _run_sql_file(schema_path)
    print("Schema initialized.")


def seed_demo_data():
    """Runs db/seed_data.sql — the optional demo ORDERS table + sample rows."""
    seed_path = os.path.join(os.path.dirname(__file__), "seed_data.sql")
    _run_sql_file(seed_path)
    print("Demo data seeded.")


if __name__ == "__main__":
    if "--init" in sys.argv:
        init_schema()
    if "--seed" in sys.argv:
        seed_demo_data()
    if "--init" not in sys.argv and "--seed" not in sys.argv:
        print(textwrap.dedent("""
            Usage:
              python -m db.exasol_client --init   # create AGENT_REGISTRY schema + tables
              python -m db.exasol_client --seed   # load demo ORDERS table + sample rows
              (both flags can be combined: --init --seed)
        """))
