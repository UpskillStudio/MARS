import asyncio
import json

import anthropic

from mars.models import ErrorCategory, ErrorResult, Finding, SubagentResult
from mars.tools.web_search import TOOL_FETCH_URL, TOOL_WEB_SEARCH, dispatch as tool_dispatch

_SYSTEM = """\
You are a web research specialist. Your job is to find high-quality sources on an assigned \
sub-topic and extract key claims with evidence.

For each task:
1. Run web searches to find relevant sources (aim for 3-5 quality sources).
2. Fetch full content for the most promising URLs.
3. Extract specific, factual claims supported by direct evidence quotes.

When you have gathered enough evidence, output ONLY a JSON array — no prose before or after:
[
  {
    "claim": "specific factual assertion",
    "evidence_excerpt": "verbatim quote from the source",
    "source_url": "https://...",
    "publication_date": "YYYY-MM-DD or empty string",
    "relevance_score": 0.85
  }
]
"""

_MAX_ITERATIONS = 10
_MAX_RETRIES = 3


async def run(prompt: str, doc_store: dict = {}) -> SubagentResult:
    client = anthropic.AsyncAnthropic()
    messages: list[dict] = [{"role": "user", "content": prompt}]

    for _ in range(_MAX_ITERATIONS):
        response = await _create_with_retry(client, messages)
        if isinstance(response, ErrorResult):
            return response

        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "end_turn":
            text = next((b.text for b in response.content if hasattr(b, "text")), "")
            return _parse_findings(text)

        tool_results = []
        for block in response.content:
            if block.type == "tool_use":
                result = await tool_dispatch(block.name, block.input)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(result),
                })

        if tool_results:
            messages.append({"role": "user", "content": tool_results})

    return ErrorResult(
        errorCategory=ErrorCategory.transient,
        isRetryable=True,
        message="WebSearch agent exceeded max iterations without returning findings",
    )


async def _create_with_retry(
    client: anthropic.AsyncAnthropic, messages: list
) -> anthropic.types.Message | ErrorResult:
    for attempt in range(_MAX_RETRIES):
        try:
            return await client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=4096,
                system=_SYSTEM,
                tools=[TOOL_WEB_SEARCH, TOOL_FETCH_URL],
                messages=messages,
            )
        except anthropic.RateLimitError:
            if attempt < _MAX_RETRIES - 1:
                wait = 2 ** attempt * 10  # 10s, 20s, 40s
                await asyncio.sleep(wait)
            else:
                return ErrorResult(
                    errorCategory=ErrorCategory.transient,
                    isRetryable=True,
                    message="Rate limit exceeded after retries",
                )
        except Exception as e:
            return ErrorResult(
                errorCategory=ErrorCategory.transient,
                isRetryable=False,
                message=str(e),
            )
    return ErrorResult(
        errorCategory=ErrorCategory.transient,
        isRetryable=True,
        message="Retry exhausted",
    )


def _parse_findings(text: str) -> list[Finding] | ErrorResult:
    raw = _extract_json_array(text)
    if raw is None:
        return ErrorResult(
            errorCategory=ErrorCategory.validation,
            isRetryable=False,
            message=f"No JSON array in response: {text[:300]}",
        )
    try:
        data = json.loads(raw)
        if not isinstance(data, list):
            raise ValueError("Expected a JSON array")
        return [Finding(**item) for item in data]
    except Exception as e:
        return ErrorResult(
            errorCategory=ErrorCategory.validation,
            isRetryable=False,
            message=f"Failed to parse findings: {e}",
        )


def _extract_json_array(text: str) -> str | None:
    """Find the outermost JSON array in text using bracket counting."""
    start = text.find("[")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escape_next = False
    for i, ch in enumerate(text[start:], start):
        if escape_next:
            escape_next = False
            continue
        if ch == "\\" and in_string:
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None
