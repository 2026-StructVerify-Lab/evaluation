"""
structverify/eval/export.py — VerificationReport → eval 입력 JSON 변환

FastAPI job.result["eval_export"], run_pipeline_Text.py, eval/run_eval.py 가
공통으로 사용하는 results[] 스키마를 생성한다.
"""
from __future__ import annotations

from typing import Any

from structverify.core.schemas import VerificationReport
from structverify.graph.claim_graph import ClaimGraph


def _schema_dict(claim) -> dict[str, Any]:
    s = claim.schema
    if not s:
        return {
            "indicator": None,
            "value": None,
            "unit": None,
            "time_period": None,
            "population": None,
            "source_reference": None,
        }
    return {
        "indicator": s.indicator,
        "value": s.value,
        "unit": s.unit,
        "time_period": s.time_period,
        "population": s.population,
        "source_reference": s.source_reference,
    }


def _graph_temporal_dict(prov: dict | None) -> dict[str, Any] | None:
    if not prov:
        return None
    return {
        "expression": prov.get("expression"),
        "resolved": prov.get("resolved"),
        "basis": prov.get("basis"),
        "via_coref": prov.get("via_coref"),
    }


def _evidence_dict(evidence) -> dict[str, Any] | None:
    if evidence is None:
        return None
    return {
        "stat_table_id": evidence.stat_table_id,
        "source_name": evidence.source_name,
        "official_value": evidence.official_value,
        "unit": evidence.unit,
        "time_period": evidence.time_period,
        "query_keyword": (
            evidence.provenance.query_used if evidence.provenance else None
        ),
        "category_path": evidence.category_path,
    }


def _result_item(claim, result, graph: ClaimGraph) -> dict[str, Any]:
    prov = graph.temporal_provenance(claim)
    return {
        "sent_id": claim.sent_id,
        "claim_text": claim.claim_text,
        "schema": _schema_dict(claim),
        "graph_temporal": _graph_temporal_dict(prov),
        "evidence": _evidence_dict(result.evidence),
        "verdict": result.verdict.value,
        "confidence": result.confidence,
        "mismatch_type": (
            result.mismatch_type.value if result.mismatch_type else None
        ),
        "explanation": result.explanation,
    }


def report_to_eval_json(report: VerificationReport) -> dict[str, Any]:
    """
    VerificationReport → eval 모듈 입력 JSON.

    Returns:
        domain, anchor_year, graph_stats, verdict_distribution, results[]
    """
    graph = ClaimGraph(report.graph_nodes, report.graph_edges)
    anchor_year = graph.get_anchor_year()

    verdict_distribution: dict[str, int] = {}
    results: list[dict[str, Any]] = []

    for claim, result in zip(report.claims, report.results):
        v = result.verdict.value
        verdict_distribution[v] = verdict_distribution.get(v, 0) + 1
        results.append(_result_item(claim, result, graph))

    return {
        "domain": report.domain_pack_used,
        "anchor_year": anchor_year,
        "graph_stats": graph.stats(),
        "verdict_distribution": verdict_distribution,
        "results": results,
    }
