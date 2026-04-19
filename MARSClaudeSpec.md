# Multi-Agent Research System
# spec.md — As-Built Project Specification

> This document reflects the system as implemented. The original preliminary spec has been superseded by `MARS-design.md` (design) and `architecture-overview.md` (implementation walkthrough).

---

## 1. Project Overview

MARS is a Python-based multi-agent AI platform that produces comprehensive, cited research reports on any topic. A coordinator agent orchestrates four specialized subagents — Web Search, Document Analysis, Synthesis, and Report Generation — in a hub-and-spoke topology. The system runs via CLI or Streamlit UI and is observable via Langfuse tracing.

---

## 2. Goals (as built)

- [x] Orchestrate multiple AI agents to collaboratively complete complex research tasks
- [x] Enable parallel and sequential agent execution via `asyncio` + `Semaphore`
- [x] Produce a final structured research report (Markdown) with inline citations and bibliography
- [x] Support document ingestion (`--docs` flag / UI uploader) alongside web research
- [x] Iterative gap-filling refinement: re-delegate until ≥2 sources per sub-domain
- [x] Streamlit UI with live progress streaming
- [x] Langfuse observability with token/cost tracking

### Non-Goals (still true)

- Not a real-time chat interface
- Does not manage user authentication or sessions

---

## 3. Technology Stack

| Layer | Technology | Notes |
|---|---|---|
| Language | Python 3.11+ | |
| LLM SDK | `anthropic ≥ 0.40.0` | Async `AsyncAnthropic` client |
| Web Search | `tavily-python ≥ 0.5.0` | AI-structured results; replaces DuckDuckGo from original spec |
| HTTP | `httpx ≥ 0.27.0` | Async `fetch_url` tool |
| Data Validation | `pydantic ≥ 2.7.0` | All inter-agent payloads are typed Pydantic models |
| Config | `python-dotenv` | `.env` for API keys |
| UI | `streamlit ≥ 1.35.0` | `app.py` — replaces CLI-only from original spec |
| Observability | `langfuse ≥ 2.0.0` | Opt-in tracing; graceful no-op if keys absent |
| OTel instrumentation | `opentelemetry-instrumentation-anthropic ≥ 0.60.0` | Auto-captures model, tokens, cost |
| Testing | `pytest ≥ 8.0`, `pytest-asyncio ≥ 0.23` | |

---

## 4. Directory Structure (as built)

```
MARS/
├── mars/
│   ├── __init__.py
│   ├── coordinator.py          # Coordinator — decompose, research, refine, synthesize, report
│   ├── models.py               # All Pydantic models for inter-agent payloads
│   ├── observability.py        # Langfuse tracing (fail-safe decorators + AnthropicInstrumentor)
│   ├── agents/
│   │   ├── __init__.py
│   │   ├── web_search.py       # ReAct loop — web_search + fetch_url tools
│   │   ├── doc_analysis.py     # ReAct loop — read_document + fetch_url tools
│   │   ├── synthesis.py        # Synthesis — verify_fact tool
│   │   └── report_gen.py       # Report generation — no tools (pure generation)
│   └── tools/
│       ├── __init__.py
│       ├── web_search.py       # web_search (Tavily) + fetch_url (httpx)
│       ├── document.py         # read_document — in-memory doc store
│       └── verify_fact.py      # verify_fact — Tavily + Claude Haiku
├── app.py                      # Streamlit UI
├── main.py                     # CLI entry point
├── pyproject.toml
├── .env.example
└── output/                     # Generated reports (gitignored)
```

---

## 5. Agent Specifications (as built)

### 5.1 Coordinator (`mars/coordinator.py`)

- Model: `claude-sonnet-4-6`
- Decomposes topic into ≥5 sub-domains via forced `tool_use` (structured JSON guaranteed)
- Spawns subagents via `asyncio.gather()` + `Semaphore(max_concurrency)` — not LLM tool calls
- Two search modes: **direct** (Tavily only, default) or **adaptive** (ReAct loop, opt-in)
- Iterative refinement: re-delegates gaps until `ResearchManifest.coverage_sufficient()` == True
- Instrumented with `@observe_trace("mars-run")` and per-phase `@observe_span` decorators

### 5.2 Web Search Subagent (`mars/agents/web_search.py`)

- Model: `claude-haiku-4-5-20251001`
- Tools: `web_search`, `fetch_url`
- ReAct loop: handles `tool_use` stop_reason, appends tool results, loops until `end_turn`
- Retry: catches `RateLimitError`, backoff 10s → 20s → 40s, up to 3 retries
- Returns: `list[Finding]`

### 5.3 Document Analysis Subagent (`mars/agents/doc_analysis.py`)

- Model: `claude-haiku-4-5-20251001`
- Tools: `read_document`, `fetch_url`
- Only activates when `--docs` / UI uploader provides documents
- Returns: `list[DocFinding]`

### 5.4 Synthesis Subagent (`mars/agents/synthesis.py`)

- Model: `claude-sonnet-4-6`, max_tokens=8096
- Tools: `verify_fact` (scoped — simple fact-checks only)
- Returns: `SynthesisOutput {themes, conflicts, gaps, citations}`

### 5.5 Report Generation Subagent (`mars/agents/report_gen.py`)

- Model: `claude-sonnet-4-6`
- No tools — pure generation from synthesis output
- Returns: markdown string with inline `[Source N]` citations and bibliography

---

## 6. Data Models (`mars/models.py`)

| Model | Key Fields |
|---|---|
| `Finding` | claim, evidence_excerpt, source_url, publication_date, relevance_score |
| `DocFinding` | claim, evidence_excerpt, document_name, page_number, section |
| `Theme` | title, summary, supporting_claims[], citations[] |
| `Conflict` | description, positions[], citations[] |
| `CoverageGap` | sub_domain, source_count, note |
| `Citation` | source_url, title, publication_date, excerpt |
| `SynthesisOutput` | themes[], conflicts[], gaps[], citations[] |
| `ErrorResult` | isError, errorCategory, isRetryable, attemptedQuery, partialResults, message |
| `SubDomainStatus` | name, source_count, findings[], doc_findings[] |
| `ResearchManifest` | topic, sub_domains[], quality_threshold=2 |

`ResearchManifest` helpers: `coverage_sufficient()` → bool, `gaps()` → list[SubDomainStatus]

---

## 7. Observability (`mars/observability.py`)

Tracing is opt-in — set `LANGFUSE_PUBLIC_KEY` + `LANGFUSE_SECRET_KEY` in `.env`.

| Layer | Mechanism | What it captures |
|---|---|---|
| LLM calls | `AnthropicInstrumentor` (OTel) | Model name, input/output tokens, cost — all agents, automatically |
| Phase hierarchy | `@observe_trace` / `@observe_span` | `mars-run` → `decompose` → `research` → `refinement` → `synthesis` → `report_gen` |

All decorators are fail-safe: runtime errors log a warning and fall back to the original function. Langfuse outages never crash a research run.

---

## 8. Data Flow

```
User input (topic + optional docs)
        ↓
Coordinator (Sonnet)
  decompose_topic() → [{name, search_query, scope}, ...]
        ↓
  asyncio.gather() — max_concurrency=1 default (configurable)
  ┌─────┴──────┬────────────────────┐
  ▼            ▼                    ▼
WebSearch    WebSearch    DocAnalysis (if docs provided)
Agent #1     Agent #2     (Haiku, ReAct)
  │            │
  └─────┬──────┘
        ▼
  ResearchManifest → gap check → re-delegate if needed (ToT)
        ▼
Synthesis Agent (Sonnet) → SynthesisOutput
        ▼
ReportGen Agent (Sonnet) → Markdown report
        ↓
  output/report.md
```

---

## 9. Running the System

```bash
# Setup
cp .env.example .env        # fill in ANTHROPIC_API_KEY, TAVILY_API_KEY
pip install -e .

# Streamlit UI (recommended)
streamlit run app.py

# CLI
python main.py --topic "impact of AI on creative industries"
python main.py --topic "quantum computing" --docs paper.pdf
python main.py --topic "AI ethics" --adaptive          # ReAct search mode
python main.py --topic "quick test" --max-domains 2    # cheap 2-domain run
python main.py --topic "AI" --concurrency 3            # parallel (higher-tier accounts)
```

---

## 10. Environment Variables

```
ANTHROPIC_API_KEY=sk-ant-...          # required
TAVILY_API_KEY=tvly-...               # required

# Optional — Langfuse observability
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_HOST=https://cloud.langfuse.com
```
