import os

import anthropic
from tavily import TavilyClient

from mars.models import ErrorCategory, ErrorResult

_tavily_client: TavilyClient | None = None


def _tavily() -> TavilyClient:
    global _tavily_client
    if _tavily_client is None:
        _tavily_client = TavilyClient(api_key=os.environ["TAVILY_API_KEY"])
    return _tavily_client


async def verify_fact(claim: str, context: str = "") -> dict:
    """
    Verify a specific factual claim via a targeted Tavily search + Claude Haiku judgement.
    Scoped to simple fact-checks (dates, statistics, names). Complex verifications
    should be delegated back to the coordinator → WebSearch subagent.
    """
    try:
        query = claim if not context else f"{claim} {context}"
        results = _tavily().search(query=query, max_results=3)
        snippets = " ".join(r.get("content", "") for r in results.get("results", []))
        source_url = results["results"][0]["url"] if results.get("results") else ""

        client = anthropic.AsyncAnthropic()
        response = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=256,
            messages=[{
                "role": "user",
                "content": (
                    f"Claim: {claim}\n\n"
                    f"Evidence snippets:\n{snippets[:2000]}\n\n"
                    "Based solely on the evidence above, is this claim verified? "
                    "Reply with JSON only: "
                    '{"verified": true/false, "confidence": 0.0-1.0, "note": "brief reason"}'
                ),
            }],
        )
        text = response.content[0].text.strip()
        import json
        data = json.loads(text[text.find("{") : text.rfind("}") + 1])
        return {"verified": data.get("verified", False), "source_url": source_url, "confidence": data.get("confidence", 0.5)}

    except Exception as e:
        return ErrorResult(
            errorCategory=ErrorCategory.transient,
            isRetryable=True,
            message=str(e),
        ).model_dump()


async def dispatch(name: str, inputs: dict) -> dict:
    if name == "verify_fact":
        return await verify_fact(**inputs)
    return {"error": f"Unknown tool: {name}"}


TOOL_VERIFY_FACT = {
    "name": "verify_fact",
    "description": (
        "Verify a specific factual claim (date, statistic, name, or simple assertion) "
        "via a targeted web search. Use ONLY for simple fact-checks. "
        "For complex verifications requiring deep research, return a gap in your output instead."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "claim": {"type": "string", "description": "The specific claim to verify"},
            "context": {"type": "string", "description": "Optional context to narrow the search"},
        },
        "required": ["claim"],
    },
}
