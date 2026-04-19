import asyncio

import anthropic

from mars.models import ErrorCategory, ErrorResult, SubagentResult

_SYSTEM = """\
You are a professional research report writer. Format the provided synthesis into a \
publication-ready markdown report.

Report structure (follow exactly):
1. # Title
2. ## Executive Summary  (3-5 sentences covering scope, key findings, and significance)
3. ## [Theme Name]  (one section per theme — use the theme title as the heading)
   - Write 2-4 paragraphs synthesising the theme
   - Use inline citations: [Source N] where N matches the bibliography number
4. ## Conflicts & Uncertainties  (cover every conflict from the synthesis)
5. ## Conclusion  (2-3 sentences on overall implications)
6. ## Bibliography  (numbered list: [N] Title — URL — Date)

Rules:
- Every factual claim must have an inline [Source N] citation
- Use the source_url and publication_date from the provided citations to build the bibliography
- Do not invent sources or claims not present in the synthesis
- Output only the markdown report, nothing else
"""

_MAX_RETRIES = 3


async def run(prompt: str, doc_store: dict = {}) -> SubagentResult:
    client = anthropic.AsyncAnthropic()

    for attempt in range(_MAX_RETRIES):
        try:
            response = await client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=8096,
                system=_SYSTEM,
                messages=[{"role": "user", "content": prompt}],
            )
            return response.content[0].text
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
        message="ReportGen retry exhausted",
    )
