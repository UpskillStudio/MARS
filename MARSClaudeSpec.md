# Multi Agent Research System
# spec.md — Project Specification

---

## 1. Project Overview

The **Multi Agent Research System** is a Python-based agentic AI platform that coordinates multiple specialized AI agents to perform deep, structured research tasks. Each agent is responsible for a distinct phase of the research pipeline — from topic decomposition and web search, to synthesis, citation, and final report generation. Agents communicate through a shared orchestration layer and produce structured, auditable outputs.

---

## 2. Goals

- [ ] Orchestrate multiple AI agents to collaboratively complete complex research tasks
- [ ] Enable parallel and sequential agent execution based on task dependency graphs
- [ ] Provide a reusable utility layer (`utils/`) shared across all agents
- [ ] Store all research outputs, logs, and documentation under a structured `docs/` folder
- [ ] Support extensibility — new agent types can be added without modifying core logic
- [ ] Produce a final structured research report (Markdown + JSON) from every run

### Non-Goals

- This system is not a real-time chat interface
- It does not manage user authentication or sessions
- It does not provide a GUI (CLI and API only in v1)

---

## 3. Technology Stack

| Layer | Technology |
|---|---|
| Language | Python 3.11+ |
| AI / LLM | Anthropic Claude API (`anthropic` SDK) |
| Web Search | `duckduckgo-search` or Anthropic web search tool |
| Async Execution | `asyncio` + `aiohttp` |
| Data Validation | `pydantic` v2 |
| Config Management | `python-dotenv` + `pydantic-settings` |
| Logging | Python `logging` (structured JSON logs) |
| Testing | `pytest` + `pytest-asyncio` |
| Dependency Management | `pip` + `requirements.txt` |

---

## 4. Folder Structure

```
multi_agent_research_system/
│
├── docs/                          # All documentation and research outputs
│   ├── architecture.md            # System architecture overview
│   ├── agent_specs.md             # Individual agent specifications
│   ├── api_reference.md           # Internal API/interface reference
│   └── outputs/                   # Generated research reports per run
│       └── {run_id}/
│           ├── report.md
│           ├── citations.json
│           └── run_log.json
│
├── agents/                        # Individual agent implementations
│   ├── __init__.py
│   ├── base_agent.py              # Abstract base class for all agents
│   ├── orchestrator_agent.py      # Coordinates task delegation
│   ├── research_agent.py          # Performs web search and data gathering
│   ├── analysis_agent.py          # Synthesizes and cross-references findings
│   ├── writer_agent.py            # Drafts final report sections
│   └── citation_agent.py          # Validates and formats citations
│
├── utils/                         # Reusable shared components
│   ├── __init__.py
│   ├── llm_client.py              # Anthropic API wrapper (shared by all agents)
│   ├── search_client.py           # Web search utility
│   ├── prompt_builder.py          # Prompt templating and construction
│   ├── task_queue.py              # Async task queue manager
│   ├── logger.py                  # Structured logging utility
│   ├── file_handler.py            # Read/write helpers for docs/outputs
│   ├── validators.py              # Pydantic models for agent I/O validation
│   └── config.py                  # Centralised config loading (.env)
│
├── tests/                         # Unit and integration tests
│   ├── __init__.py
│   ├── test_orchestrator.py
│   ├── test_research_agent.py
│   ├── test_analysis_agent.py
│   ├── test_utils.py
│   └── fixtures/
│       └── sample_research_task.json
│
├── main.py                        # Entry point — CLI runner
├── requirements.txt               # Python dependencies
├── .env.example                   # Environment variable template
├── CLAUDE.md                      # Claude Code instructions for this project
└── README.md                      # Project overview and setup guide
```

---

## 5. File Header Convention

Every `.py` file in this project **must** include the following header comment block at the top:

```python
# ============================================================
# Multi Agent Research System
# ============================================================
# File   : <filename>.py
# Purpose: <one-line description>
# ============================================================
```

---

## 6. Agent Specifications

### 6.1 Base Agent (`agents/base_agent.py`)
- Abstract class defining the `run(task: ResearchTask) -> AgentResult` interface
- Holds reference to shared `LLMClient` and `Logger` from `utils/`
- Provides retry logic and error handling hooks

### 6.2 Orchestrator Agent (`agents/orchestrator_agent.py`)
- Accepts a high-level research query from the user
- Decomposes the query into a directed task graph
- Assigns sub-tasks to specialist agents (Research, Analysis, Writer, Citation)
- Aggregates outputs into a final result
- Emits structured run logs to `docs/outputs/{run_id}/`

### 6.3 Research Agent (`agents/research_agent.py`)
- Receives a specific sub-topic or question
- Uses `utils/search_client.py` to retrieve web sources
- Summarises findings using the LLM
- Returns structured `ResearchFindings` (Pydantic model)

### 6.4 Analysis Agent (`agents/analysis_agent.py`)
- Receives multiple `ResearchFindings` objects
- Cross-references, identifies contradictions, and extracts key insights
- Returns a structured `AnalysisSummary`

### 6.5 Writer Agent (`agents/writer_agent.py`)
- Takes an `AnalysisSummary` and writes a formatted report section
- Follows a consistent Markdown report template
- Returns a `ReportSection` object

### 6.6 Citation Agent (`agents/citation_agent.py`)
- Validates URLs and source metadata from `ResearchFindings`
- Formats citations to a specified style (default: APA)
- Returns a `CitationList`

---

## 7. Reusable Utils (`utils/`) Specifications

### `utils/llm_client.py`
- Wraps `anthropic.Anthropic` client
- Exposes `async def complete(prompt, system, model, max_tokens) -> str`
- Handles rate limiting, retries (exponential backoff), and token tracking

### `utils/search_client.py`
- Wraps web search (DuckDuckGo or Anthropic tool-use search)
- Exposes `async def search(query: str, num_results: int) -> list[SearchResult]`

### `utils/prompt_builder.py`
- Template-based prompt construction using Python f-strings or Jinja2
- Loads templates from `utils/templates/` directory
- Exposes `build_prompt(template_name, **kwargs) -> str`

### `utils/task_queue.py`
- Async FIFO task queue with priority support
- Exposes `enqueue(task)`, `dequeue()`, `run_all()`

### `utils/logger.py`
- Structured JSON logger wrapping Python `logging`
- Writes to stdout (dev) and `docs/outputs/{run_id}/run_log.json` (prod)

### `utils/file_handler.py`
- `save_report(run_id, content)` → saves to `docs/outputs/{run_id}/report.md`
- `save_citations(run_id, citations)` → saves to `docs/outputs/{run_id}/citations.json`
- `load_task(path)` → loads a research task from JSON file

### `utils/validators.py`
Pydantic v2 models:
- `ResearchTask` — input schema
- `ResearchFindings` — research agent output
- `AnalysisSummary` — analysis agent output
- `ReportSection` — writer agent output
- `CitationList` — citation agent output
- `AgentResult` — generic wrapper with status, data, errors

### `utils/config.py`
- Loads from `.env` using `pydantic-settings`
- Exposes: `ANTHROPIC_API_KEY`, `DEFAULT_MODEL`, `MAX_TOKENS`, `LOG_LEVEL`, `OUTPUT_DIR`

---

## 8. Data Flow

```
User Input (CLI query)
        ↓
  OrchestratorAgent
        ↓ decomposes into sub-tasks
  ┌─────┬──────┬──────┐
  ↓     ↓      ↓      ↓
ResearchAgent × N  (parallel)
        ↓
  AnalysisAgent    (sequential)
        ↓
  WriterAgent      (sequential)
        ↓
  CitationAgent    (sequential)
        ↓
  Final Report → docs/outputs/{run_id}/
```

---

## 9. Entry Point (`main.py`)

```
Usage:
  python main.py --query "Impact of LLMs on scientific research" --output-dir docs/outputs/
  python main.py --task-file tests/fixtures/sample_research_task.json
```

CLI arguments:
- `--query` — free-text research question
- `--task-file` — path to a pre-defined JSON task file
- `--model` — Claude model to use (default: `claude-sonnet-4-20250514`)
- `--output-dir` — override default output directory
- `--log-level` — DEBUG / INFO / WARNING (default: INFO)

---

## 10. Environment Variables (`.env.example`)

```
ANTHROPIC_API_KEY=your_api_key_here
DEFAULT_MODEL=claude-sonnet-4-20250514
MAX_TOKENS=4096
LOG_LEVEL=INFO
OUTPUT_DIR=docs/outputs
```

---

## 11. Testing Requirements

- All `utils/` modules must have unit tests in `tests/test_utils.py`
- Each agent must have at least one integration test using mocked LLM responses
- Use `pytest-asyncio` for all async tests
- Minimum 80% code coverage target

---

## 12. Coding Conventions

- All files begin with the standard file header (see Section 5)
- Use `async/await` for all I/O-bound operations
- All agent inputs/outputs validated with Pydantic models
- No hardcoded API keys or secrets — always load from `config.py`
- Docstrings required on all public classes and functions (Google style)
- Type hints required on all function signatures

---

## 13. CLAUDE.md Instructions Reference

When using Claude Code in VS Code, Claude should:
1. Always read `spec.md` before starting any task
2. Place all reusable components in `utils/`
3. Store documentation and outputs in `docs/`
4. Add the standard file header to every `.py` file created
5. Follow async patterns defined in `utils/task_queue.py`
6. Validate all agent I/O against Pydantic models in `utils/validators.py`
