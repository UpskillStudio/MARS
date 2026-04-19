from mars.models import ErrorCategory, ErrorResult

# In-memory store populated by the coordinator before agents run
_doc_store: dict[str, str] = {}


def register(doc_store: dict[str, str]) -> None:
    """Called by the coordinator to load documents before spawning agents."""
    _doc_store.clear()
    _doc_store.update(doc_store)


async def read_document(document_id: str, page_range: list[int] | None = None) -> dict:
    if document_id not in _doc_store:
        return ErrorResult(
            errorCategory=ErrorCategory.validation,
            isRetryable=False,
            message=f"Document '{document_id}' not found. Available: {list(_doc_store.keys())}",
        ).model_dump()

    content = _doc_store[document_id]
    page_size = 3000  # chars per logical page
    page_count = max(1, len(content) // page_size)

    if page_range and len(page_range) == 2:
        start_char = (page_range[0] - 1) * page_size
        end_char = page_range[1] * page_size
        content = content[start_char:end_char]

    return {
        "text": content[:12000],
        "metadata": {"document_id": document_id, "total_chars": len(_doc_store[document_id])},
        "page_count": page_count,
    }


async def dispatch(name: str, inputs: dict) -> dict:
    if name == "read_document":
        return await read_document(**inputs)
    return {"error": f"Unknown tool: {name}"}


# ---------------------------------------------------------------------------
# Anthropic tool schemas
# ---------------------------------------------------------------------------

TOOL_READ_DOCUMENT = {
    "name": "read_document",
    "description": (
        "Read a specific document by its document_id. "
        "The document_id is provided by the coordinator in your prompt. "
        "DO NOT use for web searches or arbitrary URLs — use only for documents explicitly assigned to you."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "document_id": {"type": "string", "description": "Document ID as provided by the coordinator"},
            "page_range": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "Optional [start_page, end_page] to read a specific page range",
            },
        },
        "required": ["document_id"],
    },
}
