# MARS Architecture Overview

## What Was Built and Why

MARS (Multi-Agent Research System) produces comprehensive, cited research reports by orchestrating specialized AI agents. Starting from a design document and zero code, the system was built iteratively in vertical slices — each phase delivering a fully runnable system with progressively more capability.

---

## Build Journey

### Starting Point
A complete design spec (`MARS-design.md`) existed but no code. The first decision was *how* to build it.

**Original plan (rejected):** Horizontal phases — all models, then all tools, then all agents, then wire together. Nothing runs until the final step. Problems discovered only at integration time.

**Adopted approach: Vertical slices.** Each phase delivers a runnable end-to-end system. Real API feedback after every phase instead of discovering problems only at the end.

| Phase | Deliverable | Runnable? |
|---|---|---|
| 1 | Scaffold + Pydantic models | Imports only |
| 2 | Coordinator + Web Search → basic report | Yes — real API calls |
| 3 | + Document Analysis (`--docs` flag) | Yes |
| 4 | + Synthesis subagent | Yes |
| 5 | + Report Generation subagent | Yes |
| 6 | + Iterative gap-filling refinement loop | Yes — full spec |

---

## Phase 1 — Scaffold

**What was built:** `pyproject.toml`, `main.py`, module stubs, `.env.example`.

**Key decision:** Every stub raises `NotImplementedError` with a message like `"Coordinator not yet implemented (Phase 5)"` rather than being empty. This means imports always succeed and each phase's verification is unambiguous — you know exactly what's missing and why.

**Async from the start:** `main.py` uses `asyncio.run()` throughout. All LLM calls and HTTP requests are I/O-bound — committing to async early avoids a painful retrofit later.

**Stack chosen:**
- `anthropic` — official SDK, async-native client
- `tavily-python` — AI-structured search results with dates, not raw HTML
- `httpx` — async HTTP for `fetch_url`
- `pydantic` — typed, validated inter-agent payloads

---

## Phase 2 — Coordinator + Web Search (current)

Three components built in dependency order:

### Tools (`mars/tools/web_search.py`)

`web_search` calls Tavily and returns structured `[{title, url, snippet, date}]`. Tavily was chosen over raw search APIs because it returns AI-structured results — agents don't need to parse HTML.

`fetch_url` uses httpx, truncated to 8000 chars. Enough for evidence extraction; avoids filling the agent's context with full page content.

**Error design:** Both tools return `ErrorResult` (structured dict) on failure rather than raising exceptions. This is deliberate — the coordinator needs to know the failure *category* (`transient` / `validation` / `permission`) to choose a recovery path. A raw exception loses that information.

### Web Search Agent (`mars/agents/web_search.py`)

Implements the **ReAct loop** — see Architecture Patterns below.

**Output contract:** The agent is instructed to end every response with a JSON array of `Finding` objects and nothing else. `_parse_findings()` uses `rfind("[")` / `rfind("]")` to extract it robustly even if the model adds trailing text.

### Coordinator (`mars/coordinator.py`)

**Topic decomposition:** One `claude-sonnet-4-6` call with `tool_choice: {"type": "any"}` forcing use of the `decompose_topic` tool. This guarantees structured JSON output — the plan is machine-readable so Python executes it directly.

**Parallel execution:** `asyncio.gather()` over Python coroutines. Subagent spawning is *not* an LLM tool call — the coordinator LLM returns a decomposition plan, then Python executes agents in parallel natively.

---

## Problem Encountered: API Rate Limits

**What happened:** Spawning all agents simultaneously sent ~8 × 3k input tokens in the first second, hitting the 50k tokens/min ceiling. 7 of 8 agents returned 429.

**Fix 1 — Semaphore:** `asyncio.Semaphore(3)` to cap concurrency. Still rate-limited — 3 concurrent agents each making multiple calls still aggregates too many tokens.

**Fix 2 — Retry with backoff:** Catch `RateLimitError`, sleep 10s → 20s → 40s, retry up to 3×. Still failing — retries from multiple agents overlap.

**Fix 3 (current) — Sequential default:** `max_concurrency=1` as default. Sequential is always safe regardless of API tier. Exposed as `--concurrency N` CLI flag so higher-tier accounts can unlock parallelism.

**Lesson:** The design doc correctly prescribes parallel spawning for performance, but real API rate limits impose a practical constraint. The architecture accommodates both: sequential by default, parallel by configuration.

---

## Agentic Architecture Patterns

### ReAct (Reasoning + Acting) — Subagent Loops

Every subagent runs ReAct: **Thought → Action → Observation → Thought → ...**

```
while stop_reason != "end_turn":
    response = Claude(messages)          # Reason
    tool_results = execute(tool_calls)   # Act
    messages.append(tool_results)        # Observe
parse(final_response)                    # Answer
```

The agent's reasoning determines the next tool call. The tool result becomes the next observation. This repeats until the agent has sufficient evidence to produce its JSON output.

**Why ReAct for subagents:** The next search query depends on what the previous one returned. A static execution plan ("search A, then B, then C") would miss that finding X makes search C redundant. ReAct handles dynamic retrieval naturally.

**Model: `claude-haiku-4-5-20251001`**
Subagent loops are mechanical: search → fetch → extract structured JSON. Tool-calling reliability matters more than reasoning depth here. Haiku handles this at low cost and high speed. Sonnet-class reasoning is reserved for tasks requiring judgment.

---

### Mixture of Experts (MoE) — Hub-and-Spoke Routing

The coordinator is the **gating function**. Each subagent is a **sparse specialist**:

```
Coordinator (gating function)
    ├── WebSearch Expert     → web retrieval only
    ├── DocAnalysis Expert   → document retrieval only
    ├── Synthesis Expert     → cross-source reasoning only
    └── ReportGen Expert     → structured generation only
```

MoE properties enforced:
- **Sparse activation:** DocAnalysis only activates when `--docs` is provided
- **Non-overlapping scope:** coordinator assigns distinct sub-domain boundaries before delegation to prevent duplicate findings
- **Per-query gating:** coordinator reasons about the specific topic before routing, not a fixed rule

**Why MoE over a generalist agent:** A specialist with narrow tool access is more reliable than a generalist with full access. A web search agent that can only call `web_search` and `fetch_url` cannot accidentally attempt document analysis. Principle of least privilege applied to tool scoping.

**Model: `claude-sonnet-4-6` for the coordinator**
Routing requires broad domain knowledge. Correctly decomposing "AI in creative industries" into music, film, IP law, and labor economics — and assigning non-overlapping scope boundaries — requires knowing that these are genuinely distinct expert domains. Haiku produces shallower decompositions.

---

### Plan-and-Execute — Coordinator Outer Loop

The coordinator separates planning from execution completely:

```
Plan:    decompose_topic() → structured JSON            (1 Sonnet call, forced tool_use)
Execute: asyncio.gather(agent_1, agent_2, ...)          (parallel Python coroutines)
```

This is distinct from pure ReAct. The coordinator does not interleave planning and acting:
1. All sub-domain scope boundaries must be set *before* any agent starts to prevent overlap
2. All agents must be known upfront to launch in parallel

`tool_choice: {"type": "any"}` on the decompose call forces the model into the `decompose_topic` tool schema, guaranteeing machine-readable JSON rather than prose that requires further parsing.

---

### Tree of Thought (ToT) — Iterative Gap-Filling (Phase 6)

The refinement loop introduces ToT: evaluate current state → identify gaps → branch into targeted sub-searches → re-evaluate.

```
Root: initial synthesis
├── Gap: "consumer adoption" has 1 source
│   └── Targeted WebSearch → augmented findings → re-synthesize
├── Gap: "EU copyright law" has 0 sources
│   └── Targeted WebSearch → augmented findings → re-synthesize
└── Termination: ResearchManifest.coverage_sufficient() == True
```

This is **breadth-limited ToT**: one branching level per iteration, bounded by `quality_threshold` (≥2 corroborating sources per sub-domain). Full unbounded ToT would be wasteful for a retrieval task — there is no ambiguity about *whether* to fill a gap, only *what* to search for.

**Model: `claude-sonnet-4-6`** — evaluating synthesis completeness requires the same broad domain judgment as decomposition. Recognising that "consumer adoption" is a distinct sub-domain that requires its own sources is not a mechanical operation.

---

## Model Assignment Summary

| Stage | Pattern | Model | Rationale |
|---|---|---|---|
| Topic decomposition | Plan-and-Execute | `claude-sonnet-4-6` | Broad domain knowledge; forced structured JSON via tool_use |
| WebSearch agent loop | ReAct | `claude-haiku-4-5-20251001` | Mechanical tool-calling; speed + cost over reasoning depth |
| DocAnalysis agent loop | ReAct | `claude-haiku-4-5-20251001` | Same rationale; document extraction is structural |
| MoE gating + scope routing | MoE coordinator | `claude-sonnet-4-6` | Scope assignment requires domain judgment |
| Synthesis | MoE output aggregation | `claude-sonnet-4-6` | Conflict detection and gap identification across sources |
| Report generation | Pure generation | `claude-sonnet-4-6` | Prose quality and citation formatting tied directly to capability |
| Gap detection + re-delegation | ToT | `claude-sonnet-4-6` | Same domain judgment requirement as decomposition |

**General rule:** Haiku for I/O-bound tool loops (many calls, low reasoning demand). Sonnet for all judgment calls (decomposition, routing, synthesis, evaluation, generation). Opus not used — tasks do not require frontier-level reasoning and cost differential is not justified.

---

## Agent Communication

### Topology: Hub-and-Spoke (No Peer-to-Peer)

No subagent communicates with another subagent directly. Every message passes through the coordinator. WebSearch never speaks to Synthesis; Synthesis never speaks to ReportGen. This is an explicit design constraint from the spec:

```
ALLOWED:   Coordinator ↔ Subagent
FORBIDDEN: Subagent    ↔ Subagent
```

**Why:** Peer-to-peer communication creates hidden state — the coordinator loses visibility into what was agreed, what failed, and what was shared. In a hub-and-spoke topology the full research trace is auditable because all information flow is visible to the coordinator.

---

### Coordinator → Subagent (Downlink)

The coordinator communicates via **prompt injection** — structured plain text with all necessary context embedded. No formal message envelope or handshake. Every prompt follows a consistent template:

```
Research Topic: {topic}
Your Sub-domain: {name}
Search Query:    {search_query}
Scope:           {scope}

[Prior findings from other agents, if relevant — position-aware ordering]

Instructions:
- ...
- Output format: JSON array of Finding objects
```

Each subagent starts cold with no memory of prior conversations. All context it needs — topic, scope, related findings, output format — must be present in this single prompt. This is the **explicit context injection** pattern from the design spec §6.

**What the downlink does NOT use:**
- Streaming (coordinator waits for full response)
- Acknowledgment / handshake
- Message versioning or schema headers
- Shared memory or blackboard

---

### Subagent → Coordinator (Uplink)

Subagents return a Python object — a `SubagentResult` union type:

```python
SubagentResult = Union[
    list[Finding],       # WebSearch success
    list[DocFinding],    # DocAnalysis success
    SynthesisOutput,     # Synthesis success
    str,                 # ReportGen success (markdown)
    ErrorResult,         # Any agent, any failure
]
```

The uplink has two layers:

**Layer 1 — LLM output convention:** Each agent's system prompt instructs it to end its final response with a JSON array or object and nothing else. The agent runner extracts this with `rfind("[")` / `rfind("]")`.

**Layer 2 — Pydantic validation:** Extracted JSON is parsed into typed models (`Finding`, `SynthesisOutput`, `ErrorResult`). Invalid output surfaces as a `validation` `ErrorResult` — the coordinator decides whether to retry or proceed with partial results.

The uplink protocol is: **structured JSON enforced by system prompt + Pydantic schema validation on receipt.**

---

### Error Protocol

Failures are communicated as structured `ErrorResult` objects, never raw exceptions:

```python
class ErrorResult(BaseModel):
    isError: bool = True
    errorCategory: ErrorCategory        # transient | validation | permission
    isRetryable: bool
    attemptedQuery: str
    partialResults: list[Any]           # findings gathered before failure
    alternativeApproaches: list[str]
    message: str
```

The coordinator inspects `errorCategory` and `isRetryable` to choose a recovery path — retry with modified query, use partial results, flag as coverage gap, or skip. A raw Python exception would discard this decision-relevant structure.

---

### What Is Not Implemented (Protocol Gaps)

| Gap | What it means | Future solution |
|---|---|---|
| No formal schema registry | Uplink/downlink formats are implicit conventions, not versioned contracts | JSON Schema export or Pydantic model registry |
| No streaming | Coordinator blocks until each subagent returns fully | Async streaming with partial result accumulation |
| No acknowledgment / heartbeat | Coordinator cannot distinguish "agent is thinking" from "agent is stuck" | Progress events or timeout-based detection |
| No peer-to-peer | Subagents cannot share intermediate findings directly | Selective P2P via coordinator-permissioned channels (A2A pattern) |
| No transport-layer protocol | Communication is Python function calls, not a portable protocol | Google A2A or MCP extended across agent boundaries |

**Google A2A (Agent2Agent)** and extensions of **MCP (Model Context Protocol)** to span agent boundaries are the emerging standards that would formalise what MARS currently does with Python return values and prompt injection. MARS's architecture is compatible with adopting either — the coordinator's `_guarded()` dispatch function is the natural seam where a transport protocol would plug in.

---

## Data Flow

```
User: --topic "X"
        │
        ▼
Coordinator (Sonnet)
  decompose_topic() → [{name, search_query, scope}, ...]
        │
        ▼ asyncio.gather (max_concurrency=1 default)
  ┌─────┴──────┬────────────┐
  ▼            ▼            ▼
WebSearch    WebSearch    WebSearch     (Haiku, ReAct loop)
Agent #1     Agent #2     Agent #N
  │            │            │
  └─────┬──────┴────────────┘
        ▼
  list[Finding] per sub-domain
        │
        ▼  (Phase 4+)
Synthesis Agent (Sonnet)
  verify_fact() for spot-checks
  → SynthesisOutput {themes, conflicts, gaps, citations}
        │
        ▼  Gap check → re-delegate if needed (Phase 6, ToT)
        │
        ▼  (Phase 5+)
ReportGen Agent (Sonnet)
  → Markdown report with inline citations + bibliography
        │
        ▼
  output/report.md
```

---

## Key Design Decisions & Trade-offs

| Decision | Alternative Considered | Reason Chosen |
|---|---|---|
| Python async (`asyncio`) throughout | Sync SDK | All LLM + HTTP calls are I/O-bound; async enables native parallelism |
| Tavily for web search | SerpAPI / Brave | Returns structured AI results with dates; no HTML parsing needed |
| Forced `tool_use` for decomposition | Parse JSON from prose | Guarantees machine-readable output; eliminates parsing fragility |
| `ErrorResult` returned, not raised | Raise exceptions | Coordinator needs failure category to choose recovery path |
| `max_concurrency=1` default | Always parallel | Free-tier accounts hit 50k tokens/min; configurable for higher tiers |
| Haiku for subagent loops | Sonnet everywhere | Subagent tasks are mechanical; Haiku is 10× cheaper with comparable tool-calling |
| Vertical slice delivery | Build all layers then integrate | Real API feedback after every phase; problems surface early |
