import asyncio
import json

import anthropic

from mars.models import (
    Citation, CoverageGap, Conflict, DocFinding, ErrorCategory,
    ErrorResult, Finding, SubagentResult, SynthesisOutput, Theme,
)
from mars.tools.verify_fact import TOOL_VERIFY_FACT, dispatch as verify_dispatch

_SYSTEM = """\
You are a research synthesis specialist. Combine findings from multiple sources into a \
structured synthesis. Your job is to find patterns, surface conflicts, and identify gaps — \
not to summarise each source individually.

Tasks:
1. Identify 4-8 major themes that emerge across the findings.
2. Note conflicts where sources materially disagree (different statistics, opposing conclusions).
3. Identify coverage gaps: sub-domains with fewer than 2 corroborating sources.
4. Use verify_fact for any specific statistic or date you want to spot-check (simple facts only).
5. Preserve source attribution — every theme and conflict must cite its sources.

Output ONLY a JSON object — no prose before or after:
{
  "themes": [
    {
      "title": "Theme name",
      "summary": "2-3 sentence synthesis across sources",
      "supporting_claims": ["claim 1", "claim 2"],
      "citations": [{"source_url": "...", "title": "", "publication_date": "", "excerpt": ""}]
    }
  ],
  "conflicts": [
    {
      "description": "What the sources disagree on",
      "positions": ["Position A (source X)", "Position B (source Y)"],
      "citations": [{"source_url": "...", "title": "", "publication_date": "", "excerpt": ""}]
    }
  ],
  "gaps": [
    {
      "sub_domain": "Sub-domain name",
      "source_count": 1,
      "note": "Only one source; needs corroboration"
    }
  ],
  "citations": [{"source_url": "...", "title": "", "publication_date": "", "excerpt": ""}]
}
"""

_MAX_ITERATIONS = 15
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
            return _parse_synthesis(text)

        tool_results = []
        for block in response.content:
            if block.type == "tool_use":
                result = await verify_dispatch(block.name, block.input)
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
        message="Synthesis agent exceeded max iterations",
    )


async def _create_with_retry(
    client: anthropic.AsyncAnthropic, messages: list
) -> anthropic.types.Message | ErrorResult:
    for attempt in range(_MAX_RETRIES):
        try:
            return await client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=8096,
                system=_SYSTEM,
                tools=[TOOL_VERIFY_FACT],
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


def _parse_synthesis(text: str) -> SynthesisOutput | ErrorResult:
    start = text.find("{")
    end = text.rfind("}") + 1
    if start == -1 or end == 0:
        return ErrorResult(
            errorCategory=ErrorCategory.validation,
            isRetryable=False,
            message=f"No JSON object in synthesis response: {text[:300]}",
        )
    try:
        data = json.loads(text[start:end])
        return SynthesisOutput(
            themes=[Theme(**t) for t in data.get("themes", [])],
            conflicts=[Conflict(**c) for c in data.get("conflicts", [])],
            gaps=[CoverageGap(**g) for g in data.get("gaps", [])],
            citations=[Citation(**c) for c in data.get("citations", [])],
        )
    except Exception as e:
        return ErrorResult(
            errorCategory=ErrorCategory.validation,
            isRetryable=False,
            message=f"Failed to parse synthesis: {e}",
        )
