"""
eval/evaluators/common.py — 평가 공통 유틸

집계, 시점 정규화, 숫자 추출 등 evaluator 간 공유 로직.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

NUMBER_PATTERN = re.compile(r"\d+\.?\d*")


def extract_numbers(text: str) -> list[str]:
    """문자열에서 숫자 토큰 추출."""
    return NUMBER_PATTERN.findall(text or "")


def normalize_time_period(tp: str | None) -> str | None:
    """
    비교용 시점 정규화.

    - 빈 값 / 와일드카드(****) → None
    - YYYYMM (202301) → YYYY (2023)
    - YYYY-MM → YYYY-MM 유지
    - 범위 (2022-01..2022-11) → 그대로 (소문자 비교)
    """
    if tp is None:
        return None
    s = str(tp).strip()
    if not s or "*" in s:
        return None
    # KOSIS PRD_DE 형식: 202301, 202312
    if re.fullmatch(r"\d{6}", s):
        return s[:4]
    return s


def parse_item(item: dict) -> tuple[dict, dict, dict | None, str]:
    """result 아이템에서 자주 쓰는 필드 추출."""
    return (
        item.get("schema") or {},
        item.get("evidence") or {},
        item.get("graph_temporal"),
        item.get("claim_text") or "",
    )


def result_skip(reason: str, **extra: Any) -> dict:
    return {"pass": None, "reason": reason, **extra}


def result_pass(reason: str = "", **extra: Any) -> dict:
    return {"pass": True, "reason": reason, **extra}


def result_fail(reason: str, **extra: Any) -> dict:
    return {"pass": False, "reason": reason, **extra}


def aggregate_results(
    details: list[dict],
    check_keys: list[str],
) -> dict[str, dict]:
    """
    아이템별 상세 결과에서 pass/fail/skip 집계.

    details[i]는 { check_key: { "pass": True|False|None, ... }, ... } 형태.
    """
    counters: dict[str, dict[str, int]] = {
        k: {"pass": 0, "fail": 0, "skip": 0} for k in check_keys
    }
    for row in details:
        for key in check_keys:
            p = row.get(key, {}).get("pass")
            if p is True:
                counters[key]["pass"] += 1
            elif p is False:
                counters[key]["fail"] += 1
            else:
                counters[key]["skip"] += 1

    summary: dict[str, dict] = {}
    for key, cnt in counters.items():
        evaluable = cnt["pass"] + cnt["fail"]
        summary[key] = {
            "pass": cnt["pass"],
            "fail": cnt["fail"],
            "skip": cnt["skip"],
            "pass_rate": round(cnt["pass"] / evaluable, 3) if evaluable > 0 else None,
        }
    return summary


def run_checks_on_report(
    report: dict,
    checks: list[tuple[str, Callable[[dict], dict]]],
    row_prefix: Callable[[dict], dict] | None = None,
) -> dict:
    """
    report["results"]에 대해 등록된 검사 함수를 실행하고 집계한다.

    Args:
        report: pipeline JSON
        checks: [(key, check_fn), ...]
        row_prefix: 각 row에 공통 필드 추가 (sent_id, claim_text 등)
    """
    results = report.get("results", [])
    if not results:
        return {"total": 0, "summary": {}, "details": []}

    keys = [k for k, _ in checks]
    details: list[dict] = []

    for item in results:
        row = row_prefix(item) if row_prefix else {}
        for key, fn in checks:
            row[key] = fn(item)
        details.append(row)

    return {
        "total": len(results),
        "summary": aggregate_results(details, keys),
        "details": details,
    }


def consistency_row_prefix(item: dict) -> dict:
    return {
        "sent_id": item.get("sent_id"),
        "claim_text": (item.get("claim_text") or "")[:80],
    }


def judge_row_prefix(item: dict) -> dict:
    return consistency_row_prefix(item)


# ── 지표 표시 이름 (콘솔·README 공통) ─────────────────────────────────────────

METRIC_LABELS: dict[str, dict[str, str]] = {
    # 내적 일관성
    "value_traceability": {
        "ko": "스키마 수치 추적성",
        "en": "Value Traceability",
    },
    "unit_traceability": {
        "ko": "스키마 단위 추적성",
        "en": "Unit Traceability",
    },
    "temporal_3way_consistency": {
        "ko": "시계열 3-Way 일관성",
        "en": "Temporal 3-Way (graph↔schema↔evidence)",
    },
    "schema_completeness": {
        "ko": "스키마 완전성",
        "en": "Schema Completeness",
    },
    "verdict_diff_consistency": {
        "ko": "판정-차이율 일관성",
        "en": "Verdict-Diff Consistency",
    },
    "evidence_relevance": {
        "ko": "증거 관련성 (규칙)",
        "en": "Evidence Relevance (rule)",
    },
    # LLM Judge
    "indicator_judge": {
        "ko": "지표 의미 판정 (HCX)",
        "en": "Indicator Judge (HCX)",
    },
    "explanation_judge": {
        "ko": "설명 품질 판정 (HCX)",
        "en": "Explanation Judge (HCX)",
    },
    "evidence_relevance_judge": {
        "ko": "증거 적합성 판정 (HCX)",
        "en": "Evidence Relevance Judge (HCX)",
    },
}


def format_metric_label(key: str, *, lang: str = "both") -> str:
    """
    지표 키 → 표시 문자열.

    lang: "ko" | "en" | "both" (기본: 한글 · 영문)
    """
    entry = METRIC_LABELS.get(key)
    if not entry:
        return key
    if lang == "ko":
        return entry["ko"]
    if lang == "en":
        return entry["en"]
    return f"{entry['ko']} · {entry['en']}"
