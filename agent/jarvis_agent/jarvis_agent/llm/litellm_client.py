# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""LiteLLM behind the model port: the model string picks the provider, no code change to switch.

    openrouter/<vendor>/<model>    one key, many models ($OPENROUTER_API_KEY)
    anthropic/<model>, openai/<model>, gemini/<model>, ...    direct ($ANTHROPIC_API_KEY, ...)
    ollama/<model>, openai/<model> + api_base    local or LAN servers (Ollama, vLLM, a LiteLLM proxy)

litellm is imported here only, from requirements-llm.txt (hash-locked). Its model cost map is the
bundled one, so nothing is fetched at import, and its telemetry is off. The stable prefix of the
prompt is marked cacheable for the providers that take the hint.
"""

import logging
import os

log = logging.getLogger("jarvis_agent.llm")


class LiteLLMClient:
    def __init__(
        self,
        model,
        api_base="",
        api_key="",
        timeout_s=60.0,
        max_retries=2,
        max_output_tokens=2048,
        temperature=None,
        extra=None,
    ):
        os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
        import litellm
        import openai
        from litellm.utils import supports_prompt_caching

        litellm.telemetry = False
        litellm.suppress_debug_info = True
        self.litellm = litellm
        # What litellm raises for a provider's answer (all mapped onto openai's errors) or a budget.
        self.errors = (openai.OpenAIError, litellm.exceptions.BudgetExceededError)
        self.name = model
        try:
            self.provider = litellm.get_llm_provider(model, api_base=api_base or None)[1]
        except Exception:  # noqa: BLE001 - only a label
            self.provider = model.split("/", 1)[0]
        try:
            self.caching = bool(supports_prompt_caching(model=model))
        except Exception:  # noqa: BLE001 - a hint, not a requirement
            self.caching = False
        self.api_base = api_base
        self._key = api_key
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self.max_output_tokens = max_output_tokens
        self.temperature = temperature
        self.extra = dict(extra or {})  # passed through (e.g. reasoning_effort; mock_response in tests)

    def supports_tools(self):
        try:
            return bool(self.litellm.supports_function_calling(model=self.name))
        except Exception:  # noqa: BLE001 - unknown model: litellm cannot tell
            return False

    def _messages(self, messages):
        if not self.caching or not messages or messages[0]["role"] != "system":
            return messages
        system = {
            "role": "system",
            "content": [
                {"type": "text", "text": messages[0]["content"], "cache_control": {"type": "ephemeral"}}
            ],
        }
        return [system, *messages[1:]]

    def complete(self, messages, tools, tool_choice="auto"):
        from jarvis_agent.llm.base import Completion, ModelUnavailable, ToolCall

        kwargs = {
            "model": self.name,
            "messages": self._messages(messages),
            "timeout": self.timeout_s,
            "num_retries": self.max_retries,
            "max_tokens": self.max_output_tokens,
            "drop_params": True,  # what a provider does not take is left out, not an error
            **self.extra,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice
        if self.api_base:
            kwargs["api_base"] = self.api_base
        if self._key:
            kwargs["api_key"] = self._key
        if self.temperature is not None:
            kwargs["temperature"] = self.temperature
        try:
            r = self.litellm.completion(**kwargs)
        except self.errors as e:
            raise ModelUnavailable(self._safe(f"{type(e).__name__}: {e}")) from e
        except Exception as e:  # an unmapped litellm failure: the heuristic analyst still answers
            log.warning("%s: unexpected %s", self.name, type(e).__name__, exc_info=True)
            raise ModelUnavailable(self._safe(f"{type(e).__name__}: {e}")) from e
        if not r.choices:
            raise ModelUnavailable("the model returned no choices")
        choice = r.choices[0]
        message = choice.message
        calls = [
            ToolCall(
                id=c.id or f"call_{i}",
                name=c.function.name or "",
                arguments=c.function.arguments or "{}",
            )
            for i, c in enumerate(message.tool_calls or [])
        ]
        usage = getattr(r, "usage", None)
        cost = (getattr(r, "_hidden_params", None) or {}).get("response_cost")
        if cost is None:
            try:
                cost = self.litellm.completion_cost(completion_response=r)
            except Exception:  # noqa: BLE001 - a model without a price (local): free
                cost = 0.0
        return Completion(
            text=message.content or "",
            tool_calls=calls,
            input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            output_tokens=getattr(usage, "completion_tokens", 0) or 0,
            cost_usd=float(cost or 0.0),
            model=r.model or self.name,
            finish_reason=choice.finish_reason or "",
        )

    def _safe(self, text):
        """An error for the logs and the insight: short, and never the key."""
        if self._key:
            text = text.replace(self._key, "***")
        return " ".join(text.split())[:300]
