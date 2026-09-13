"""
Central configuration loader. Everything reads from environment variables
(populated from .env via python-dotenv), so switching providers, models,
or database targets is a config change, never a code change.
"""
import os
from dataclasses import dataclass
from dotenv import load_dotenv

# Load .env from the project root explicitly (the folder that contains
# this "config" package), rather than relying on python-dotenv's
# current-working-directory search. That search depends on which folder
# you happen to run the command from, which is a common source of
# "it's using default values instead of my .env" bugs on Windows.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ENV_PATH = os.path.join(_PROJECT_ROOT, ".env")
_loaded = load_dotenv(dotenv_path=_ENV_PATH)

if not _loaded:
    print(
        f"[config] Warning: no .env file found at {_ENV_PATH} — "
        f"falling back to default/placeholder values. "
        f"Copy .env.example to .env in that exact folder and fill it in."
    )


def _env(name: str, default: str = None, required: bool = False) -> str:
    val = os.getenv(name, default)
    if required and not val:
        raise EnvironmentError(
            f"Required environment variable {name} is not set. "
            f"Copy .env.example to .env and fill it in."
        )
    return val


@dataclass(frozen=True)
class ExasolConfig:
    # For Exasol SaaS: dsn is "<connection string>:<port>" from the web
    # console, user is your SaaS username, and password is a Personal
    # Access Token (PAT) — not your account password.
    dsn: str = _env("EXASOL_DSN", "localhost:8563")
    user: str = _env("EXASOL_USER", "sys")
    password: str = _env("EXASOL_PASSWORD", "")
    schema: str = _env("EXASOL_SCHEMA", "AGENT_DEMO")


@dataclass(frozen=True)
class ModelConfig:
    provider: str = _env("MODEL_PROVIDER", "gemini").lower()

    gemini_api_key: str = _env("GEMINI_API_KEY", "")
    gemini_model: str = _env("GEMINI_MODEL", "gemini-2.5-flash")

    ollama_host: str = _env("OLLAMA_HOST", "http://localhost:11434")
    ollama_model: str = _env("OLLAMA_MODEL", "llama3.1")

    groq_api_key: str = _env("GROQ_API_KEY", "")
    groq_model: str = _env("GROQ_MODEL", "llama-3.1-70b-versatile")


@dataclass(frozen=True)
class AgentConfig:
    max_udf_repair_attempts: int = int(_env("MAX_UDF_REPAIR_ATTEMPTS", "3"))
    udf_sandbox_timeout_seconds: int = int(_env("UDF_SANDBOX_TIMEOUT_SECONDS", "10"))


EXASOL = ExasolConfig()
MODEL = ModelConfig()
AGENT = AgentConfig()
