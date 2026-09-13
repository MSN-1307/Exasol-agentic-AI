# 🛠️ Deployment & Run Guide — The Self-Extending Analyst

This guide walks through setting up and running **The Self-Extending Analyst** — an agent that answers questions against an Exasol database and writes its own validated Python UDFs when it hits a capability gap.

> ⚠️ **Security note before you start:** if your `.env` / `.env.example` files contain real API keys or an Exasol Personal Access Token, **rotate them and never commit them**. An `.env.example` should only ever contain placeholder values (see Step 6).

---

## 1. Prerequisites

| Requirement | Notes |
|---|---|
| Python 3.10+ | Check with `python --version` |
| pip | Bundled with Python |
| An Exasol SaaS account | Free "Personal" tier at [cloud.exasol.com](https://cloud.exasol.com) — no local Docker instance needed |
| An LLM provider key | Gemini (default), or Groq / a local Ollama install as a fallback |
| Internet connection | Required for the Exasol SaaS connection and cloud LLM calls (not required if using Ollama fully locally, except for the initial model pull) |

---

## 2. Clone / Copy the Project

```bash
cd self_extending_analyst
```

*(Adjust to wherever you've placed the project — `agent/`, `config/`, `db/`, `tools/`, `ui/`, `main.py`, etc.)*

---

## 3. Create a Virtual Environment

```bash
python3 -m venv venv
source venv/bin/activate      # macOS / Linux
# venv\Scripts\activate       # Windows
```

---

## 4. Install Dependencies

```bash
pip install -r requirements.txt
```

This installs:

| Package | Purpose |
|---|---|
| `pyexasol` | Exasol database client |
| `google-genai` | Gemini API client |
| `openai` | Used for the OpenAI-compatible Groq endpoint |
| `python-dotenv` | Loads `.env` config |
| `streamlit` | Optional live-trace UI |
| `pandas` | Data handling |
| `tenacity` | Retry logic for flaky API calls |

---

## 5. Set Up Your Exasol SaaS Database

This project targets **Exasol SaaS**, not a local Docker instance — you create the database in the browser, then the code connects to it.

1. **Sign up / log in** at [cloud.exasol.com](https://cloud.exasol.com) and open the web console.
2. **Create a database and cluster** on the **Databases** page if you don't already have one (the free Personal tier gives you one cluster).
3. **Allow your IP.** Open the cluster's **Connect via tools** wizard (or the **Security** page) and add the IP address of the machine that will run this code to the allow list. Skipping this step is the #1 cause of connection timeouts.
4. **Get your connection details.** On the **Databases** page, click the info icon on your cluster to find the **Connection string** and **Port** (default `8563`), and your **User name**.
5. **Create a Personal Access Token (PAT).** In the web console, go to your user menu → **Personal Access Token** → create one. This PAT goes in `EXASOL_PASSWORD` — SaaS authentication uses a token here, not your account login password. **It's shown only once, so copy it immediately** and store it somewhere safe (e.g. a password manager), not just in a plaintext file you might commit.

---

## 6. Configure Environment Variables

Copy the example file and fill in your own values:

```bash
cp .env.example .env
```

`.env`:

```
# --- Exasol SaaS ---
EXASOL_DSN=your-cluster-connection-string.exasol.com:8563
EXASOL_USER=your_saas_username
EXASOL_PASSWORD=your_personal_access_token_here
EXASOL_SCHEMA=AGENT_DEMO

# --- Model provider selection: gemini | ollama | groq ---
MODEL_PROVIDER=gemini

# --- Gemini ---
GEMINI_API_KEY=your_own_gemini_key
GEMINI_MODEL=gemini-2.5-flash   # check https://ai.google.dev/gemini-api/docs/models for the current name

# --- Ollama (local fallback) ---
OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=llama3.1

# --- Groq (OpenAI-compatible cloud fallback) ---
GROQ_API_KEY=your_own_groq_key
GROQ_MODEL=llama-3.1-70b-versatile

# --- Agent behavior ---
MAX_UDF_REPAIR_ATTEMPTS=3
UDF_SANDBOX_TIMEOUT_SECONDS=10
```

**Important:** `.env.example` (the file committed to the repo) should contain **placeholder text only** — `your_own_gemini_key`, not an actual working key. `.env` (your real, local config) should be listed in `.gitignore` and never pushed to GitHub.

Exasol SaaS terminates TLS with a publicly-trusted certificate, so — unlike a self-hosted/Docker Exasol instance — you don't need to configure a certificate fingerprint; the client just connects with `encryption=True` once your IP is allow-listed and the PAT is correct.

---

## 7. Initialize the Database Schema

```bash
python -m db.exasol_client --init          # creates AGENT_REGISTRY schema + tables
python -m db.exasol_client --init --seed   # also loads the demo ORDERS table
```

This creates the `agent_registry.udf_catalog` table — the agent's persistent toolkit and audit log.

---

## 8. Run the Agent

**Via the CLI:**

```bash
python main.py "What's the median order value per region, excluding refunds?"

# Override the model provider for a single run:
python main.py --provider ollama "What's the median order value per region, excluding refunds?"
```

You'll see a live trace in the terminal: `[PLAN]` → `[TOOL CALL]` → `[GAP DETECTED]` (if applicable) → `[VALIDATED]` / `[VALIDATION FAILED]` → `[REGISTERED]` → `[ANSWER]`, followed by a summary of whether a new UDF was written and registered.

**Via the Streamlit UI:**

```bash
streamlit run ui/app.py
```

This gives the same live trace in a browser, plus a running panel of the toolkit built up so far.

---

## 9. Swapping the LLM Backend

Everything the agent talks to goes through `agent.model_adapter.get_adapter()`. To switch providers, change one line in `.env`:

```
MODEL_PROVIDER=ollama   # or groq, or back to gemini
```

No code changes required — this is your safety net for rate limits or no-internet situations (e.g. run fully local with Ollama).

---

## 10. Verifying the Setup

| Check | Command | Expected Result |
|---|---|---|
| DB connectivity | `python -m db.exasol_client --init` | Schema created without connection errors |
| Basic SQL query | `python main.py "How many orders are there?"` | Returns an answer without a gap being detected |
| UDF self-extension | Ask a question needing custom logic (e.g. median, geo-distance) | `[GAP DETECTED]` → `[VALIDATED]` → `[REGISTERED]` → `[ANSWER]` trace appears |
| Persistence | Re-run the same or a similar question | The agent reuses the already-registered UDF instead of rewriting it |
| Provider swap | `python main.py --provider ollama "..."` | Runs successfully against your local Ollama install |

---

## 11. Validation Philosophy (What Keeps This Safe)

A self-written UDF is **never** trusted on the strength of the LLM's own claim that it works. `agent/validation.py`:

1. Writes the candidate code to a temp file.
2. Runs it in a subprocess with a timeout and no assumed network access.
3. Feeds it each test case's input and compares the output to the known-correct answer (exact match, or tolerance for floats).
4. Only a 100%-passing candidate is eligible for registration; failures are fed back to the agent as an error report so it can revise and retry, bounded by `MAX_UDF_REPAIR_ATTEMPTS`.

---

## 12. Common Issues & Troubleshooting

| Issue | Likely Cause | Fix |
|---|---|---|
| Connection times out | Your IP isn't allow-listed on the Exasol cluster | Add your current IP in the cluster's Security / Connect via tools page |
| Auth failure | Using your account password instead of a PAT | Generate a Personal Access Token and use that as `EXASOL_PASSWORD` |
| `ModuleNotFoundError` | Dependencies not installed / venv not activated | Re-activate venv, re-run `pip install -r requirements.txt` |
| Gemini model errors | Model name in `.env` no longer exists / typo'd | Check the current model list at ai.google.dev/gemini-api/docs/models and update `GEMINI_MODEL` |
| UDF keeps failing validation | Test cases too strict/ambiguous, or repair attempts exhausted | Check the `[VALIDATION FAILED]` report for the specific mismatch; raise `MAX_UDF_REPAIR_ATTEMPTS` if needed |
| Streamlit UI won't start | Streamlit not installed, or wrong Python env active | Confirm venv is activated and `pip show streamlit` succeeds |

---

## 13. Moving Beyond Local / Personal-Tier Use (Optional)

- Move from the free Personal tier to a production Exasol SaaS cluster for larger workloads.
- Add authentication in front of the Streamlit UI before exposing it to more than one user.
- Consider a secrets manager (rather than `.env`) for API keys and the Exasol PAT in any shared or deployed environment.
- Expand the validation harness's test-case library as the UDF catalog grows, to keep validation rigorous over time.
