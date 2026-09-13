"""
One interface (`ModelAdapter.generate`) that the agent loop talks to.
Three backends behind it. Switching providers is `MODEL_PROVIDER` in
.env — no code changes, which is the point: it's the safety net if
venue wifi or a rate limit takes out the primary provider mid-demo.

Every adapter speaks the same request/response shape:
  generate(messages, tools) -> ModelResponse(text, tool_calls)

`messages` is the OpenAI-style list of {"role", "content"} dicts (plus
"tool" role messages for tool results going back in).
`tools` is a list of {"name", "description", "parameters": <JSON schema>}.
"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from config import MODEL


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict
    # Gemini 3.x "thinking" models attach an opaque thought_signature to
    # (the first of) any function_call part in a response. It MUST be
    # echoed back verbatim on that exact part when the conversation
    # history is replayed, or the next call 400s with "Function call is
    # missing a thought_signature". Other providers ignore this field.
    thought_signature: Any = None


@dataclass
class ModelResponse:
    text: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)


class ModelAdapter(ABC):
    @abstractmethod
    def generate(self, messages: list[dict], tools: list[dict]) -> ModelResponse:
        ...


# ---------------------------------------------------------------- Gemini --
# Google's documented sentinel for replaying function_call parts that
# don't have a real thought_signature (e.g. because the installed SDK
# version drops the real one during response deserialization, which is
# a confirmed upstream bug: googleapis/python-genai#2406). Passing this
# tells the API to skip signature validation for that part instead of
# rejecting the request outright.
# https://ai.google.dev/gemini-api/docs/generate-content/thought-signatures
_SKIP_SIGNATURE = "skip_thought_signature_validator"


class GeminiAdapter(ModelAdapter):
    def __init__(self):
        from google import genai
        from google.genai import types
        self._types = types
        if not MODEL.gemini_api_key:
            raise EnvironmentError(
                "GEMINI_API_KEY is not set. Add your own key to .env "
                "(get one at https://aistudio.google.com/apikey)."
            )
        self._client = genai.Client(api_key=MODEL.gemini_api_key)
        self._model = MODEL.gemini_model

    def _to_gemini_tools(self, tools: list[dict]):
        t = self._types
        decls = [
            t.FunctionDeclaration(
                name=tool["name"],
                description=tool["description"],
                parameters=tool["parameters"],
            )
            for tool in tools
        ]
        return [t.Tool(function_declarations=decls)]

    def _to_gemini_contents(self, messages: list[dict]):
        """Translate our generic OpenAI-shaped history into Gemini `contents`."""
        t = self._types
        contents = []
        for m in messages:
            role = m["role"]
            if role == "system":
                # handled separately as system_instruction by the caller
                continue
            elif role == "user":
                contents.append(t.Content(role="user", parts=[t.Part(text=m["content"])]))
            elif role == "assistant":
                if m.get("tool_calls"):
                    # IMPORTANT: built as a plain dict, NOT t.Content/t.Part.
                    # The installed google-genai SDK's Part pydantic model
                    # doesn't declare `thought_signature` as a field (a
                    # confirmed, still-open upstream bug — see
                    # googleapis/python-genai#2406), so constructing
                    # t.Part(thought_signature=...) raises a Pydantic
                    # `extra_forbidden` ValidationError. Passing a plain
                    # dict skips that strict validation entirely — the
                    # SDK accepts dicts anywhere it accepts Content/Part
                    # objects — while still putting the real field on the
                    # wire so Gemini 3.x's signature check is satisfied.
                    parts = []
                    for tc in m["tool_calls"]:
                        part = {
                            "function_call": {
                                "name": tc["name"],
                                "args": tc["args"],
                            },
                            # Echo the real signature back if we captured
                            # one; otherwise fall back to Google's
                            # documented skip sentinel so Gemini 3.x
                            # doesn't 400 on the next turn.
                            "thought_signature": tc.get("thought_signature") or _SKIP_SIGNATURE,
                        }
                        parts.append(part)
                    contents.append({"role": "model", "parts": parts})
                else:
                    contents.append(t.Content(role="model", parts=[t.Part(text=m["content"] or "")]))
            elif role == "tool":
                contents.append(t.Content(
                    role="user",
                    parts=[t.Part(function_response=t.FunctionResponse(
                        name=m["name"], response={"result": m["content"]}
                    ))],
                ))
        return contents

    def generate(self, messages: list[dict], tools: list[dict]) -> ModelResponse:
        t = self._types
        system_msgs = [m["content"] for m in messages if m["role"] == "system"]
        config = t.GenerateContentConfig(
            system_instruction="\n".join(system_msgs) if system_msgs else None,
            tools=self._to_gemini_tools(tools) if tools else None,
        )
        contents = self._to_gemini_contents(messages)
        resp = self._client.models.generate_content(
            model=self._model, contents=contents, config=config,
        )

        tool_calls, text_parts = [], []
        candidate = resp.candidates[0] if resp.candidates else None
        if candidate:
            for i, part in enumerate(candidate.content.parts):
                if getattr(part, "function_call", None):
                    fc = part.function_call
                    # Only the first function_call part in a turn carries a
                    # thought_signature (parallel calls after it won't) —
                    # capture whatever is there, None otherwise. Note: due
                    # to the same upstream SDK bug, this will currently
                    # often come back None even when Gemini did return a
                    # real signature, in which case _to_gemini_contents
                    # falls back to the skip sentinel on replay.
                    sig = getattr(part, "thought_signature", None)
                    tool_calls.append(ToolCall(id=f"call_{i}", name=fc.name, args=dict(fc.args), thought_signature=sig))
                elif getattr(part, "text", None):
                    text_parts.append(part.text)

        return ModelResponse(text="\n".join(text_parts) if text_parts else None, tool_calls=tool_calls)


# ------------------------------------------------ OpenAI-compatible base --
class OpenAICompatibleAdapter(ModelAdapter):
    """Shared implementation for any OpenAI-chat-compatible endpoint —
    used for both Ollama (local) and Groq (cloud), which only differ in
    base_url / api_key / model name."""

    def __init__(self, base_url: str, api_key: str, model: str):
        from openai import OpenAI
        self._client = OpenAI(base_url=base_url, api_key=api_key or "not-needed")
        self._model = model

    def _to_openai_tools(self, tools: list[dict]):
        return [
            {
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool["description"],
                    "parameters": tool["parameters"],
                },
            }
            for tool in tools
        ]

    def generate(self, messages: list[dict], tools: list[dict]) -> ModelResponse:
        oa_messages = []
        for m in messages:
            if m["role"] == "assistant" and m.get("tool_calls"):
                oa_messages.append({
                    "role": "assistant",
                    "content": m.get("content"),
                    "tool_calls": [
                        {
                            "id": tc["id"],
                            "type": "function",
                            "function": {"name": tc["name"], "arguments": json.dumps(tc["args"])},
                        }
                        for tc in m["tool_calls"]
                    ],
                })
            elif m["role"] == "tool":
                oa_messages.append({
                    "role": "tool", "tool_call_id": m["tool_call_id"],
                    "name": m["name"], "content": m["content"],
                })
            else:
                oa_messages.append({"role": m["role"], "content": m["content"]})

        kwargs = {"model": self._model, "messages": oa_messages}
        if tools:
            kwargs["tools"] = self._to_openai_tools(tools)

        resp = self._client.chat.completions.create(**kwargs)
        choice = resp.choices[0].message

        tool_calls = []
        if choice.tool_calls:
            for tc in choice.tool_calls:
                tool_calls.append(ToolCall(
                    id=tc.id, name=tc.function.name,
                    args=json.loads(tc.function.arguments or "{}"),
                ))

        return ModelResponse(text=choice.content, tool_calls=tool_calls)


class OllamaAdapter(OpenAICompatibleAdapter):
    def __init__(self):
        super().__init__(
            base_url=f"{MODEL.ollama_host.rstrip('/')}/v1",
            api_key="ollama",
            model=MODEL.ollama_model,
        )


class GroqAdapter(OpenAICompatibleAdapter):
    def __init__(self):
        if not MODEL.groq_api_key:
            raise EnvironmentError("GROQ_API_KEY is not set. Add your own key to .env.")
        super().__init__(
            base_url="https://api.groq.com/openai/v1",
            api_key=MODEL.groq_api_key,
            model=MODEL.groq_model,
        )


_ADAPTERS = {
    "gemini": GeminiAdapter,
    "ollama": OllamaAdapter,
    "groq": GroqAdapter,
}


def get_adapter(provider: str | None = None) -> ModelAdapter:
    provider = (provider or MODEL.provider).lower()
    if provider not in _ADAPTERS:
        raise ValueError(f"Unknown MODEL_PROVIDER '{provider}'. Choose from: {list(_ADAPTERS)}")
    return _ADAPTERS[provider]()