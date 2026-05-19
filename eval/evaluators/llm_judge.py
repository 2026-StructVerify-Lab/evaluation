"""
eval/evaluators/llm_judge.py — HCX-as-Judge 정성 평가
"""
from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from typing import Any

from eval.evaluators.common import (
    aggregate_results,
    extract_numbers,
    judge_row_prefix,
    result_skip,
)

# ── 프롬프트 ─────────────────────────────────────────────────────────────────

_INDICATOR_SYSTEM = (
    "너는 한국 통계청 지표 전문가다. "
    "원문 문장과 추출된 indicator를 보고 의미적 정확성을 판단한다. "
    "판단 기준은 오직 통계 지표명의 의미적 정확성이며, "
    "표현 방식의 차이는 무시하고 의미 일치 여부만 본다."
)

_INDICATOR_PROMPT = """\
[원문]
{claim_text}

[추출된 indicator]
{indicator}

원문의 통계 지표와 추출된 indicator가 의미상 일치하는지 판단하라.

1. 근거: 원문에서 실제로 언급한 지표와 추출된 indicator를 비교해 한 문장으로 서술하라.
2. 판정: 마지막 줄에 PASS 또는 FAIL 중 하나만 출력하라.

PASS 기준: indicator가 원문 통계 지표의 의미를 실질적으로 표현한다.
FAIL 기준: indicator가 원문과 다른 지표를 가리키거나 의미를 왜곡한다.\
"""

_EXPLANATION_SYSTEM = (
    "너는 한국어 팩트체크 품질 심사관이다. "
    "아래 팩트체크 결과와 생성된 설명을 보고 논리적 일관성을 판단한다."
)

_EXPLANATION_PROMPT = """\
[원문 주장]
{claim_text}

[KOSIS 공식 수치]
{official_value} {official_unit} ({time_period})

[판정]
{verdict}

[생성된 설명]
{explanation}

위 설명이 판정 근거를 논리적으로 서술하고 있는지 판단하라.

1. 근거: 설명에서 어색하거나 틀린 부분이 있으면 구체적으로 지적하라. 없으면 "이상 없음"이라고 하라.
2. 판정: 마지막 줄에 PASS 또는 FAIL 중 하나만 출력하라.

FAIL 기준:
- 원문/증거에 없는 수치를 설명에서 언급한다 (환각)
- 판정 결과와 설명 내용이 모순된다
- 설명이 판정 근거를 전혀 서술하지 않는다\
"""

_EVIDENCE_RELEVANCE_SYSTEM = (
    "너는 한국 통계청 데이터 전문가다. "
    "통계 지표명과 KOSIS 테이블명을 보고 해당 테이블이 지표를 검증하기에 적합한지 판단한다."
)

_EVIDENCE_RELEVANCE_PROMPT = """\
[indicator (검증 대상 통계 지표)]
{indicator}

[KOSIS 테이블명 (실제 검색된 증거)]
{source_name}

[검색에 사용된 쿼리 키워드]
{query_keyword}

위 KOSIS 테이블이 indicator를 검증하기에 적합한지 판단하라.

1. 근거: indicator와 테이블명이 다루는 통계 범주가 일치하는지 한 문장으로 서술하라.
2. 판정: 마지막 줄에 PASS 또는 FAIL 중 하나만 출력하라.

PASS 기준: 테이블이 indicator와 같은 통계 범주를 다룬다.
FAIL 기준: 테이블이 indicator와 전혀 다른 통계 범주다 (예: 실업률인데 기상 테이블).\
"""


# ── 유틸 ─────────────────────────────────────────────────────────────────────

def _extract_verdict(text: str) -> str:
    lines = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]
    for line in reversed(lines):
        upper = line.upper()
        if "PASS" in upper:
            return "PASS"
        if "FAIL" in upper:
            return "FAIL"
    return "UNKNOWN"


async def _judge_pass_fail(
    llm_client: Any,
    *,
    system_prompt: str,
    prompt: str,
    extra: dict | None = None,
) -> dict:
    """공통 LLM PASS/FAIL 판정."""
    try:
        response = await llm_client.generate(
            prompt=prompt,
            system_prompt=system_prompt,
            model_tier="heavy",
        )
        verdict = _extract_verdict(response)
        out = {
            "pass": verdict == "PASS",
            "verdict": verdict,
            "llm_response": response,
        }
        if extra:
            out.update(extra)
        return out
    except Exception as exc:
        return result_skip(f"LLM 호출 실패: {exc}")


def _check_hallucination_rule(item: dict) -> dict:
    explanation = item.get("explanation") or ""
    claim_text = item.get("claim_text") or ""
    evidence = item.get("evidence") or {}

    if not explanation:
        return result_skip("explanation 없음")

    known: set[str] = set(extract_numbers(claim_text))
    official = evidence.get("official_value")
    if official is not None:
        known.add(str(official))
        if official == int(official):
            known.add(str(int(official)))

    expl_nums = set(extract_numbers(explanation))
    hallucinated = {
        n for n in (expl_nums - known)
        if not (1900 <= float(n) <= 2100)
    }
    passed = len(hallucinated) == 0
    return {
        "pass": passed,
        "method": "rule",
        "explanation_numbers": sorted(expl_nums),
        "known_numbers": sorted(known),
        "hallucinated_candidates": sorted(hallucinated),
        "needs_llm_review": not passed,
        "reason": "환각 의심 수치 없음"
        if passed
        else f"환각 의심 수치: {sorted(hallucinated)}",
    }


# ── 개별 판정 ───────────────────────────────────────────────────────────────

async def judge_indicator(item: dict, llm_client: Any) -> dict:
    schema = item.get("schema") or {}
    indicator = schema.get("indicator")
    if not indicator:
        return result_skip("indicator 없음")

    return await _judge_pass_fail(
        llm_client,
        system_prompt=_INDICATOR_SYSTEM,
        prompt=_INDICATOR_PROMPT.format(
            claim_text=item.get("claim_text", ""),
            indicator=indicator,
        ),
        extra={"indicator": indicator},
    )


async def judge_explanation(item: dict, llm_client: Any) -> dict:
    rule_result = _check_hallucination_rule(item)
    explanation = item.get("explanation") or ""
    evidence = item.get("evidence") or {}

    if not explanation:
        return {**result_skip("explanation 없음"), "rule_check": rule_result}

    if rule_result.get("pass") is True:
        return {
            "pass": True,
            "method": "rule_only",
            "rule_check": rule_result,
            "reason": "규칙 검사 통과 — LLM 호출 생략",
        }

    result = await _judge_pass_fail(
        llm_client,
        system_prompt=_EXPLANATION_SYSTEM,
        prompt=_EXPLANATION_PROMPT.format(
            claim_text=item.get("claim_text", ""),
            official_value=evidence.get("official_value", "N/A"),
            official_unit=evidence.get("unit", ""),
            time_period=evidence.get("time_period", "N/A"),
            verdict=item.get("verdict", "N/A"),
            explanation=explanation,
        ),
    )
    result["method"] = "llm"
    result["rule_check"] = rule_result
    return result


async def judge_evidence_relevance(item: dict, llm_client: Any) -> dict:
    schema = item.get("schema") or {}
    evidence = item.get("evidence") or {}
    indicator = schema.get("indicator")
    source_name = evidence.get("source_name")
    query_keyword = evidence.get("query_keyword", "")

    if not indicator or not source_name:
        return result_skip("indicator 또는 source_name 없음 → skip")

    return await _judge_pass_fail(
        llm_client,
        system_prompt=_EVIDENCE_RELEVANCE_SYSTEM,
        prompt=_EVIDENCE_RELEVANCE_PROMPT.format(
            indicator=indicator,
            source_name=source_name,
            query_keyword=query_keyword or "(없음)",
        ),
        extra={
            "indicator": indicator,
            "source_name": source_name,
            "query_keyword": query_keyword,
        },
    )


# ── 검사 등록 ─────────────────────────────────────────────────────────────────

JUDGE_CHECKS: list[tuple[str, Callable]] = [
    ("indicator_judge", judge_indicator),
    ("explanation_judge", judge_explanation),
    ("evidence_relevance_judge", judge_evidence_relevance),
]

JUDGE_CHECK_KEYS = [k for k, _ in JUDGE_CHECKS]
JUDGE_FAIL_KEYS = list(JUDGE_CHECK_KEYS)


async def _judge_one(item: dict, llm_client: Any) -> dict:
    row = judge_row_prefix(item)
    awaitables = [fn(item, llm_client) for _, fn in JUDGE_CHECKS]
    results = await asyncio.gather(*awaitables)
    for (key, _), result in zip(JUDGE_CHECKS, results):
        row[key] = result
    return row


async def run_all_judge_checks(report: dict, llm_client: Any) -> dict:
    results_items = report.get("results", [])
    if not results_items:
        return {"total": 0, "summary": {}, "details": []}

    details = await asyncio.gather(
        *[_judge_one(item, llm_client) for item in results_items]
    )
    return {
        "total": len(results_items),
        "summary": aggregate_results(list(details), JUDGE_CHECK_KEYS),
        "details": list(details),
    }
