from __future__ import annotations
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..discovery.schema import RawSchema
from .condenser import condense

GROUND_TRUTH_DIR = Path(__file__).parent.parent.parent / "evals" / "ground_truth"


@dataclass
class EvalResult:
    service: str
    source_file: str
    tool_count: int
    target_tool_count: int
    generated_tools: list[str]
    ground_truth_tools: list[str]
    matched: list[str]      # ground truth tools that were covered
    unmatched: list[str]    # ground truth tools that were not covered
    coverage_score: float   # matched / total ground truth
    conciseness_score: float
    overall_score: float
    errors: list[str] = field(default_factory=list)


def _nouns(tool_name: str) -> set[str]:
    """All meaningful words in a tool name (strip common verbs to focus on subject nouns)."""
    verbs = {"get", "list", "create", "add", "update", "edit", "delete", "remove",
             "manage", "set", "fetch", "retrieve", "search", "find", "check",
             "inspect", "simulate", "prompt", "send", "place", "put"}
    words = set(tool_name.lower().split("_"))
    nouns = words - verbs
    return nouns if nouns else words  # fall back to all words if no nouns remain


def _covers(gt_tool: str, generated_names: set[str]) -> bool:
    """True if any generated tool semantically covers the ground truth tool."""
    if gt_tool in generated_names:
        return True
    gt_nouns = _nouns(gt_tool)
    for gen in generated_names:
        gen_nouns = _nouns(gen)
        if gt_nouns & gen_nouns:  # any noun overlap
            return True
    return False


async def run_eval(schema_file: Optional[Path] = None) -> list[EvalResult]:
    files = [schema_file] if schema_file else sorted(GROUND_TRUTH_DIR.glob("*.json"))
    results = []
    for f in files:
        results.append(await _eval_single(f))
    return results


async def _eval_single(path: Path) -> EvalResult:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        raw = RawSchema(**data.get("raw_schema", data))
        ground_truth: list[str] = data.get("ground_truth_tools", [])

        condensed = await condense(raw)
        generated = [t.name for t in condensed.tools]
        generated_set = set(generated)

        matched   = [gt for gt in ground_truth if     _covers(gt, generated_set)]
        unmatched = [gt for gt in ground_truth if not _covers(gt, generated_set)]

        coverage    = len(matched) / max(len(ground_truth), 1)
        conciseness = 1.0 if len(generated) <= 15 else max(0.0, 1 - (len(generated) - 15) / 10)
        overall     = 0.6 * coverage + 0.4 * conciseness

        return EvalResult(
            service=raw.title,
            source_file=path.name,
            tool_count=len(generated),
            target_tool_count=len(ground_truth),
            generated_tools=generated,
            ground_truth_tools=ground_truth,
            matched=matched,
            unmatched=unmatched,
            coverage_score=coverage,
            conciseness_score=conciseness,
            overall_score=overall,
        )
    except Exception as exc:
        return EvalResult(
            service=path.stem,
            source_file=path.name,
            tool_count=0,
            target_tool_count=0,
            generated_tools=[],
            ground_truth_tools=[],
            matched=[],
            unmatched=[],
            coverage_score=0.0,
            conciseness_score=0.0,
            overall_score=0.0,
            errors=[str(exc)],
        )


def print_report(results: list[EvalResult]) -> None:
    SEP = "-" * 64
    print(f"\n{'Condensation Eval':^64}")
    print(SEP)
    for r in results:
        if r.errors:
            print(f"  ERROR  {r.source_file}")
            for e in r.errors:
                print(f"         {e}")
            print()
            continue
        grade = "PASS" if r.overall_score >= 0.7 else "FAIL"
        mark  = "[PASS]" if grade == "PASS" else "[FAIL]"
        print(f"\n{mark}  {r.service}  ({r.source_file})")
        print(f"  Generated : {r.tool_count} tools  |  Ground truth : {r.target_tool_count} concepts")
        print(f"  Coverage  : {r.coverage_score:.0%}   Conciseness : {r.conciseness_score:.0%}   Overall : {r.overall_score:.0%}")
        if r.unmatched:
            print(f"  Missing   : {', '.join(r.unmatched)}")
        if r.matched:
            print(f"  Covered   : {', '.join(r.matched)}")
        print(f"  Tools     : {', '.join(r.generated_tools)}")
    print(f"\n{SEP}")
    avg = sum(r.overall_score for r in results) / len(results) if results else 0
    passing = sum(1 for r in results if r.overall_score >= 0.7)
    print(f"  Result : {passing}/{len(results)} passing   Average score : {avg:.0%}")
    print(SEP)
