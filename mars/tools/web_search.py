import os

import httpx
from tavily import TavilyClient

from mars.models import ErrorCategory, ErrorResult

_tavily_client: TavilyClient | None = None


def _tavily() -> TavilyClient:
    global _tavily_client
    if _tavily_client is None:
        _tavily_client = TavilyClient(api_key=os.environ["TAVILY_API_KEY"])
    return _tavily_client


# ---------------------------------------------------------------------------
# Tool functions
# ---------------------------------------------------------------------------

async def web_search(query: str, max_results: int = 5) -> list[dict] | dict:
    try:
        response = _tavily().search(query=query, max_results=max_results)
        return [
            {
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "snippet": r.get("content", ""),
                "date": r.get("published_date", ""),
            }
            for r in response.get("results", [])
        ]
    except Exception as e:
        return ErrorResult(
            errorCategory=ErrorCategory.transient,
            isRetryable=True,
            attemptedQuery=query,
            message=str(e),
        ).model_dump()


async def fetch_url(url: str) -> dict:
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as http:
            r = await http.get(url, headers={"User-Agent": "MARS-Research/1.0"})
            r.raise_for_status()
            return {"content": r.text[:8000], "title": "", "date": "", "url": url}
    except Exception as e:
        return ErrorResult(
            errorCategory=ErrorCategory.transient,
            isRetryable=True,
            message=str(e),
        ).model_dump()


async def dispatch(name: str, inputs: dict) -> list | dict:
    if name == "web_search":
        return await web_search(**inputs)
    if name == "fetch_url":
        return await fetch_url(**inputs)
    return {"error": f"Unknown tool: {name}"}


# ---------------------------------------------------------------------------
# Anthropic tool schemas
# ---------------------------------------------------------------------------

TOOL_WEB_SEARCH = {
    "name": "web_search",
    "description": (
        "Search the web for sources on a topic. Returns [{title, url, snippet, date}]. "
        "Use fetch_url to retrieve full article content. "
        "DO NOT use for documents already provided in your context."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Natural language search query"},
            "max_results": {"type": "integer", "description": "Number of results to return (1-10)", "default": 5},
        },
        "required": ["query"],
    },
}

TOOL_FETCH_URL = {
    "name": "fetch_url",
    "description": (
        "Fetch the full text of a URL. Use after web_search to read complete articles. "
        "Returns up to 8000 characters of page content."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Full URL to fetch"},
        },
        "required": ["url"],
    },
}
