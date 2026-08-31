# Pearl Inference API Contract — v1

**Status: CLIENT IMPLEMENTED. SERVER REQUIRES DEPLOYMENT.**

Pearl's inference layer speaks the OpenAI Chat Completions wire protocol.
This is the industry standard — OpenRouter, the future `api.pearl.ai` gateway,
and every major model provider implement it. No custom serialisation is needed.

---

## Endpoint

```
POST {PEARL_INFERENCE_BASE_URL}/chat/completions
```

| Environment | Base URL |
|---|---|
| Development (current) | `https://openrouter.ai/api/v1` |
| Production (future) | `https://api.pearl.ai/v1` — **REQUIRES DEPLOYMENT** |

---

## Authentication

```
Authorization: Bearer <PEARL_INFERENCE_API_KEY>
```

| Context | Token format |
|---|---|
| Development | OpenRouter API key (`sk-or-v1-…`). Get one at openrouter.ai/keys. |
| Production (future) | Pearl device token (`prl_dev_…`), auto-issued on first run. |

The production device token path is the zero-configuration experience.
It does not yet exist.

---

## Request

Standard OpenAI Chat Completions body:

```json
{
  "model": "anthropic/claude-haiku-4-5-20251001",
  "messages": [
    {"role": "system", "content": "…"},
    {"role": "user", "content": "…"}
  ],
  "stream": true,
  "temperature": 0.2,
  "max_tokens": 4096,
  "tools": [],
  "tool_choice": "auto"
}
```

### Model routing

| Task | Env var | Default (via OpenRouter) |
|---|---|---|
| Chat | `PEARL_INFERENCE_CHAT_MODEL` | `anthropic/claude-haiku-4-5-20251001` |
| Planning | `PEARL_INFERENCE_PLAN_MODEL` | `anthropic/claude-sonnet-4-5-20251001` |

---

## Response

Non-streaming: standard `ChatCompletion` object.
Streaming: standard SSE — `data: {"choices":[{"delta":{"content":"…"}}]}` lines,
terminated by `data: [DONE]`.

---

## Error format

```json
{
  "error": {
    "message": "…",
    "type": "authentication_error | invalid_request_error | rate_limit_error | …",
    "code": "…"
  }
}
```

Pearl surfaces its own `PearlInferenceNotConfiguredError` before any network call
when `PEARL_INFERENCE_API_KEY` is empty, so the user sees a setup message,
not a raw HTTP 401.

---

## Timeouts

| Phase | Limit |
|---|---|
| Connection | 10 s |
| Non-streaming request | 60 s |
| Streaming session | 300 s |

Currently inherited from `OpenAICompatibleProvider` defaults (60 s).
Separate streaming timeout to be enforced when the production gateway exists.

---

## Retry policy

| Error class | Behaviour |
|---|---|
| Connection error, timeout, rate limit, 5xx | Exponential backoff, 3 attempts (handled by `LLMClient`) |
| Authentication error | No retry — surface immediately |
| `PearlInferenceNotConfiguredError` | No retry — surface immediately |

---

## Limits

| Parameter | Limit |
|---|---|
| Request body | 512 KB |
| Output (chat) | 4 096 tokens |
| Output (planning) | 8 192 tokens |

Currently OpenRouter's own limits apply. Pearl gateway limits to be defined at deployment.

---

## Security invariants

- `PEARL_INFERENCE_API_KEY` never appears in HTTP responses.
- `/api/provider` returns `configured: bool` — never the key itself.
- Errors are sanitised before reaching the browser.
- The browser never receives any provider credential.

---

## What remains before zero-configuration Pearl

1. Deploy `api.pearl.ai/v1` implementing this contract.
2. Implement device-token issuance (first-run, stored in `.env`).
3. Set `PEARL_INFERENCE_BASE_URL=https://api.pearl.ai/v1` as the compiled default.
4. Remove the OpenRouter setup step from documentation.

Steps 1–3 are server-side work. Pearl Core requires no code changes — only
the three env vars (`PEARL_INFERENCE_BASE_URL`, model names) need to change.
