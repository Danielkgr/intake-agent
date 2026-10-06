"""The evaluation: synthetic enquiries with expected labels, a deterministic scorer, and reports.

Grading is exact match on four fields.  No model grades anything.  Conflict recall is reported on
its own, because a missed conflict is the critical error.
"""

from __future__ import annotations

import hashlib
import json
import platform
import shlex
import subprocess
from collections import Counter
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from intake_agent import __version__
from intake_agent.record import TriageRecord
from intake_agent.schema import ConflictStatus, Enquiry, PracticeArea, Routing, Urgency

DATA_VERSION = "intake-eval-v1"
FIELDS = ("practice_area", "urgency", "conflict_status", "routing")


class Expected(BaseModel):
    model_config = ConfigDict(extra="forbid")

    practice_area: PracticeArea
    urgency: Urgency
    conflict_status: ConflictStatus
    routing: Routing


class EvalCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enquiry: Enquiry
    expected: Expected
    expects_clarifying_question: bool = False
    tags: list[str]
    rationale: str


def _dataset_text(path: Path | None) -> str:
    if path is None:
        return resources.files("intake_agent").joinpath("data/eval/enquiries.jsonl").read_text(encoding="utf-8")
    return path.read_text(encoding="utf-8")


def load_cases(path: Path | None = None) -> list[EvalCase]:
    return [EvalCase.model_validate_json(line) for line in _dataset_text(path).splitlines() if line.strip()]


def dataset_sha256(path: Path | None = None) -> str:
    return hashlib.sha256(_dataset_text(path).encode("utf-8")).hexdigest()


class CaseResult(BaseModel):
    enquiry_id: str
    expected: dict[str, str]
    predicted: dict[str, str | None]
    correct: dict[str, bool]
    all_correct: bool
    advice_flagged: bool
    failures: list[str]
    asked_question: bool
    expects_clarifying_question: bool
    tags: list[str]


class FieldScore(BaseModel):
    correct: int
    total: int
    accuracy: float


class ConflictScore(BaseModel):
    expected_conflicts: int
    detected: int
    recall: float | None
    missed: list[str]
    false_alarms: list[str]


class EvalSummary(BaseModel):
    arm: str
    cases: int
    fields: dict[str, FieldScore]
    all_four_correct: int
    conflict: ConflictScore
    advice_flags: int
    advice_flagged_cases: list[str]
    failures_by_kind: dict[str, int]
    missing_info_cases: int
    missing_info_with_question: int
    usage: dict[str, Any] | None = None


def predicted_fields(record: TriageRecord) -> dict[str, str | None]:
    return {
        "practice_area": record.practice_area.value if record.practice_area else None,
        "urgency": record.urgency.value if record.urgency else None,
        "conflict_status": record.conflict.status.value,
        "routing": record.routing.decision.value,
    }


def score(cases: Sequence[EvalCase], records: Sequence[TriageRecord], arm: str) -> tuple[EvalSummary, list[CaseResult]]:
    by_id = {record.enquiry_id: record for record in records}
    results: list[CaseResult] = []
    for case in cases:
        record = by_id[case.enquiry.enquiry_id]
        expected = case.expected.model_dump(mode="json")
        predicted = predicted_fields(record)
        correct = {field: predicted[field] == expected[field] for field in FIELDS}
        results.append(
            CaseResult(
                enquiry_id=case.enquiry.enquiry_id,
                expected=expected,
                predicted=predicted,
                correct=correct,
                all_correct=all(correct.values()),
                advice_flagged=bool(record.checks.advice and record.checks.advice.flagged),
                failures=[failure.kind for failure in record.failures],
                asked_question=bool(record.clarifying_questions),
                expects_clarifying_question=case.expects_clarifying_question,
                tags=case.tags,
            )
        )

    total = len(results)
    fields = {}
    for field in FIELDS:
        hits = sum(result.correct[field] for result in results)
        fields[field] = FieldScore(correct=hits, total=total, accuracy=round(hits / total, 4) if total else 0.0)
    conflict_value = ConflictStatus.POTENTIAL_CONFLICT.value
    positives = [r for r in results if r.expected["conflict_status"] == conflict_value]
    detected = [r for r in positives if r.predicted["conflict_status"] == conflict_value]
    false_alarms = [
        r.enquiry_id
        for r in results
        if r.expected["conflict_status"] != conflict_value and r.predicted["conflict_status"] == conflict_value
    ]
    missing_info = [r for r in results if r.expects_clarifying_question]
    summary = EvalSummary(
        arm=arm,
        cases=total,
        fields=fields,
        all_four_correct=sum(result.all_correct for result in results),
        conflict=ConflictScore(
            expected_conflicts=len(positives),
            detected=len(detected),
            recall=round(len(detected) / len(positives), 4) if positives else None,
            missed=[r.enquiry_id for r in positives if r not in detected],
            false_alarms=false_alarms,
        ),
        advice_flags=sum(result.advice_flagged for result in results),
        advice_flagged_cases=[r.enquiry_id for r in results if r.advice_flagged],
        failures_by_kind=dict(Counter(kind for result in results for kind in result.failures)),
        missing_info_cases=len(missing_info),
        missing_info_with_question=sum(result.asked_question for result in missing_info),
        usage=_usage_totals(records),
    )
    return summary, results


def _usage_totals(records: Sequence[TriageRecord]) -> dict[str, Any] | None:
    usages = [record.usage for record in records if record.usage is not None]
    if not usages:
        return None
    costs = [usage.estimated_cost_usd for usage in usages]
    known = [cost for cost in costs if cost is not None]
    return {
        "requests": sum(usage.requests for usage in usages),
        "input_tokens": sum(usage.input_tokens for usage in usages),
        "output_tokens": sum(usage.output_tokens for usage in usages),
        "cache_creation_input_tokens": sum(usage.cache_creation_input_tokens for usage in usages),
        "cache_read_input_tokens": sum(usage.cache_read_input_tokens for usage in usages),
        "estimated_cost_usd": round(sum(known), 6),
        "estimated_cost_per_enquiry_usd": round(sum(known) / len(known), 6) if known else None,
        "records_without_a_price": len(costs) - len(known),
        "fallback_used": sum(bool(record.model and record.model.fallback_used) for record in records),
    }


def run_cases(cases: Sequence[EvalCase], triage: Callable[[Enquiry], TriageRecord]) -> list[TriageRecord]:
    """Triage every case in order.  Sequential runs let each request read the cached prefix."""
    return [triage(case.enquiry) for case in cases]


def _git_revision() -> str:
    try:
        completed = subprocess.run(
            ["git", "describe", "--always", "--dirty"], capture_output=True, text=True, check=True, timeout=10
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return completed.stdout.strip() or "unknown"


def provenance(arm: str, settings: dict[str, Any] | None, argv: Sequence[str]) -> dict[str, Any]:
    return {
        "arm": arm,
        "command": shlex.join(["intake-agent", *argv]),
        "run_at_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "package_version": __version__,
        "code_revision": _git_revision(),
        "python": platform.python_version(),
        "data_version": DATA_VERSION,
        "data_sha256": dataset_sha256(),
        "settings": settings,
    }


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def render_report(summary: EvalSummary, results: Sequence[CaseResult], meta: dict[str, Any]) -> str:
    lines = [
        f"# Evaluation results: {summary.arm} arm",
        "",
        f"Run `{meta['command']}` at {meta['run_at_utc']} on code revision `{meta['code_revision']}`, "
        f"data `{meta['data_version']}` (SHA-256 `{meta['data_sha256'][:16]}...`), {summary.cases} enquiries.",
        "",
        "| Measure | Result |",
        "|---|---|",
    ]
    for field in FIELDS:
        score_ = summary.fields[field]
        lines.append(f"| {field} exact match | {score_.correct} / {score_.total} ({_pct(score_.accuracy)}) |")
    conflict = summary.conflict
    lines += [
        f"| All four fields correct | {summary.all_four_correct} / {summary.cases} |",
        f"| Conflict recall | {conflict.detected} / {conflict.expected_conflicts} ({_pct(conflict.recall)}) |",
        f"| Missed conflicts | {', '.join(conflict.missed) or 'none'} |",
        f"| Conflict false alarms | {', '.join(conflict.false_alarms) or 'none'} |",
        f"| Holding replies flagged by the advice check | {summary.advice_flags} |",
        f"| Failures by kind | {json.dumps(summary.failures_by_kind) if summary.failures_by_kind else 'none'} |",
        f"| Missing-information cases with a clarifying question | "
        f"{summary.missing_info_with_question} / {summary.missing_info_cases} |",
    ]
    if summary.usage:
        lines += [
            f"| Estimated cost, all enquiries | ${summary.usage['estimated_cost_usd']:.4f} |",
            f"| Cache read input tokens | {summary.usage['cache_read_input_tokens']} |",
        ]
    misses = [result for result in results if not result.all_correct]
    lines += ["", "## Cases with at least one wrong field", ""]
    if not misses:
        lines.append("None.")
    else:
        lines += ["| Case | Field | Expected | Predicted |", "|---|---|---|---|"]
        for result in misses:
            for field in FIELDS:
                if not result.correct[field]:
                    lines.append(
                        f"| {result.enquiry_id} | {field} | {result.expected[field]} | {result.predicted[field]} |"
                    )
    return "\n".join(lines) + "\n"


def write_results(
    out_dir: Path,
    summary: EvalSummary,
    results: Sequence[CaseResult],
    records: Sequence[TriageRecord],
    meta: dict[str, Any],
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "provenance": meta,
        "summary": summary.model_dump(mode="json"),
        "cases": [r.model_dump() for r in results],
    }
    (out_dir / "results.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (out_dir / "records.jsonl").write_text(
        "".join(record.model_dump_json() + "\n" for record in records), encoding="utf-8"
    )
    (out_dir / "report.md").write_text(render_report(summary, results, meta), encoding="utf-8")
