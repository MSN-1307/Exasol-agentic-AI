# The Self-Extending Analyst

An agent that answers questions against an Exasol database using SQL and a
growing library of **self-written UDFs**. When it hits a capability gap
(something plain SQL / existing UDFs can't do), it writes a new Python UDF,
validates it against known test cases in a sandbox, registers it in Exasol,
and uses it to answer the question. That UDF then stays available forever —
to this session and every future one.

```
User question
     │
     ▼
Agent (LLM via model adapter) ── plans ──► tries existing tools (SQL / UDFs)
     │                                            │
     │                                   answer found? ──► respond
     │                                            │
     │                               capability gap detected
     │                                            │
     │                                  drafts new UDF code
     │                                            │
     │                          validation harness runs test cases
     │                                            │
     │                         pass? ──► register in Exasol registry
     │                          │                 │
     │                         fail                │
     │                          │                 ▼
     │                    revise / retry     answer original question
     │                                            │
     └────────────────────────────────────────────┘
```

## Components

| Component | File | Purpose |
|---|---|---|
| Exasol client | `db/exasol_client.py` | Connection pool, query execution, schema introspection, UDF DDL |
| Registry schema | `db/schema.sql` | `agent_registry.udf_catalog` table (the visible "toolkit") |
| MCP-style tool layer | `tools/mcp_tools.py` | Callable tool functions exposed to the LLM: `list_schema`, `run_sql`, `register_udf`, `run_udf`, `list_toolkit` |
| Model adapter | `agent/model_adapter.py` | One interface, three backends: Gemini (default), Ollama, Groq (OpenAI-compatible). Swap via config/env, no code changes. |
| Validation harness | `agent/validation.py` | Runs a candidate UDF against known-answer test cases in a sandboxed subprocess before it's ever trusted |
| Agent loop | `agent/agent_loop.py` | The core loop described above, using native function/tool calling |
| Thin UI | `ui/app.py` | Streamlit live trace: plan → gap detected → UDF drafted → validated → registered → answered, plus a running toolkit panel |
| Entry point | `main.py` | CLI runner (no UI needed) |

## Setup

```bash
cd self_extending_analyst
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in your own values
```

### Creating the database (Exasol SaaS Personal / free tier)

This project targets Exasol **SaaS**, not a local Docker instance — you
create the actual database in the browser, then this code connects to it.

1. Sign up / log in at **cloud.exasol.com** and open the web console.
2. On the **Databases** page, create a database and a cluster if you don't
   already have one (the free Personal tier gives you one cluster).
3. **Allow your IP:** go to the cluster's **Connect via tools** wizard (or
   the **Security** page) and add the IP address of the machine that will
   run this code to the allow list. Without this, every connection attempt
   times out — it's the #1 SaaS gotcha.
4. **Get connection details:** on the same **Databases** page, click the
   info icon on your cluster to see the **Connection string** and **Port**
   (default `8563`) and your **User name**.
5. **Create a Personal Access Token (PAT):** in the web console, go to your
   user menu → **Personal Access Token** → create one. This PAT is what
   you put in `EXASOL_PASSWORD` — SaaS auth uses a token here, not your
   login password. It's shown once, so copy it immediately.
6. Put all of that into `.env` (see below), then run:
   ```bash
   python -m db.exasol_client --init          # creates AGENT_REGISTRY schema + tables
   python -m db.exasol_client --init --seed    # also loads the demo ORDERS table
   ```

### `.env` values you must supply

```
# From the web console: Databases -> your cluster -> Connect via tools
EXASOL_DSN=your-cluster-connection-string.exasol.com:8563
EXASOL_USER=your_saas_username
# A Personal Access Token, not your account password (see step 5 above)
EXASOL_PASSWORD=your_personal_access_token_here
EXASOL_SCHEMA=AGENT_DEMO

MODEL_PROVIDER=gemini            # gemini | ollama | groq
GEMINI_API_KEY=...               # your own key from aistudio.google.com
GEMINI_MODEL=gemini-2.5-flash    # see note below

OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=llama3.1

GROQ_API_KEY=...
GROQ_MODEL=llama-3.1-70b-versatile
```

Exasol SaaS terminates TLS with a publicly-trusted certificate, so unlike
a self-hosted/Docker instance you don't need to configure a certificate
fingerprint — `db/exasol_client.py` just connects with `encryption=True`
and it works once your IP is allow-listed and the PAT is correct.

> **Note on the model name:** you asked for "Gemini 3.6" — as of this
> writing that isn't a real Gemini model name (the current line is
> Gemini 2.5 / 2.0, e.g. `gemini-2.5-flash`, `gemini-2.5-pro`). I've wired
> the code to read the model name from `GEMINI_MODEL` in `.env` so you can
> drop in whatever the current model string is without touching code —
> just check https://ai.google.dev/gemini-api/docs/models for the latest
> name before you run it. I also can't generate or embed a real API key
> for you (it's a secret tied to your own Google account) — `.env.example`
> has a placeholder for you to fill in.

### Run

```bash
# CLI
python main.py "What's the median order value per region, excluding refunds?"

# or the live-trace UI
streamlit run ui/app.py
```

## Swapping the model backend

Everything the agent loop talks to is `agent.model_adapter.get_adapter()`.
Change `MODEL_PROVIDER` in `.env` to `ollama` or `groq` and nothing else in
the code needs to change — this is your venue-wifi/rate-limit safety net.

## Validation philosophy

A self-written UDF is **never** trusted on the strength of the LLM's own
claim that it works. `agent/validation.py`:
1. Writes the candidate code to a temp file.
2. Runs it in a subprocess with a timeout and no network access assumptions.
3. Feeds it each test case's input, compares output to the known-correct
   answer (exact match or tolerance for floats).
4. Only a 100%-pass candidate is eligible for registration; failures are
   fed back to the agent as an error report so it can revise and retry
   (bounded retry count, configurable).

## Registry table

```sql
CREATE TABLE agent_registry.udf_catalog (
    udf_name       VARCHAR(128) PRIMARY KEY,
    purpose        VARCHAR(2000),
    signature      VARCHAR(500),
    source_code    CLOB,
    test_report    CLOB,
    created_at     TIMESTAMP,
    created_for_question VARCHAR(2000)
);
```
This is both the agent's live toolkit and its audit log — every tool it
has ever taught itself, why, and proof it passed validation.
