from __future__ import annotations

import json
import os
from typing import Any


class JsonModelError(RuntimeError):
    pass


class OmbreAdapter:
    """Narrow boundary around Ombre internals used by Night Fall."""

    def __init__(self, ombre_server: Any):
        self.ombre_server = ombre_server

    @property
    def config(self) -> dict:
        return getattr(self.ombre_server, "config", {}) or {}

    @property
    def dehydrator(self) -> Any:
        return getattr(self.ombre_server, "dehydrator", None)

    @property
    def embedding_engine(self) -> Any:
        return getattr(self.ombre_server, "embedding_engine", None)

    async def list_candidate_buckets(self) -> list[dict]:
        bucket_mgr = getattr(self.ombre_server, "bucket_mgr", None)
        if bucket_mgr is None:
            return []
        buckets = await bucket_mgr.list_all(include_archive=False)
        candidates = []
        for bucket in buckets:
            meta = bucket.get("metadata", {})
            if meta.get("type") in ("permanent", "feel", "archived"):
                continue
            if meta.get("pinned") or meta.get("protected"):
                continue
            if not bucket.get("content"):
                continue
            candidates.append(bucket)
        return candidates

    async def call_json_model(
        self,
        system_prompt: str,
        payload: dict,
        *,
        max_tokens: int = 700,
        temperature: float = 0.7,
        schema: dict | None = None,
    ) -> dict | list | None:
        raw_errors = []
        for _ in range(2):
            raw = await self._call_raw_model(
                system_prompt,
                payload,
                max_tokens=max_tokens,
                temperature=temperature,
                schema=schema,
            )
            cleaned = raw.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            try:
                return json.loads(cleaned)
            except json.JSONDecodeError as exc:
                raw_errors.append(str(exc))
                continue
        raise JsonModelError(f"LLM returned invalid JSON after retry: {'; '.join(raw_errors)}")

    async def _call_raw_model(
        self,
        system_prompt: str,
        payload: dict,
        *,
        max_tokens: int,
        temperature: float,
        schema: dict | None = None,
    ) -> str:
        provider = os.environ.get("NIGHT_FALL_LLM_PROVIDER", "").strip().lower()
        if not provider:
            provider = "anthropic" if os.environ.get("ANTHROPIC_API_KEY") else "deepseek"

        if provider == "anthropic":
            return await self._call_anthropic(
                system_prompt,
                payload,
                max_tokens=max_tokens,
                temperature=temperature,
                schema=schema,
            )
        # DeepSeek path ignores schema (no Tool Use enforcement); it relies on
        # the prompt + post-parse validation in the upstream caller.

        dehydrator = self.dehydrator
        client = getattr(dehydrator, "client", None)
        model = getattr(dehydrator, "model", "deepseek-chat")

        if not client or not getattr(dehydrator, "api_available", False):
            env_key = os.environ.get("DEEPSEEK_API_KEY", "")
            if not env_key:
                raise JsonModelError(
                    "No LLM provider available. Configure Ombre's dehydration API or set DEEPSEEK_API_KEY / ANTHROPIC_API_KEY."
                )
            try:
                from openai import AsyncOpenAI
            except ImportError as exc:
                raise JsonModelError("The openai package is required for DEEPSEEK_API_KEY provider.") from exc
            client = AsyncOpenAI(
                api_key=env_key,
                base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
                timeout=60.0,
            )
            model = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")

        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            max_tokens=max_tokens,
            temperature=temperature,
        )
        if not response.choices:
            raise JsonModelError("LLM provider returned no choices.")
        raw = response.choices[0].message.content or ""
        if not raw.strip():
            raise JsonModelError("LLM provider returned an empty response.")
        return raw

    async def _call_anthropic(
        self,
        system_prompt: str,
        payload: dict,
        *,
        max_tokens: int,
        temperature: float,
        schema: dict | None = None,
    ) -> str:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            raise JsonModelError("ANTHROPIC_API_KEY is required when provider=anthropic.")
        try:
            from anthropic import AsyncAnthropic
        except ImportError as exc:
            raise JsonModelError("The anthropic package is required for the Anthropic provider.") from exc

        base_url = os.environ.get("ANTHROPIC_BASE_URL", "").strip() or None
        model = os.environ.get("NIGHT_FALL_LLM_MODEL", "claude-opus-4-7")

        client_kwargs: dict = {"api_key": api_key, "timeout": 90.0}
        if base_url:
            client_kwargs["base_url"] = base_url
        client = AsyncAnthropic(**client_kwargs)

        # Use Tool Use to guarantee well-formed JSON. If the caller provides a
        # schema, it's enforced by Anthropic's tool validation; otherwise we
        # fall back to a permissive "any object" schema.
        tool_name = "submit_response"
        input_schema = schema if schema is not None else {
            "type": "object",
            "additionalProperties": True,
        }
        tool_instruction = (
            f"\n\nReturn your response by calling the `{tool_name}` tool. Pass "
            "the JSON structure described above as the tool's arguments — do "
            "not write the JSON as plain text."
        )

        response = await client.messages.create(
            model=model,
            system=system_prompt + tool_instruction,
            messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
            tools=[{
                "name": tool_name,
                "description": "Submit the structured JSON response described in the system prompt.",
                "input_schema": input_schema,
            }],
            tool_choice={"type": "tool", "name": tool_name},
            max_tokens=max_tokens,
            temperature=temperature,
        )
        if not response.content:
            raise JsonModelError("Anthropic provider returned no content.")

        # Preferred: tool_use block with structured input
        for block in response.content:
            if getattr(block, "type", "") == "tool_use" and getattr(block, "name", "") == tool_name:
                return json.dumps(block.input, ensure_ascii=False)

        # Fallback: some proxies may not relay tool_use blocks correctly; try
        # to recover plain text and let the upstream JSON parser have a go.
        parts = [getattr(b, "text", "") for b in response.content if getattr(b, "type", "") == "text"]
        raw = "".join(parts).strip()
        if not raw:
            raise JsonModelError(
                "Anthropic provider returned neither a tool_use block nor text. "
                "The proxy may not support tool use."
            )
        return raw
