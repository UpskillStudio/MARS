import asyncio
import json
import os
from pathlib import Path

import anthropic
from tavily import TavilyClient

from mars.models import (
    DocFinding, ErrorResult, Finding, ResearchManifest,
    SubDomainStatus, SynthesisOutput,
)
from mars.agents import web_search as web_search_agent
from mars.agents import doc_analysis as doc_analysis_agent
from mars.agents import synthesis as synthesis_agent
from mars.agents import report_gen as report_gen_agent
from mars.tools import document as doc_tool
from mars.observability import observe_span, observe_trace, flush

_tavily_client: TavilyClient | None = None

def _tavily() -> TavilyClient:
    global _tavily_client
    if _tavily_client is None:
        _tavily_client = TavilyClient(api_key=os.environ["TAVILY_API_KEY"])
    return _tavily_client

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MAX_REFINEMENT_ITERATIONS = 2
_FALLBACK_REPORT_HEADER = "# Research Report (synthesis unavailable)\n\n"

# ---------------------------------------------------------------------------
# Decomposition tool schema
# ---------------------------------------------------------------------------

_DECOMPOSE_TOOL = {
    "name": "decompose_topic",
    "description": "Decompose a research topic into distinct sub-domains with search queries.",
    "input_schema": {
        "type": "object",
        "properties": {
            "sub_domains": {
                "type": "array",
                "minItems": 5,
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "search_query": {"type": "string"},
                        "scope": {"type": "string"},
                    },
                    "required": ["name", "search_query", "scope"],
                },
            }
        },
        "required": ["sub_domains"],
    },
}

_DECOMPOSE_SYSTEM = """\
You are a research coordinator. When given a topic, decompose it into ALL relevant sub-domains.
Think broadly — enumerate at least 5-8 distinct sub-domains a domain expert would expect covered.
Assign non-overlapping scope boundaries to prevent duplicate findings across sub-domains.
"""


# ---------------------------------------------------------------------------
# Coordinator
# ---------------------------------------------------------------------------

class Coordinator:
    def __init__(
        self,
        max_concurrency: int = 1,
        adaptive_search: bool = False,
        max_domains: int = 0,
    ) -> None:
        """
        Args:
            max_concurrency: Max parallel subagent calls. Default 1 (sequential).
            adaptive_search: If True, use ReAct loop (Claude decides search strategy).
                             If False (default), call Tavily directly — no Haiku calls.
            max_domains: Cap sub-domains to this number (0 = no cap). Use 2-3 for test runs.
        """
        self._client = anthropic.AsyncAnthropic()
        self._max_concurrency = max_concurrency
        self._adaptive_search = adaptive_search
        self._max_domains = max_domains

    @observe_trace("mars-run")
    async def run(self, topic: str, doc_paths: list[str] = []) -> str:
        print(f"\n[Coordinator] Topic: {topic}")

        mode = "adaptive (ReAct)" if self._adaptive_search else "direct (Tavily)"
        print(f"[Coordinator] Search mode: {mode}")

        # Phase 1 — Decompose
        sub_domains = await self._decompose(topic)
        if self._max_domains:
            sub_domains = sub_domains[: self._max_domains]

        print(f"[Coordinator] Sub-domains ({len(sub_domains)}):")
        for sd in sub_domains:
            print(f"  - {sd['name']}")

        manifest = ResearchManifest(
            topic=topic,
            sub_domains=[SubDomainStatus(name=sd["name"]) for sd in sub_domains],
        )

        # Phase 2 — Load documents
        doc_store = self._load_documents(doc_paths)
        if doc_store:
            doc_tool.register(doc_store)

        # Phase 3 — Initial research
        sd_findings, sd_doc_findings = await self._run_initial_research(
            topic, sub_domains, doc_store
        )
        manifest = self._update_manifest(manifest, sd_findings, sd_doc_findings)
        total = sum(len(v) for v in sd_findings.values())
        doc_total = sum(len(v) for v in sd_doc_findings.values())
        print(f"\n[Coordinator] Findings: {total} web, {doc_total} doc")

        # Phase 6 — Refinement
        sd_findings = await self._refine(topic, manifest, sd_findings)
        manifest = self._update_manifest(manifest, sd_findings, sd_doc_findings)

        # Phase 4 — Synthesis
        print("\n[Coordinator] Running synthesis...")
        synthesis = await self._synthesize(topic, sub_domains, sd_findings, sd_doc_findings)
        if isinstance(synthesis, ErrorResult):
            print(f"  [Synthesis] ERROR: {synthesis.message} — falling back")
            flush()
            return self._fallback_report(topic, sub_domains, sd_findings)

        print(f"  [Synthesis] {len(synthesis.themes)} themes, {len(synthesis.conflicts)} conflicts, {len(synthesis.gaps)} gaps")

        # Phase 5 — Report generation
        print("\n[Coordinator] Generating report...")
        report = await self._generate_report(topic, synthesis)
        if isinstance(report, ErrorResult):
            print(f"  [ReportGen] ERROR: {report.message} — falling back")
            flush()
            return self._fallback_report(topic, sub_domains, sd_findings)

        flush()
        return report

    # -----------------------------------------------------------------------
    # Search modes
    # -----------------------------------------------------------------------

    async def _direct_web_search(
        self, topic: str, sub_domains: list[dict]
    ) -> dict[str, list[Finding]]:
        """Call Tavily directly — no Claude Haiku, no ReAct loop."""
        print(f"\n[Coordinator] Direct Tavily search across {len(sub_domains)} sub-domains...")
        sd_findings: dict[str, list[Finding]] = {}
        for sd in sub_domains:
            name = sd["name"]
            try:
                response = _tavily().search(query=sd["search_query"], max_results=5)
                findings = [
                    Finding(
                        claim=r.get("title", ""),
                        evidence_excerpt=r.get("content", ""),
                        source_url=r.get("url", ""),
                        publication_date=r.get("published_date", ""),
                        relevance_score=r.get("score", 0.5),
                    )
                    for r in response.get("results", [])
                    if r.get("url")
                ]
                sd_findings[name] = findings
                print(f"  [{name}] {len(findings)} findings")
            except Exception as e:
                print(f"  [{name}] Tavily error: {e}")
                sd_findings[name] = []
        return sd_findings

    async def _adaptive_web_search(
        self, topic: str, sub_domains: list[dict]
    ) -> dict[str, list[Finding]]:
        """ReAct loop — Claude Haiku decides search strategy adaptively."""
        sem = asyncio.Semaphore(self._max_concurrency)

        async def _web(sd: dict):
            async with sem:
                return sd["name"], await web_search_agent.run(
                    self._build_search_prompt(topic, sd)
                )

        print(f"\n[Coordinator] Adaptive search: {len(sub_domains)} agents (max {self._max_concurrency} concurrent)...")
        results = await asyncio.gather(*[_web(sd) for sd in sub_domains], return_exceptions=True)

        sd_findings: dict[str, list[Finding]] = {}
        for item in results:
            if isinstance(item, Exception):
                print(f"  [WebSearch] EXCEPTION: {item}")
                continue
            name, result = item
            if isinstance(result, list):
                findings = [f for f in result if isinstance(f, Finding)]
                sd_findings[name] = findings
                print(f"  [{name}] {len(findings)} findings")
            elif isinstance(result, ErrorResult):
                print(f"  [{name}] ERROR: {result.message}")
                sd_findings[name] = []
        return sd_findings

    # -----------------------------------------------------------------------
    # Phase: Decompose
    # -----------------------------------------------------------------------

    @observe_span("decompose")
    async def _decompose(self, topic: str) -> list[dict]:
        response = await self._client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            system=_DECOMPOSE_SYSTEM,
            tools=[_DECOMPOSE_TOOL],
            tool_choice={"type": "any"},
            messages=[{"role": "user", "content": f"Research topic: {topic}"}],
        )
        for block in response.content:
            if block.type == "tool_use" and block.name == "decompose_topic":
                return block.input["sub_domains"]
        return [{"name": topic, "search_query": topic, "scope": "general overview"}]

    # -----------------------------------------------------------------------
    # Phase: Initial research (web + doc in parallel)
    # -----------------------------------------------------------------------

    @observe_span("research")
    async def _run_initial_research(
        self, topic: str, sub_domains: list[dict], doc_store: dict
    ) -> tuple[dict[str, list[Finding]], dict[str, list[DocFinding]]]:
        if self._adaptive_search:
            sd_findings = await self._adaptive_web_search(topic, sub_domains)
        else:
            sd_findings = await self._direct_web_search(topic, sub_domains)

        sd_doc_findings: dict[str, list[DocFinding]] = {}
        if doc_store:
            print(f"\n[Coordinator] Spawning DocAnalysis agent for {len(doc_store)} document(s)...")
            doc_result = await doc_analysis_agent.run(
                self._build_doc_prompt(topic, list(doc_store.keys()))
            )
            if isinstance(doc_result, list):
                doc_findings = [f for f in doc_result if isinstance(f, DocFinding)]
                sd_doc_findings["_documents"] = doc_findings
                print(f"  [DocAnalysis] {len(doc_findings)} findings")
            elif isinstance(doc_result, ErrorResult):
                print(f"  [DocAnalysis] ERROR: {doc_result.message}")

        return sd_findings, sd_doc_findings

    # -----------------------------------------------------------------------
    # Phase 6: Iterative refinement
    # -----------------------------------------------------------------------

    @observe_span("refinement")
    async def _refine(
        self, topic: str, manifest: ResearchManifest, sd_findings: dict[str, list[Finding]]
    ) -> dict[str, list[Finding]]:
        for iteration in range(_MAX_REFINEMENT_ITERATIONS):
            if manifest.coverage_sufficient():
                print(f"\n[Coordinator] Coverage sufficient after iteration {iteration}")
                break

            gaps = manifest.gaps()
            print(f"\n[Coordinator] Refinement {iteration + 1}: {len(gaps)} gap(s) — {[g.name for g in gaps]}")

            sem = asyncio.Semaphore(self._max_concurrency)

            async def _gap_search(gap: SubDomainStatus):
                async with sem:
                    sd = {
                        "name": gap.name,
                        "search_query": f"{gap.name} {topic} evidence research",
                        "scope": f"Find additional corroborating sources for: {gap.name}",
                    }
                    return gap.name, await web_search_agent.run(
                        self._build_search_prompt(topic, sd)
                    )

            gap_results = await asyncio.gather(*[_gap_search(g) for g in gaps], return_exceptions=True)

            for item in gap_results:
                if isinstance(item, Exception):
                    continue
                name, result = item
                if isinstance(result, list):
                    new_findings = [f for f in result if isinstance(f, Finding)]
                    sd_findings.setdefault(name, []).extend(new_findings)
                    print(f"  [{name}] +{len(new_findings)} findings")

            manifest = self._update_manifest(manifest, sd_findings, {})

        return sd_findings

    # -----------------------------------------------------------------------
    # Phase 4: Synthesis
    # -----------------------------------------------------------------------

    @observe_span("synthesis")
    async def _synthesize(
        self,
        topic: str,
        sub_domains: list[dict],
        sd_findings: dict[str, list[Finding]],
        sd_doc_findings: dict[str, list[DocFinding]],
    ) -> SynthesisOutput | ErrorResult:
        all_findings = [f for findings in sd_findings.values() for f in findings]
        all_doc_findings = [f for findings in sd_doc_findings.values() for f in findings]

        # Trim to key fields to manage context window
        trimmed_web = [
            {"claim": f.claim, "source_url": f.source_url, "date": f.publication_date, "sub_domain": name}
            for name, findings in sd_findings.items()
            for f in findings
        ]
        trimmed_docs = [
            {"claim": f.claim, "document": f.document_name, "page": f.page_number, "section": f.section}
            for f in all_doc_findings
        ]

        sub_domain_names = [sd["name"] for sd in sub_domains]

        prompt = (
            f"Research Topic: {topic}\n\n"
            f"Sub-domains to cover (check all for gaps):\n"
            + "\n".join(f"- {n}" for n in sub_domain_names)
            + f"\n\nWeb Research Findings ({len(trimmed_web)} total):\n"
            + json.dumps(trimmed_web, indent=2)[:12000]
            + (f"\n\nDocument Findings ({len(trimmed_docs)} total):\n" + json.dumps(trimmed_docs, indent=2)[:4000] if trimmed_docs else "")
            + "\n\nSynthesize all findings. Preserve source URLs in citations."
        )

        return await synthesis_agent.run(prompt)

    # -----------------------------------------------------------------------
    # Phase 5: Report generation
    # -----------------------------------------------------------------------

    @observe_span("report_gen")
    async def _generate_report(self, topic: str, synthesis: SynthesisOutput) -> str | ErrorResult:
        # Build numbered citation list for inline references
        all_citations = synthesis.citations[:]
        for theme in synthesis.themes:
            all_citations.extend(theme.citations)
        for conflict in synthesis.conflicts:
            all_citations.extend(conflict.citations)

        # Deduplicate by URL
        seen: set[str] = set()
        unique_citations = []
        for c in all_citations:
            if c.source_url not in seen:
                unique_citations.append(c)
                seen.add(c.source_url)

        bib = "\n".join(
            f"[{i}] {c.title or c.source_url} — {c.source_url}"
            + (f" — {c.publication_date}" if c.publication_date else "")
            for i, c in enumerate(unique_citations, 1)
        )

        prompt = (
            f"Research Topic: {topic}\n\n"
            f"Synthesis:\n{json.dumps(synthesis.model_dump(), indent=2)[:14000]}\n\n"
            f"Bibliography (use these [Source N] numbers for inline citations):\n{bib}\n\n"
            "Write the full research report now."
        )

        return await report_gen_agent.run(prompt)

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    def _build_search_prompt(self, topic: str, sd: dict) -> str:
        return (
            f"Research Topic: {topic}\n"
            f"Your Sub-domain: {sd['name']}\n"
            f"Search Query: {sd['search_query']}\n"
            f"Scope: {sd['scope']}\n\n"
            "Find 3-5 high-quality, recent sources. Extract specific factual claims with verbatim evidence."
        )

    def _build_doc_prompt(self, topic: str, doc_ids: list[str]) -> str:
        return (
            f"Research Topic: {topic}\n"
            f"Documents to analyse: {', '.join(doc_ids)}\n\n"
            "Read each document and extract key claims with supporting evidence, page numbers, and sections."
        )

    def _load_documents(self, doc_paths: list[str]) -> dict[str, str]:
        doc_store: dict[str, str] = {}
        for path_str in doc_paths:
            path = Path(path_str)
            if not path.exists():
                print(f"  [Warning] Document not found: {path_str}")
                continue
            try:
                if path.suffix.lower() == ".pdf":
                    text = self._extract_pdf(path)
                else:
                    text = path.read_text(encoding="utf-8", errors="ignore")
                doc_store[path.name] = text
                print(f"  [Doc] Loaded: {path.name} ({len(text):,} chars)")
            except Exception as e:
                print(f"  [Doc] Failed to load {path.name}: {e}")
        return doc_store

    def _extract_pdf(self, path: Path) -> str:
        try:
            import pypdf
            reader = pypdf.PdfReader(str(path))
            return "\n".join(page.extract_text() or "" for page in reader.pages)
        except ImportError:
            return f"[PDF extraction unavailable — install pypdf: pip install pypdf. File: {path.name}]"

    def _update_manifest(
        self,
        manifest: ResearchManifest,
        sd_findings: dict[str, list[Finding]],
        sd_doc_findings: dict[str, list[DocFinding]],
    ) -> ResearchManifest:
        for sd in manifest.sub_domains:
            web = sd_findings.get(sd.name, [])
            docs = sd_doc_findings.get(sd.name, [])
            sd.findings = web
            sd.doc_findings = docs
            sd.source_count = len(web) + len(docs)
        return manifest

    def _fallback_report(
        self, topic: str, sub_domains: list[dict], sd_findings: dict[str, list[Finding]]
    ) -> str:
        all_findings = [f for findings in sd_findings.values() for f in findings]
        lines = [
            f"# Research Report: {topic}",
            "",
            f"*{len(all_findings)} findings across {len(sub_domains)} sub-domains.*",
            "",
        ]
        for sd in sub_domains:
            lines.append(f"## {sd['name']}")
            lines.append("")
            for i, f in enumerate(sd_findings.get(sd["name"], []), 1):
                lines.append(f"**{i}. {f.claim}**")
                lines.append(f"> {f.evidence_excerpt}")
                lines.append(f"Source: [{f.source_url}]({f.source_url})")
                lines.append("")
        return "\n".join(lines)
