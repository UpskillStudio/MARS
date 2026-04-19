import asyncio
import json

import anthropic

from mars.models import DocFinding, ErrorCategory, ErrorResult, SubagentResult
from mars.tools.document import TOOL_READ_DOCUMENT, dispatch as doc_dispatch
from mars.tools.web_search import TOOL_FETCH_URL, dispatch as web_dispatch

_SYSTEM = """\
You are a document analysis specialist. Your job is to extract key claims and supporting \
evidence from documents provided by the coordinator.

For each task:
1. Read the assigned document(s) using read_document.
2. Use fetch_url if you need to retrieve a referenced URL for additional context.
3. Extract specific, factual claims with direct evidence quotes, page numbers, and section names.

DO NOT perform web searches — work only with the provided documents.

When you have extracted sufficient evidence, output ONLY a JSON array — no prose before or after:
[
  {
    "claim": "specific factual assertion",
    "evidence_excerpt": "verbatim quote from the document",
    "document_name": "filename or document_id",
    "page_number": 1,
    "section": "section or chapter name, or empty string",
    "publication_date": "YYYY-MM-DD or empty string"
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
                if block.name == "read_document":
                    result = await doc_dispatch(block.name, block.input)
                else:
                    result = await web_dispatch(block.name, block.input)
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
        message="DocAnalysis agent exceeded max iterations",
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
                tools=[TOOL_READ_DOCUMENT, TOOL_FETCH_URL],
                messages=messages,
            )
        except anthropic.RateLimitError:
            if attempt < _MAX_RETRIES - 1:
                await asyncio.sleep(2 ** attempt * 10)
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


def _parse_findings(text: str) -> list[DocFinding] | ErrorResult:
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
        return [DocFinding(**item) for item in data]
    except Exception as e:
        return ErrorResult(
            errorCategory=ErrorCategory.validation,
            isRetryable=False,
            message=f"Failed to parse doc findings: {e}",
        )


def _extract_json_array(text: str) -> str | None:
    start = text.find("[")
    if start == -1:
        return None
    depth, in_string, escape_next = 0, False, False
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
