"""LLM analysis module. Provider and key come from .env (see .env.example):

  HP_LLM_PROVIDER   auto | anthropic | openai          (auto = anthropic if ANTHROPIC_API_KEY is set, else openai)
  ANTHROPIC_API_KEY Claude API key
  OPENAI_API_KEY    OpenAI (or any OpenAI-compatible endpoint) key
  HP_LLM_MODEL      model id (default claude-opus-5-5 for anthropic; required for openai)
  HP_LLM_EFFORT     low | medium | high (Claude effort, default medium)
  OPENAI_BASE_URL   optional, for OpenAI-compatible servers (default https://api.openai.com/v1)
  ANTHROPIC_BASE_URL optional, a Claude gateway / proxy speaking the Messages API (default https://api.anthropic.com)
  ANTHROPIC_AUTH_TOKEN optional, Bearer token for such a gateway (used when ANTHROPIC_API_KEY is empty)

A platform admin can override provider / model / effort / endpoint / on-off in Admin → Settings → AI analyst (stored in
settings_kv["llm"]; keys are never stored there). Without a key, when disabled, or for 60 s after a failure, the module returns a deterministic rule-based brief, clearly labelled, so the screens still work.
"""
