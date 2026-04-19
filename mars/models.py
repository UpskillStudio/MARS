from __future__ import annotations

from enum import Enum
from typing import Any, Union

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Web Search subagent output
# ---------------------------------------------------------------------------

class Finding(BaseModel):
    claim: str
    evidence_excerpt: str
    source_url: str
    publication_date: str = ""
    relevance_score: float = Field(ge=0.0, le=1.0, default=0.5)


# ---------------------------------------------------------------------------
# Document Analysis subagent output
# ---------------------------------------------------------------------------

class DocFinding(BaseModel):
    claim: str
    evidence_excerpt: str
    document_name: str
    page_number: int = 0
    section: str = ""
    publication_date: str = ""


# ---------------------------------------------------------------------------
# Synthesis subagent output
# ---------------------------------------------------------------------------

class Citation(BaseModel):
    source_url: str
    title: str = ""
    publication_date: str = ""
    excerpt: str = ""


class Theme(BaseModel):
    title: str
    summary: str
    supporting_claims: list[str] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)


class Conflict(BaseModel):
    description: str
    positions: list[str] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)


class CoverageGap(BaseModel):
    sub_domain: str
    source_count: int
    note: str = ""


class SynthesisOutput(BaseModel):
    themes: list[Theme] = Field(default_factory=list)
    conflicts: list[Conflict] = Field(default_factory=list)
    gaps: list[CoverageGap] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Structured error (design §5.3)
# ---------------------------------------------------------------------------

class ErrorCategory(str, Enum):
    transient = "transient"
    validation = "validation"
    permission = "permission"


class ErrorResult(BaseModel):
    isError: bool = True
    errorCategory: ErrorCategory
    isRetryable: bool
    attemptedQuery: str = ""
    partialResults: list[Any] = Field(default_factory=list)
    alternativeApproaches: list[str] = Field(default_factory=list)
    message: str


# ---------------------------------------------------------------------------
# Coverage tracking (coordinator internal)
# ---------------------------------------------------------------------------

class SubDomainStatus(BaseModel):
    name: str
    source_count: int = 0
    findings: list[Finding] = Field(default_factory=list)
    doc_findings: list[DocFinding] = Field(default_factory=list)


class ResearchManifest(BaseModel):
    topic: str
    sub_domains: list[SubDomainStatus] = Field(default_factory=list)
    quality_threshold: int = 2  # min sources per sub-domain

    def coverage_sufficient(self) -> bool:
        return all(s.source_count >= self.quality_threshold for s in self.sub_domains)

    def gaps(self) -> list[SubDomainStatus]:
        return [s for s in self.sub_domains if s.source_count < self.quality_threshold]


# ---------------------------------------------------------------------------
# Union type returned by all subagent runners
# ---------------------------------------------------------------------------

SubagentResult = Union[list[Finding], list[DocFinding], SynthesisOutput, str, ErrorResult]
