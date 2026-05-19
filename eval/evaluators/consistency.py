"""
eval/evaluators/consistency.py — 내적 일관성 검사 (골든셋 불필요)

각 함수는 pipeline JSON의 result 아이템(dict)을 받아 검사 결과 dict를 반환한다.
"""
from __future__ import annotations

import re
from collections.abc import Callable

from eval.evaluators.common import (
    aggregate_results,
    consistency_row_prefix,
    extract_numbers,
    normalize_time_period,
    parse_item,
    result_fail,
    result_pass,
    result_skip,
    run_checks_on_report,
)

# ── 시계열 상대 표현 (3-Way SKIP 메시지용) ─────────────────────────────────────

_RELATIVE_TIME_PATTERN = re.compile(
    r"작년|재작년|지난해|올해|내년|전년|어제|전월|당월|지난\s*\d+월",
)

REQUIRED_SCHEMA_FIELDS = ("value", "unit", "indicator", "time_period")


# ── 개별 검사 ─────────────────────────────────────────────────────────────────

def check_value_traceability(item: dict) -> dict:
    schema, _, _, claim_text = parse_item(item)
    value = schema.get("value")

    if value is None:
        return result_skip("schema.value가 null", numbers_in_text=[])

    numbers_in_text = extract_numbers(claim_text)
    value_str = str(value)
    value_str_int = str(int(value)) if value == int(value) else None
    passed = value_str in numbers_in_text or (
        value_str_int is not None and value_str_int in numbers_in_text
    )
    return {
        "pass": passed,
        "schema_value": value,
        "numbers_in_text": numbers_in_text,
        "reason": "원문에서 수치 추적 성공"
        if passed
        else f"schema.value={value}가 원문 수치 {numbers_in_text}에 없음",
    }


def check_unit_traceability(item: dict) -> dict:
    schema, _, _, claim_text = parse_item(item)
    unit = schema.get("unit")

    if not unit:
        return result_skip("schema.unit이 null")

    passed = unit in claim_text
    return {
        "pass": passed,
        "schema_unit": unit,
        "reason": "원문에서 단위 추적 성공"
        if passed
        else f"unit={unit!r}이 원문에 없음 (단위 표현 변환 가능성 있음 — 수동 확인 권장)",
    }


def check_temporal_3way_consistency(item: dict) -> dict:
    """
    graph.resolved ↔ schema.time_period ↔ evidence.time_period (정규화 후 비교).

    FAIL 시 broken_at: L5_schema | L7_evidence
    """
    schema, evidence, graph_temporal, claim_text = parse_item(item)
    schema_tp = normalize_time_period(schema.get("time_period"))
    graph_resolved = normalize_time_period(
        (graph_temporal or {}).get("resolved")
    )
    evidence_tp = normalize_time_period(evidence.get("time_period"))

    if schema_tp is None:
        return {**result_skip("schema.time_period 없거나 와일드카드"), "mode": "skip"}

    if graph_resolved is None:
        has_relative = bool(_RELATIVE_TIME_PATTERN.search(claim_text))
        return {
            **result_skip(
                "graph_temporal.resolved 없음 — 상대 시점 문장인데 그래프 미연결 의심"
                if has_relative
                else "graph_temporal 없음 → 3-way 불가"
            ),
            "mode": "skip",
            "has_relative_expression": has_relative,
        }

    if graph_resolved != schema_tp:
        return {
            **result_fail(
                f"그래프={graph_resolved!r} vs 스키마={schema_tp!r} 불일치 "
                "(스키마 유도가 temporal hint 무시)",
                broken_at="L5_schema",
            ),
            "mode": "2way",
            "graph_resolved": graph_resolved,
            "schema_time_period": schema_tp,
            "evidence_time_period": evidence_tp,
            "graph_expression": (graph_temporal or {}).get("expression"),
        }

    if evidence_tp is None:
        return {
            **result_pass("graph↔schema 일치, evidence 없음 (2-way PASS)"),
            "mode": "2way",
            "graph_resolved": graph_resolved,
            "schema_time_period": schema_tp,
            "evidence_time_period": None,
        }

    if graph_resolved == schema_tp == evidence_tp:
        return {
            **result_pass("graph ↔ schema ↔ evidence 시점 일치"),
            "mode": "3way",
            "graph_resolved": graph_resolved,
            "schema_time_period": schema_tp,
            "evidence_time_period": evidence_tp,
            "graph_expression": (graph_temporal or {}).get("expression"),
        }

    return {
        **result_fail(
            f"graph·schema={graph_resolved!r} 일치, "
            f"evidence={evidence_tp!r} 불일치 (KOSIS 검색/연도 필터 의심)",
            broken_at="L7_evidence",
        ),
        "mode": "3way",
        "graph_resolved": graph_resolved,
        "schema_time_period": schema_tp,
        "evidence_time_period": evidence_tp,
        "graph_expression": (graph_temporal or {}).get("expression"),
    }


def check_schema_completeness(item: dict) -> dict:
    schema, _, _, _ = parse_item(item)
    filled = [f for f in REQUIRED_SCHEMA_FIELDS if schema.get(f) is not None]
    missing = [f for f in REQUIRED_SCHEMA_FIELDS if schema.get(f) is None]
    score = len(filled) / len(REQUIRED_SCHEMA_FIELDS)
    return {
        "pass": score >= 0.75,
        "score": round(score, 2),
        "filled": filled,
        "missing": missing,
    }


def check_verdict_diff_consistency(item: dict) -> dict:
    schema, evidence, _, _ = parse_item(item)
    verdict = item.get("verdict", "")

    if not evidence or evidence.get("official_value") is None:
        return result_skip("evidence 없음 → skip", diff_pct=None)

    claim_val = schema.get("value")
    official_val = evidence.get("official_value")

    if claim_val is None:
        return result_skip("schema.value 없음 → skip", diff_pct=None)
    if official_val == 0:
        return result_skip("official_value=0 → diff_pct 계산 불가", diff_pct=None)

    diff_pct = abs(claim_val - official_val) / abs(official_val) * 100

    if diff_pct <= 10:
        expected = "match"
    elif diff_pct > 90:
        expected = "unverifiable"
    elif diff_pct > 30:
        expected = "mismatch"
    else:
        expected = "unverifiable"

    contradiction = (diff_pct <= 10 and verdict == "mismatch") or (
        diff_pct > 10 and verdict == "match"
    )
    return {
        "pass": not contradiction,
        "verdict": verdict,
        "expected_verdict": expected,
        "diff_pct": round(diff_pct, 2),
        "claim_value": claim_val,
        "official_value": official_val,
        "reason": "verdict와 수치 차이가 일관성 있음"
        if not contradiction
        else f"모순: diff_pct={diff_pct:.1f}% 인데 verdict={verdict!r} (기대={expected!r})",
    }


def check_evidence_relevance(item: dict) -> dict:
    schema, evidence, _, _ = parse_item(item)
    indicator = schema.get("indicator") or ""
    query_keyword = evidence.get("query_keyword") or ""
    source_name = evidence.get("source_name") or ""

    if not indicator or not evidence:
        return result_skip("indicator 또는 evidence 없음 → skip")
    if not query_keyword:
        return result_skip("query_keyword 없음 (provenance 미저장) → skip")

    indicator_tokens = [t for t in re.split(r"[\s>|/]", indicator) if len(t) >= 2]
    keyword_overlap = any(tok in query_keyword for tok in indicator_tokens)
    source_overlap = any(tok in source_name for tok in indicator_tokens)

    return {
        "pass": keyword_overlap,
        "indicator": indicator,
        "query_keyword": query_keyword,
        "source_name": source_name,
        "keyword_overlap": keyword_overlap,
        "source_overlap": source_overlap,
        "reason": "indicator 토큰이 쿼리 키워드에 포함됨"
        if keyword_overlap
        else f"indicator={indicator!r} 토큰이 쿼리={query_keyword!r}에 없음 → 검색 오매핑 의심",
    }


# ── 검사 등록 (단일 소스) ─────────────────────────────────────────────────────

CONSISTENCY_CHECKS: list[tuple[str, Callable[[dict], dict]]] = [
    ("value_traceability", check_value_traceability),
    ("unit_traceability", check_unit_traceability),
    ("temporal_3way_consistency", check_temporal_3way_consistency),
    ("schema_completeness", check_schema_completeness),
    ("verdict_diff_consistency", check_verdict_diff_consistency),
    ("evidence_relevance", check_evidence_relevance),
]

CONSISTENCY_CHECK_KEYS = [k for k, _ in CONSISTENCY_CHECKS]

# FAIL 상세 출력용 (completeness는 pass_rate만 참고)
CONSISTENCY_FAIL_KEYS = [
    "value_traceability",
    "unit_traceability",
    "temporal_3way_consistency",
    "verdict_diff_consistency",
    "evidence_relevance",
]


def run_all_consistency_checks(report: dict) -> dict:
    """report JSON 전체에 대해 내적 일관성 검사 실행."""
    return run_checks_on_report(
        report,
        CONSISTENCY_CHECKS,
        row_prefix=consistency_row_prefix,
    )
