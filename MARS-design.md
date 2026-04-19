Multi-Agent Research System — System Design Document

SCENARIO 3

Multi-Agent Research System

System Design Document

|  |  |
| :-: | :-: |
| Objective | Comprehensive Cited Research Reports |
| Architecture | Hub-and-Spoke Multi-Agent (Claude Agent SDK) |
| Subagents | Web Search · Document Analysis · Synthesis · Report Generation |
| Primary Domains | Agentic Architecture · Tool Design & MCP · Context Management |

# 0. Agentic Architecture Patterns

MARS combines four distinct agentic patterns, each applied where its properties are most useful.

## 0.1 ReAct (Reasoning + Acting) — Subagent Loops

Every subagent runs the ReAct pattern: **Thought → Action → Observation → Thought → ...**

```
while stop_reason != "end_turn":
    response = Claude(messages)          # Reason
    tool_results = execute(tool_calls)   # Act
    messages.append(tool_results)        # Observe
parse(final_response)                    # Answer
```

The agent's internal reasoning determines the next tool call. The tool result is appended as an observation that informs the next reasoning step. This loop repeats until the agent decides it has sufficient evidence.

**Why ReAct for subagents:** The next search query depends on what the previous one returned. A static plan would miss that finding X makes search C irrelevant. ReAct handles dynamic retrieval naturally.

**Model: `claude-haiku-4-5-20251001`** — subagent loops are mechanical (search → fetch → extract JSON). Tool-calling reliability matters more than reasoning depth. Haiku handles this at low latency and cost.

## 0.2 Mixture of Experts (MoE) — Hub-and-Spoke Routing

The coordinator is the **gating function**. Each subagent is a **sparse specialist**:

```
Coordinator (gating)
    ├── WebSearch Expert     → web retrieval only
    ├── DocAnalysis Expert   → document retrieval only
    ├── Synthesis Expert     → cross-source reasoning only
    └── ReportGen Expert     → structured generation only
```

MoE properties enforced:
- **Sparse activation:** DocAnalysis only activates when `--docs` is provided
- **Non-overlapping scope:** coordinator assigns distinct sub-domain boundaries before delegation
- **Per-query gating:** coordinator reasons about the topic before routing, not a fixed rule

**Why MoE:** Specialists with narrow tool access outperform generalists with full access. A web search agent that can only call `web_search` and `fetch_url` will never hallucinate a document analysis result.

**Model: `claude-sonnet-4-6` for the coordinator** — routing requires broad domain knowledge. Decomposing "AI in creative industries" into music, film, IP law, and labor economics correctly requires knowing those are all distinct expert domains worth separate investigation.

## 0.3 Plan-and-Execute — Coordinator Outer Loop

The coordinator separates planning from execution:

```
Plan:    decompose_topic() → structured JSON sub-domains    (1 Claude call, forced tool_use)
Execute: asyncio.gather(agents...)                          (parallel Python coroutines)
```

This is distinct from pure ReAct. The coordinator does not interleave planning and execution — it plans completely first so that:
1. All sub-domain scope boundaries are set before any agent starts (prevents overlap)
2. All agents can launch in parallel via `asyncio.gather()`

`tool_choice: {"type": "any"}` on the decompose call forces structured JSON output — the plan is machine-readable so Python executes it directly without parsing prose.

## 0.4 Tree of Thought (ToT) — Iterative Gap-Filling (Phase 6)

The refinement loop introduces ToT: the coordinator evaluates current state, identifies gaps, branches into targeted sub-searches, and re-evaluates:

```
Root: initial synthesis
├── Gap: "consumer adoption" has 1 source
│   └── Targeted WebSearch → new findings → re-synthesize
├── Gap: "EU copyright law" has 0 sources
│   └── Targeted WebSearch → new findings → re-synthesize
└── Termination: ResearchManifest.coverage_sufficient() == True
```

This is **breadth-limited ToT**: one branching level per iteration, bounded by `quality_threshold` (≥2 sources/sub-domain). Full unbounded ToT would be wasteful for retrieval — there's no ambiguity about *whether* to fill a gap, only *what* to search for.

## 0.5 Model Assignment by Pattern

| Stage | Pattern | Model | Rationale |
| :-: | :-: | :-: | :-: |
| Topic decomposition | Plan-and-Execute | `claude-sonnet-4-6` | Broad domain knowledge; structured JSON via forced tool_use |
| WebSearch agent loop | ReAct | `claude-haiku-4-5-20251001` | Mechanical tool-calling; speed + cost over reasoning depth |
| DocAnalysis agent loop | ReAct | `claude-haiku-4-5-20251001` | Same rationale; document extraction is structural |
| MoE gating + routing | MoE coordinator | `claude-sonnet-4-6` | Scope assignment requires judgment, not just pattern matching |
| Synthesis | MoE output aggregation | `claude-sonnet-4-6` | Conflict detection and gap identification require cross-source reasoning |
| Report generation | Pure generation | `claude-sonnet-4-6` | Prose quality and citation formatting directly tied to model capability |
| Gap detection + re-delegation | ToT | `claude-sonnet-4-6` | Evaluating synthesis completeness requires the same domain judgment as decomposition |

**General principle:** Haiku for I/O-bound tool loops (many calls, low reasoning demand). Sonnet for judgment calls (decomposition, synthesis, evaluation, generation). Opus not used — tasks do not require frontier-level reasoning and cost differential is not justified.

# 1. Executive Summary

This document describes the architecture of a Multi-Agent Research System built with the Claude Agent SDK. A coordinator agent orchestrates four specialized subagents — Web Search, Document Analysis, Synthesis, and Report Generation — to produce comprehensive, fully cited research reports on any topic. The system is designed around a hub-and-spoke topology where all inter-agent communication, error handling, and information flow are routed exclusively through the coordinator.

## 1.1 Design Goals

Produce comprehensive research reports with full source attribution and citation provenance

Enable parallel subagent execution to minimize end-to-end latency on complex topics

Guarantee coverage breadth via iterative refinement loops — the coordinator evaluates synthesis output for gaps and re-delegates targeted queries before finalizing

Propagate structured error context so the coordinator can make intelligent recovery decisions without terminating the workflow

Enforce strict tool scoping: each subagent receives only the tools it needs for its role

## 1.2 Key Constraints

Subagents do not inherit coordinator context automatically — all relevant findings must be passed explicitly in each subagent prompt

The coordinator's allowedTools must include "Task" for subagent spawning to work

Task decomposition must be broad enough to cover all relevant sub-domains of a research topic — overly narrow decomposition is the primary cause of incomplete reports

The synthesis subagent must preserve source attribution (URL, document name, date, excerpt) when combining findings from multiple subagents

# 2. Architecture Overview

The system uses a hub-and-spoke multi-agent architecture. The coordinator is the single point of control: it decomposes the research topic, delegates to subagents via the Task tool, aggregates their outputs, evaluates coverage, and iterates until the research is complete. No subagent communicates with another directly.

## 2.1 Why Hub-and-Spoke (Not Peer-to-Peer)?

The hub-and-spoke pattern is chosen over peer-to-peer or pipeline architectures for three reasons:

Observability: All information flow passes through the coordinator, making the research trace fully auditable.

Error containment: Subagent failures are reported to the coordinator, which decides recovery strategy — partial results are preserved rather than discarding the whole workflow.

Adaptive decomposition: The coordinator can re-delegate targeted follow-up queries after inspecting synthesis output for gaps, without restarting the entire pipeline.

## 2.2 Coordinator Agentic Loop

The coordinator operates in the standard Claude agentic loop, controlled by the stop_reason field:

Receive the research topic and quality criteria from the user.

Analyze the topic: identify sub-domains, scope, and which subagent types are needed.

Emit multiple Task tool calls in a single response to spawn subagents in parallel.

Inspect stop_reason: if "tool_use", collect Task results and route them; if "end_turn", present the final report.

Evaluate synthesis output for coverage gaps. If gaps exist, re-delegate targeted queries and re-invoke synthesis.

Once coverage is sufficient, invoke the Report Generation subagent with the synthesized findings.

|  |
| :-: |
| Critical: Parallel Subagent Spawning |
| Emit multiple Task tool calls within a SINGLE coordinator response to run subagents in parallel.Emitting Task calls across separate turns forces sequential execution, multiplying latency.Example: Spawning 3 search subagents in one response vs. sequentially = \~3x latency reduction. |

## 2.3 Agent Topology Diagram

|  |
| :-: |
| USER REQUEST↓ topic + quality criteria┌────────────────────────┐│ COORDINATOR AGENT (Task tool) │└─┬──────┬───────┬─────┬┘ ↓ ↓ ↓ ↓[Web Search] [Doc Analysis] [Synthesis] [Report Gen] ↑ ↑ ↑ ↑ findings + metadata → coordinator → routing |

# 3. Subagent Definitions & Tool Scoping

Each subagent is defined with a discrete AgentDefinition: a name, system prompt, and a strictly scoped set of tools. Giving a subagent tools outside its role degrades selection reliability and leads to misuse (e.g., the synthesis agent attempting web searches instead of synthesizing).

## 3.1 Web Search Subagent

|  |  |
| :-: | :-: |
| Property | Value |
| Role | Search the web for sources on an assigned subtopic; return structured findings with provenance |
| Tools | web_search, fetch_url |
| Output Format | Array of { claim, evidence_excerpt, source_url, publication_date, relevance_score } |
| Error Handling | Retry transient timeouts locally (up to 2x); on exhaustion, return partial results with structured error context to coordinator |

## 3.2 Document Analysis Subagent

|  |  |
| :-: | :-: |
| Property | Value |
| Role | Analyze documents (PDFs, papers, reports) provided by the coordinator; extract key claims and supporting evidence |
| Tools | read_document, extract_text, fetch_url |
| Output Format | Array of { claim, evidence_excerpt, document_name, page_number, section, publication_date } |
| Scope Limit | Does NOT perform web searches — documents are provided by the coordinator |

## 3.3 Synthesis Subagent

|  |  |
| :-: | :-: |
| Property | Value |
| Role | Combine findings from web search and document analysis into a structured synthesis, identifying agreements, conflicts, and coverage gaps |
| Tools | verify_fact (scoped: simple fact lookups only — dates, names, statistics) |
| Output Format | { themes: [...], conflicts: [...], gaps: [...], citations: [...] } |
| verify_fact Scope | Handles 85% of verifications (simple fact-checks) locally. Complex verifications (15%) delegated back to coordinator → Web Search subagent |

|  |
| :-: |
| Design Decision: Scoped verify_fact Tool for Synthesis Agent |
| Problem: The synthesis agent frequently needs to verify specific claims while combining findings.Naïve solution: Return control to the coordinator for every verification → adds 2–3 round trips per task, 40% latency increase.Solution: Give the synthesis agent a scoped verify_fact tool for simple lookups (85% of cases).Complex verifications (15%) still delegate to the Web Search subagent through the coordinator.This applies the principle of least privilege: the synthesis agent gets exactly what it needs, nothing more. |

## 3.4 Report Generation Subagent

|  |  |
| :-: | :-: |
| Property | Value |
| Role | Format the synthesized findings into a professional report with executive summary, body sections, and inline citations |
| Tools | None (pure generation from provided synthesis output) |
| Input | Complete synthesis output passed explicitly in prompt by coordinator |
| Output Format | Markdown report with inline [Source N] citations, executive summary, section headers, and bibliography |

# 4. Coordinator Agent Design

The coordinator is the most critical component of the system. Its task decomposition quality directly determines the breadth and quality of the final report. A coordinator that decomposes "AI in creative industries" into only visual arts subtasks will produce a report that misses music, writing, and film — even if every subagent executes perfectly within its assigned scope.

## 4.1 Task Decomposition Strategy

The coordinator uses dynamic adaptive decomposition rather than a fixed pipeline. For each research topic, the coordinator reasons about:

Which sub-domains exist within the topic (must be exhaustive, not illustrative)

Which source types are most relevant (web sources, academic papers, reports, news)

How to partition scope across subagents to minimize duplication

What quality criteria will be used to evaluate synthesis completeness

|  |
| :-: |
| Anti-Pattern: Overly Narrow Task Decomposition |
| Root cause of most incomplete reports is coordinator decomposition that is too narrow.Example: Topic = "impact of AI on creative industries"Bad decomposition: "AI in digital art", "AI in graphic design", "AI in photography" → Result: Report covers only visual arts; music, writing, film production are missed entirely.Good decomposition: "AI in visual arts", "AI in music composition", "AI in film/video production", "AI in writing and publishing", "economic impact on creative workers"Rule: Decomposition must enumerate ALL relevant sub-domains, not just the most obvious ones. |

## 4.2 Coordinator System Prompt Design

The coordinator's system prompt specifies research goals and quality criteria — not step-by-step procedural instructions. This enables subagent adaptability while keeping the coordinator accountable for coverage:

|  |
| :-: |
| You are a research coordinator. For each research topic:1. DECOMPOSE: Identify ALL sub-domains of the topic. Think broadly. - List at least 5-8 distinct sub-domains before assigning subtasks. - Ask: What aspects would a domain expert expect to see covered?2. DELEGATE: Spawn web search and document analysis subagents in parallel. - Assign distinct, non-overlapping subtopics to each search subagent. - Provide explicit scope boundaries to prevent duplication.3. EVALUATE: After synthesis, check coverage against your sub-domain list. - If any sub-domain has fewer than 2 corroborating sources: re-delegate. - If any sub-domain is missing entirely: spawn targeted follow-up search.4. FINALIZE: Only invoke report generation when coverage is sufficient. |

## 4.3 Iterative Refinement Loop

The coordinator does not assume that a single pass through the pipeline is sufficient. It evaluates the synthesis output against its sub-domain checklist and re-delegates before finalizing:

|  |  |
| :-: | :-: |
| Condition | Coordinator Action |
| Sub-domain has 0 sources | Spawn targeted web search subagent with explicit scope |
| Sub-domain has only 1 source | Spawn corroboration search; flag as low-confidence in synthesis |
| Conflicting claims detected | Spawn document analysis subagent focused on conflict resolution |
| Coverage sufficient (≥2 sources per sub-domain) | Proceed to Report Generation |

# 5. MCP Tool Design

Each MCP tool is designed with a detailed description that unambiguously specifies its purpose, input format, and boundary conditions. Ambiguous or overlapping descriptions cause misrouting — particularly between web_search and analyze_document, which can look similar to a model without careful differentiation.

## 5.1 Tool Registry

|  |  |  |  |
| :-: | :-: | :-: | :-: |
| Tool | Agent | Input | Output |
| web_search | Web Search | query: string, max_results: int | Array of { title, url, snippet, date } |
| fetch_url | Web Search, Doc Analysis | url: string | { content: string, title, date, url } |
| read_document | Doc Analysis | document_id: string, page_range?: [int, int] | { text, metadata, page_count } |
| verify_fact | Synthesis (scoped) | claim: string, context?: string | { verified: bool, source_url, confidence } |
| Task | Coordinator only | agent_type, prompt, context | Structured subagent output |

## 5.2 Tool Description Design Principles

Tool descriptions must eliminate functional overlap. The two tools most prone to misrouting are web_search and read_document:

|  |
| :-: |
| // web_search description (precise boundary):"Use this tool to find new sources on the web via a search query. Input: a natural language search query (e.g., 'AI music composition 2024'). Returns URLs and snippets. Use fetch_url to retrieve full content. DO NOT use for documents already provided in your context."// read_document description (distinct boundary):"Use this tool to read a specific document by its document_id. The document_id is provided by the coordinator in your prompt. DO NOT use this tool for web searches or fetching arbitrary URLs. Use only for documents explicitly assigned to you by the coordinator." |

## 5.3 Structured Error Responses

All MCP tools return structured error metadata using the isError flag pattern. Generic error messages ("Search failed") prevent the coordinator from making intelligent recovery decisions:

|  |
| :-: |
| // Structured error response from web_search on timeout:{ isError: true, error: { errorCategory: "transient", // transient \| validation \| permission isRetryable: true, attemptedQuery: "AI in music composition 2024", partialResults: [...], // any results before timeout alternativeApproaches: [ "Try narrower query: 'AI music 2024'", "Try alternative: 'machine learning audio generation'" ], message: "Search timed out after 30s on query: AI in music composition 2024" }} |

|  |
| :-: |
| Error Propagation Design Rule |
| NEVER: Catch timeout within subagent and return empty result set marked as successful. → This suppresses the error, prevents recovery, and risks an incomplete report silently.NEVER: Propagate raw exception to a top-level handler that terminates the workflow. → This discards partial results and terminates unnecessarily.CORRECT: Return structured error context to the coordinator including failure type, attempted query, partial results, and alternative approaches. → The coordinator can retry with modified query, try alternative approach, or proceed with partial results. |

# 6. Context Passing Between Agents

Subagents have isolated context — they do not inherit the coordinator's conversation history automatically. Every piece of information a subagent needs to do its job must be provided explicitly in its prompt. This is the most common source of integration failures in multi-agent systems.

## 6.1 Context Injection Pattern

The coordinator constructs each subagent's prompt to include: the research topic and sub-domain scope, any relevant findings from prior subagents, explicit output format requirements, and quality criteria. Here is the pattern used when invoking the Synthesis subagent:

|  |
| :-: |
| // Coordinator constructs synthesis subagent prompt:const synthesisPrompt = \`Research Topic: ${topic}Sub-domains to cover: ${subdomains.join(', ')}Web Search Findings:${JSON.stringify(webSearchResults, null, 2)}Document Analysis Findings:${JSON.stringify(docAnalysisResults, null, 2)}Instructions:- Synthesize all findings into themes, noting agreements and conflicts.- Preserve source attribution: include source_url and publication_date for every claim.- Identify coverage gaps: sub-domains with fewer than 2 sources.- Output format: { themes, conflicts, gaps, citations }\`; |

## 6.2 Structured Findings Format

All subagents return findings in a structured format that separates content from metadata. This enables the synthesis subagent to preserve attribution without requiring it to parse free text:

|  |  |  |
| :-: | :-: | :-: |
| Field | Purpose | Example |
| claim | The specific finding or assertion | "AI-generated music tools increased 340% in 2023" |
| evidence_excerpt | Verbatim quote supporting the claim | "According to IFPI, AI music tools grew..." |
| source_url | Canonical URL for citation | "https://ifpi.org/reports/2024" |
| publication_date | For recency assessment during synthesis | "2024-03-15" |
| relevance_score | 0.0–1.0, used by coordinator for gap detection | 0.87 |

## 6.3 Context Trimming for Downstream Agents

When passing findings from web search and document analysis to the synthesis subagent, the coordinator trims verbose content to avoid context window exhaustion. The coordinator keeps structured key facts and citations but omits raw search snippets and full document text that the synthesis agent does not need:

|  |
| :-: |
| Lost-in-the-Middle Mitigation |
| When aggregating findings from 4+ subagents into a single synthesis prompt, position matters.Models reliably process information at the beginning and end of long inputs.Strategy 1: Place the most important or underrepresented sub-domain findings FIRST.Strategy 2: Use explicit section headers for each sub-domain to aid attention allocation.Strategy 3: Include a 'Coverage Checklist' at the top listing all expected sub-domains.This tells the synthesis agent what it SHOULD find, reducing the chance of missing middle sections. |

# 7. End-to-End Workflow Walkthrough

This section walks through a complete research request: "What is the impact of AI on creative industries?" — the canonical example from the exam guide.

## 7.1 Phase 1 — Topic Decomposition (Coordinator)

The coordinator analyzes the topic and identifies all sub-domains before delegating:

AI in visual arts (digital art, graphic design, photography)

AI in music composition and audio production

AI in film and video production

AI in writing and publishing

Economic impact on creative workers and labor markets

Copyright, IP, and legal framework developments

Consumer adoption and audience reception

The coordinator then assigns distinct, non-overlapping subtopics to three Web Search subagents and passes two documents (academic papers on AI labor impact) to the Document Analysis subagent. All four subagents are spawned in a single coordinator response for parallel execution.

## 7.2 Phase 2 — Parallel Research (Web Search + Doc Analysis)

|  |  |
| :-: | :-: |
| Subagent | Assigned Scope |
| Web Search #1 | Visual arts (digital art, graphic design, photography) + music composition |
| Web Search #2 | Film/video production + writing/publishing + consumer adoption |
| Web Search #3 | Economic/labor impact + copyright/IP + legal developments |
| Doc Analysis #1 | Provided academic papers: 'AI and Creative Labor Markets' (2024), 'Generative AI Copyright Survey' (2024) |

## 7.3 Phase 3 — Synthesis & Gap Detection

The coordinator collects all subagent outputs and constructs a synthesis prompt with all findings explicitly injected. The Synthesis subagent combines findings, identifies conflicts (e.g., differing estimates of job displacement rates), and reports two coverage gaps: 'audience reception' has only 1 source, 'copyright law in EU specifically' has 0 sources.

The coordinator's iterative refinement loop detects these gaps and spawns two targeted follow-up web searches before re-invoking synthesis.

## 7.4 Phase 4 — Report Generation

Once synthesis passes the coverage threshold (2+ corroborating sources per sub-domain), the coordinator passes the complete synthesis output to the Report Generation subagent. The report is generated with: an executive summary, one section per sub-domain, inline [Source N] citations, a conflicts and uncertainties section, and a full bibliography.

# 8. Error Recovery Strategies

The coordinator applies different recovery strategies depending on the error category returned by the subagent:

|  |  |  |  |
| :-: | :-: | :-: | :-: |
| Error Category | isRetryable | Coordinator Action | Example |
| transient | true | Retry with modified query; use partial results if retry also fails | Search timeout on complex query |
| validation | false | Log error; skip this subtopic; note gap in synthesis | Invalid document_id provided |
| permission | false | Flag source as inaccessible; assign alternative search to another subagent | Paywalled journal article |
| empty result | n/a (success) | Treat as coverage gap; spawn follow-up search with alternative query | No results for niche sub-domain |

|  |
| :-: |
| Critical Distinction: Empty Result vs. Error |
| A successful search with no results is NOT an error — it is valid output with a coverage implication.Do NOT use isError: true for empty result sets; that conflates two different conditions.Empty results → coordinator notes coverage gap and spawns follow-up with alternative query.transient error → coordinator retries with the same or modified query.Conflating them prevents the coordinator from choosing the appropriate recovery path. |

# 9. Context Management Strategy

Research workflows can involve dozens of tool calls and thousands of tokens of subagent output. Without deliberate context management, the coordinator's context fills with verbose subagent results, and the "lost in the middle" effect causes critical findings to be ignored.

## 9.1 Coordinator Context Architecture

|  |  |
| :-: | :-: |
| Context Layer | Contents & Purpose |
| Research Manifest | Topic, sub-domain checklist, quality criteria, coverage status per sub-domain. Persisted across all iterations. |
| Structured Findings Cache | Trimmed, structured output from all subagents. Only { claim, source_url, date } retained — verbose raw content discarded. |
| Active Task Context | Current iteration's subagent prompts and partial results. Reset after each iteration. |
| Synthesis Summary | Coordinator-maintained running synthesis. Updated after each iteration. Replaces raw subagent outputs in context. |

## 9.2 Upstream Output Optimization

When downstream agents (synthesis, report generation) have limited context budgets, upstream agents (web search, document analysis) must be instructed to return structured key facts rather than verbose content and reasoning chains:

Web Search subagent: return { claim, source_url, date, relevance_score } — NOT full article text

Document Analysis subagent: return { claim, page_number, evidence_excerpt } — NOT full document paraphrase

Coordinator trims findings to relevant fields before passing to synthesis subagent

Progressive summarization must preserve numerical values, dates, and specific statistics — these are lost when condensed into vague summaries

## 9.3 Position-Aware Aggregation

When the coordinator aggregates findings from multiple subagents into a single synthesis prompt, it applies position-aware ordering:

The research manifest and coverage checklist are placed FIRST (model reliably attends to beginning)

Underrepresented sub-domains are placed FIRST within the findings section

The synthesis instructions and quality criteria are placed LAST (model reliably attends to end)

Verbose middle sections use explicit section headers: ## Visual Arts Findings ## Music Findings, etc.

# 10. Exam-Relevant Design Decisions Summary

The following table maps each key design decision in this system to the exam domain it demonstrates:

|  |  |  |
| :-: | :-: | :-: |
| Design Decision | Domain | Why It Matters |
| Hub-and-spoke: all comms routed through coordinator | Agentic Architecture | Observability, consistent error handling, controlled flow |
| Emit multiple Task calls in single coordinator response | Agentic Architecture | Parallel execution; sequential = 3–4x latency penalty |
| Coordinator evaluates coverage & re-delegates gaps | Agentic Architecture | Iterative refinement prevents incomplete reports |
| allowedTools includes "Task" for coordinator | Tool Design & MCP | Required for Task-based subagent spawning |
| Scoped tool access per subagent (principle of least privilege) | Tool Design & MCP | Prevents misuse; improves tool selection reliability |
| Structured error with errorCategory, isRetryable, partialResults | Tool Design & MCP | Enables intelligent coordinator recovery decisions |
| verify_fact scoped to synthesis agent for 85% of verifications | Tool Design & MCP | Eliminates 40% latency overhead from unnecessary round-trips |
| All subagent context passed explicitly in prompt | Context Management | Subagents have no automatic context inheritance |
| Structured findings format with metadata separation | Context Management | Preserves source attribution through synthesis pipeline |
| Upstream agents return structured key facts, not verbose text | Context Management | Prevents context budget exhaustion downstream |
| Position-aware aggregation with coverage checklist first | Context Management | Mitigates lost-in-the-middle effect on synthesis |

# 11. Implementation Specification

## 11.1 Stack

| Component | Choice | Rationale |
| :-: | :-: | :-: |
| Language | Python 3.11+ | Best ecosystem for Anthropic SDK and AI tooling |
| LLM SDK | anthropic ≥ 0.40.0 | Official Python SDK; async messages.create() |
| Web Search | tavily-python ≥ 0.5.0 | AI-native structured results; generous free tier |
| HTTP client | httpx ≥ 0.27.0 | Async-first; used by fetch_url tool |
| Data validation | pydantic ≥ 2.7.0 | Typed models for all inter-agent payloads |
| Config | python-dotenv | ANTHROPIC_API_KEY + TAVILY_API_KEY from .env |
| UI | streamlit ≥ 1.35.0 | Interactive web UI with live progress streaming |
| Observability | langfuse ≥ 2.0.0 | Trace/span hierarchy, token usage, cost tracking |
| OTel instrumentation | opentelemetry-instrumentation-anthropic ≥ 0.60.0 | Auto-captures model name, token counts, cost on every Anthropic call |

## 11.2 Directory Structure

```
MARS/
├── mars/
│   ├── __init__.py
│   ├── coordinator.py          # Coordinator agentic loop (§4)
│   ├── models.py               # Pydantic models for all inter-agent payloads
│   ├── observability.py        # Langfuse tracing (graceful no-op if keys absent)
│   ├── agents/
│   │   ├── __init__.py
│   │   ├── web_search.py       # Web Search subagent runner (§3.1)
│   │   ├── doc_analysis.py     # Document Analysis subagent runner (§3.2)
│   │   ├── synthesis.py        # Synthesis subagent runner (§3.3)
│   │   └── report_gen.py       # Report Generation subagent runner (§3.4)
│   └── tools/
│       ├── __init__.py
│       ├── web_search.py       # web_search + fetch_url (Tavily + httpx)
│       ├── document.py         # read_document (in-memory doc store)
│       └── verify_fact.py      # verify_fact (Tavily + Claude mini)
├── app.py                      # Streamlit UI (streamlit run app.py)
├── main.py                     # CLI: --topic, --output, --docs, --adaptive
├── pyproject.toml
├── .env.example
└── output/                     # Generated reports (gitignored)
```

## 11.3 Data Models (`mars/models.py`)

| Model | Fields |
| :-: | :-: |
| `Finding` | claim, evidence_excerpt, source_url, publication_date, relevance_score (0.0–1.0) |
| `DocFinding` | claim, evidence_excerpt, document_name, page_number, section, publication_date |
| `Theme` | title, summary, supporting_claims[], citations[] |
| `Conflict` | description, positions[], citations[] |
| `CoverageGap` | sub_domain, source_count, note |
| `Citation` | source_url, title, publication_date, excerpt |
| `SynthesisOutput` | themes[], conflicts[], gaps[], citations[] |
| `ErrorCategory` | Enum: transient \| validation \| permission |
| `ErrorResult` | isError, errorCategory, isRetryable, attemptedQuery, partialResults, alternativeApproaches, message |
| `SubDomainStatus` | name, source_count, findings[], doc_findings[] |
| `ResearchManifest` | topic, sub_domains[], quality_threshold (default 2) — coordinator internal |
| `SubagentResult` | Union[list[Finding] \| list[DocFinding] \| SynthesisOutput \| str \| ErrorResult] |

`ResearchManifest` exposes two helpers used by the coordinator:
- `coverage_sufficient()` → True when all sub-domains have ≥ quality_threshold sources
- `gaps()` → list of SubDomainStatus below threshold

## 11.4 Subagent Runner Pattern

Each runner in `mars/agents/` follows the same pattern:

```python
async def run(prompt: str, doc_store: dict = {}) -> SubagentResult:
    # 1. Build system prompt for this agent's role
    # 2. client.messages.create() with scoped tools
    # 3. Agentic loop: handle tool_use stop_reason
    # 4. Return structured SubagentResult
```

Tool scoping enforced at runner level — each runner only receives its allowed tool schemas.

## 11.5 Coordinator Pattern (`mars/coordinator.py`)

```python
class Coordinator:
    async def run(self, topic: str, doc_paths: list[str]) -> str:
        # 1. Decompose topic into ≥5 sub-domains via Claude
        # 2. Spawn WebSearch × N (+ DocAnalysis × M if --docs) via asyncio.gather
        # 3. Pass findings to Synthesis subagent (position-aware aggregation)
        # 4. Gap detection → re-delegate targeted searches if needed
        # 5. Delegate to ReportGen when coverage ≥2 sources/sub-domain
        # Returns: markdown report string
```

Subagent spawning is implemented via `asyncio.gather()` over Python coroutines — not an LLM tool call. The coordinator LLM decomposes the topic and returns structured JSON (sub-domains + scope assignments); the Python coordinator loop executes those as parallel async calls.

**Rate limit design:** Spawning all agents simultaneously saturates the 50k input token/min API rate limit. The coordinator wraps each agent call in `asyncio.Semaphore(max_concurrency)`. Default is `max_concurrency=1` (sequential) which is safe for free-tier accounts. Pass `--concurrency 3` on the CLI for higher-tier accounts. Agents additionally catch `RateLimitError` and retry up to 3× with exponential backoff (10s → 20s → 40s) before returning a structured `ErrorResult`.

## 11.6 Search Modes

Two modes are available, selectable per run:

| Mode | Flag | How it works | Cost |
| :-: | :-: | :-: | :-: |
| Direct (default) | *(none)* | Calls Tavily directly per sub-domain — no Claude Haiku involved | ~$0.001/run |
| Adaptive (ReAct) | `--adaptive` / UI toggle | Claude Haiku drives the search strategy via ReAct loop | ~$0.05/run |

Direct mode is the default because Tavily already returns structured, high-quality results for most topics. Adaptive mode is worth the cost when the topic is nuanced and benefits from iterative query refinement (e.g., niche technical topics where the first search reveals better search terms).

## 11.7 Observability

Tracing is opt-in. Set `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` in `.env` to enable:

```
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_HOST=https://cloud.langfuse.com
```

**Two-layer instrumentation:**

1. `AnthropicInstrumentor` (OpenTelemetry) — automatically captures model name, input/output tokens, and cost for every `client.messages.create()` call. No code changes required in agent files.
2. `@observe` decorators on coordinator phase methods — provides the span hierarchy visible in Langfuse: `mars-run` → `decompose` → `research` → `refinement` → `synthesis` → `report_gen`.

Tracing is **fail-safe**: if any Langfuse API call errors at runtime, the decorator logs a warning and falls back to calling the underlying function directly. Langfuse errors never crash a research run.

## 11.8 CLI and UI Usage

```bash
cp .env.example .env          # add ANTHROPIC_API_KEY, TAVILY_API_KEY (+ optional Langfuse keys)
pip install -e .

# CLI
python main.py --topic "impact of AI on creative industries"
python main.py --topic "quantum computing" --docs paper1.pdf paper2.pdf
python main.py --topic "AI ethics" --concurrency 3   # higher-tier accounts only
python main.py --topic "AI ethics" --adaptive         # ReAct search mode
python main.py --topic "AI ethics" --max-domains 3    # cap sub-domains for cheap test runs

# Streamlit UI
streamlit run app.py
# → opens browser at http://localhost:8501
# → sidebar: adaptive toggle, max sub-domains slider, document uploader
# → live progress log streams during the run
# → rendered report + download button on completion

# Output: output/report.md
```

## 11.9 Build Phases (Vertical Slices)

Each phase delivers a fully runnable end-to-end system. Later phases add capability, not prerequisites.

| Phase | Deliverable | Runnable after? | Status |
| :-: | :-: | :-: | :-: |
| 1 | Scaffold + all Pydantic models | Imports only (NotImplementedError on run) | Done |
| 2 | Coordinator + Web Search → basic markdown report | **Yes** — real Tavily + Claude calls | Done |
| 3 | + Document Analysis subagent (`--docs` flag) | **Yes** — web + doc sources in report | Done |
| 4 | + Synthesis subagent (themes, conflicts, gap detection) | **Yes** — structured synthesis | Done |
| 5 | + Report Generation subagent (citations, bibliography) | **Yes** — publication-quality output | Done |
| 6 | + Iterative refinement loop (re-delegate gaps until coverage ≥2/sub-domain) | **Yes** — full spec per §4.3 | Done |
| 7 | + ReAct toggle, Streamlit UI, Langfuse observability | **Yes** — UI at `streamlit run app.py` | Done |

Design rationale: vertical slices ensure every phase produces observable value and allows early feedback on real API behaviour before the full system is assembled.