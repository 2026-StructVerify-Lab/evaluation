"""
eval/run_eval.py — 전체 평가 실행 진입점

사용법:
    # 내적 일관성 검사만 (LLM 호출 없음, 즉시 실행)
    python eval/run_eval.py

    # 내적 일관성 + LLM Judge 함께
    CLOVASTUDIO_API_KEY=nv-xxx python eval/run_eval.py --judge

    # 특정 파일만
    python eval/run_eval.py --file test_outputs/pipeline_text_result.json

    # LLM Judge만 skip_consistency 옵션으로
    python eval/run_eval.py --judge --skip-consistency

실행 흐름:
    eval/data/samples/*.json 로드
      → consistency.py 실행  → 항목별 pass율 집계
      → llm_judge.py 실행    → PASS/FAIL 집계 (--judge 옵션 시)
      → 결과 eval/results/eval_{timestamp}.json 저장
      → 콘솔에 요약 테이블 출력
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import unicodedata
from datetime import datetime
from pathlib import Path

# 프로젝트 루트를 sys.path에 추가 (eval/ 폴더에서 실행 시 import 오류 방지)
_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

# 프로젝트 루트의 .env 자동 로드 (CLOVASTUDIO_API_KEY 등)
try:
    from dotenv import load_dotenv
    load_dotenv(_ROOT / ".env")
except ImportError:
    # dotenv 미설치 시 직접 파싱 (fallback)
    _env_path = _ROOT / ".env"
    if _env_path.exists():
        for _line in _env_path.read_text(encoding="utf-8").splitlines():
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _, _v = _line.partition("=")
                os.environ.setdefault(_k.strip(), _v.strip())

from eval.evaluators.common import format_metric_label
from eval.evaluators.consistency import (
    CONSISTENCY_FAIL_KEYS,
    run_all_consistency_checks,
)
from eval.evaluators.llm_judge import JUDGE_FAIL_KEYS


# ── 유틸 ─────────────────────────────────────────────────────────────────────

def _load_samples(file_arg: str | None) -> list[tuple[str, dict]]:
    """
    평가할 JSON 파일 목록을 로드한다.

    Args:
        file_arg: --file 옵션으로 지정한 경로. None이면 eval/data/samples/*.json 전체.

    Returns:
        [(파일명, report_dict), ...]
    """
    if file_arg:
        path = Path(file_arg)
        if not path.exists():
            print(f"[오류] 파일 없음: {path}")
            sys.exit(1)
        with open(path, encoding="utf-8") as f:
            return [(path.name, json.load(f))]

    samples_dir = Path(__file__).parent / "data" / "samples"
    json_files = sorted(samples_dir.glob("*.json"))
    if not json_files:
        print(f"[오류] {samples_dir} 에 JSON 파일 없음")
        print("  힌트: python scripts/run_pipeline_Text.py 실행 후")
        print("        cp test_outputs/pipeline_text_result.json eval/data/samples/")
        sys.exit(1)

    result = []
    for p in json_files:
        with open(p, encoding="utf-8") as f:
            result.append((p.name, json.load(f)))
    return result


def _build_llm_client() -> object:
    """LLMClient 인스턴스 생성. API 키 없으면 오류 출력 후 종료."""
    from structverify.utils.llm_client import LLMClient

    api_key = os.environ.get("CLOVASTUDIO_API_KEY") or os.environ.get("NCP_API_KEY")
    if not api_key:
        print("[오류] CLOVASTUDIO_API_KEY 또는 NCP_API_KEY 가 없음")
        print("  힌트: 프로젝트 루트 .env 에 CLOVASTUDIO_API_KEY=nv-xxx 추가")
        sys.exit(1)

    return LLMClient(
        config={
            "provider": "hcx",
            "models": {
                "heavy": "HCX-003",
                "light": "HCX-DASH-002",
                "structured": "HCX-007",
            },
            "temperature": 0.1,
            "max_tokens": 1024,
            "api_key_env": "CLOVASTUDIO_API_KEY",
        }
    )


# ── 출력 포맷 ─────────────────────────────────────────────────────────────────

_SUMMARY_INDENT = "  "
_SUMMARY_LABEL_DISPLAY_W = 30  # 한글 등 동아시아 문자 2칸 폭 반영
_SUMMARY_STATS_FMT = "{pass_:>4}  {fail:>4}  {skip:>4}  {rate:>7}"


def _display_width(text: str) -> int:
    """터미널 표시 폭 (CJK·전각 문자는 2칸)."""
    w = 0
    for ch in text:
        if unicodedata.east_asian_width(ch) in ("F", "W"):
            w += 2
        else:
            w += 1
    return w


def _pad_display(text: str, width: int) -> str:
    return text + " " * max(0, width - _display_width(text))


def _summary_label_col(text: str) -> str:
    return _SUMMARY_INDENT + _pad_display(text, _SUMMARY_LABEL_DISPLAY_W)


def _summary_stats_col(stat: dict | None = None) -> str:
    if stat is None:
        return _SUMMARY_STATS_FMT.format(pass_="PASS", fail="FAIL", skip="SKIP", rate="Pass율")
    pr = stat.get("pass_rate")
    pr_str = f"{pr * 100:.1f}%" if pr is not None else "  N/A"
    return _SUMMARY_STATS_FMT.format(
        pass_=stat["pass"],
        fail=stat["fail"],
        skip=stat["skip"],
        rate=pr_str,
    )


def _summary_row(label: str, stat: dict | None) -> str:
    gap = "  "
    return _summary_label_col(label) + gap + _summary_stats_col(stat)


def _summary_separator() -> str:
    inner = _SUMMARY_LABEL_DISPLAY_W + 2 + len(_summary_stats_col())
    return _SUMMARY_INDENT + "-" * inner


def _print_summary(filename: str, consistency: dict, judge: dict | None) -> None:
    """콘솔에 평가 요약 테이블 출력 (지표당 한글 1줄 + 영문 1줄)."""
    total = consistency.get("total", 0)
    print(f"\n{'='*62}")
    print(f"  파일: {filename}  (총 클레임: {total}개)")
    print(f"{'='*62}")
    print(_summary_row("검사 항목", None))
    print(_summary_separator())

    all_summaries: dict[str, dict] = {}
    all_summaries.update(consistency.get("summary", {}))
    if judge:
        all_summaries.update(judge.get("summary", {}))

    items = list(all_summaries.items())
    for i, (key, stat) in enumerate(items):
        ko = format_metric_label(key, lang="ko")
        en = format_metric_label(key, lang="en")
        print(_summary_row(ko, stat))
        print(f"{_SUMMARY_INDENT}{en}")
        if i < len(items) - 1:
            print(_summary_separator())

    print(f"{'='*62}")


def _collect_failures(row: dict, check_keys: list[str]) -> list[str]:
    failures = []
    for key in check_keys:
        r = row.get(key, {})
        if r.get("pass") is False:
            reason = r.get("reason") or r.get("verdict", "FAIL")
            broken = r.get("broken_at")
            suffix = f" ({broken})" if broken else ""
            label = format_metric_label(key)
            failures.append(f"  [{label}]{suffix} {reason}")
    return failures


def _print_fail_details(consistency: dict, judge: dict | None) -> None:
    """FAIL 케이스를 상세 출력한다."""
    details = consistency.get("details", [])
    judge_details = {d["sent_id"]: d for d in (judge or {}).get("details", [])}

    has_fail = False
    for row in details:
        sent_id = row.get("sent_id", "?")
        failures = _collect_failures(row, CONSISTENCY_FAIL_KEYS)

        jd = judge_details.get(sent_id)
        if jd:
            failures.extend(_collect_failures(jd, JUDGE_FAIL_KEYS))

        if failures:
            has_fail = True
            print(f"\n[FAIL] {sent_id}: {row.get('claim_text', '')[:60]}")
            for f in failures:
                print(f)

    if not has_fail:
        print("\n  모든 검사 통과 — FAIL 케이스 없음")


# ── 메인 ─────────────────────────────────────────────────────────────────────

async def _run(args: argparse.Namespace) -> None:
    samples = _load_samples(args.file)
    print(f"[eval] {len(samples)}개 파일 로드 완료")

    llm_client = _build_llm_client() if args.judge else None

    all_results = []

    for filename, report in samples:
        print(f"\n[eval] 처리 중: {filename}")

        # 내적 일관성 검사
        consistency_result: dict = {}
        if not args.skip_consistency:
            print("  → 내적 일관성 검사 실행...")
            consistency_result = run_all_consistency_checks(report)

        # LLM Judge
        judge_result: dict | None = None
        if args.judge and llm_client is not None:
            from eval.evaluators.llm_judge import run_all_judge_checks
            print("  → LLM Judge 실행 (HCX-003)...")
            judge_result = await run_all_judge_checks(report, llm_client)

        # 콘솔 출력
        _print_summary(filename, consistency_result, judge_result)

        if args.verbose:
            _print_fail_details(consistency_result, judge_result)

        # 결과 수집
        all_results.append(
            {
                "filename": filename,
                "domain": report.get("domain"),
                "anchor_year": report.get("anchor_year"),
                "verdict_distribution": report.get("verdict_distribution"),
                "consistency": consistency_result,
                "judge": judge_result,
            }
        )

    # results/ 저장
    results_dir = Path(__file__).parent / "results"
    results_dir.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = results_dir / f"eval_{timestamp}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2, default=str)

    print(f"\n[eval] 결과 저장 완료: {out_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="StructVerify 파이프라인 평가 실행기")
    parser.add_argument(
        "--file", "-f",
        type=str,
        default=None,
        help="평가할 JSON 파일 경로 (기본: eval/data/samples/*.json 전체)",
    )
    parser.add_argument(
        "--judge",
        action="store_true",
        default=False,
        help="HCX LLM Judge 실행 (CLOVASTUDIO_API_KEY 필요)",
    )
    parser.add_argument(
        "--skip-consistency",
        action="store_true",
        default=False,
        help="내적 일관성 검사 건너뜀 (--judge 단독 실행 시 사용)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        default=False,
        help="FAIL 케이스 상세 출력",
    )
    args = parser.parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
