# StructVerify Evaluation

StructVerify 파이프라인의 레이어별 산출물 및 최종 팩트체크 결과를 평가하는 코드 모음.

> 위치: **`structverify/eval/`** (백엔드 레포 내). evaluators는 JSON만 읽어 독립 레포 이식을 염두에 둔다.

> **팀원 안내**: eval 모듈만 받았을 때 FastAPI·config 연동 방법 → [**플랫폼 연동 가이드 (팀원용)**](#플랫폼-연동-가이드-팀원용)

---

## 평가 목적

StructVerify는 뉴스·보고서 등 비정형 텍스트에서 수치 주장을 탐지하고,
KOSIS 공식 통계와 대조해 `match` / `mismatch` / `unverifiable` 판정을 내리는 파이프라인이다.

이 eval 모듈은 아래 두 가지를 측정한다.

1. **레이어별 산출물 품질** — 각 단계 출력이 입력·중간 산출물과 논리적으로 일치하는가 (골든셋 불필요)
2. **최종 판정 정확도** — 파이프라인이 올바른 verdict를 내리는가 (골든셋 필요, Phase 2)

**주의**: Phase 1만으로는 “팩트체크 정확도 X%”를 주장할 수 없다. 파이프라인 **헬스·병목 진단**용이다.

---

## 폴더 구조

```
structverify/eval/
  README.md
  export.py              # VerificationReport → eval 입력 JSON (공통)
  run_eval.py            # 실행 진입점, 콘솔 요약 표 출력
  data/samples/          # (선택) 파이프라인 산출 JSON
  evaluators/
    common.py            # 집계, 시점 정규화, METRIC_LABELS(한·영 표시명) 등
    consistency.py       # 내적 일관성 (규칙 기반)
    llm_judge.py         # HCX-as-Judge (정성)
    __init__.py
  results/               # eval_{timestamp}.json (출력)
```

---

## 입력 데이터

eval이 읽는 JSON은 **`results[]` 스키마**다. 생성 경로는 아래 **셋 중 하나** (모두 `export.report_to_eval_json()` 과 동일 형식).

### A. 로컬 스크립트

[`scripts/run_pipeline_Text.py`](../../scripts/run_pipeline_Text.py) → `test_outputs/pipeline_text_result.json`

```bash
conda activate structverify
python scripts/run_pipeline_Text.py
python structverify/eval/run_eval.py -f test_outputs/pipeline_text_result.json --verbose
```

### B. FastAPI 파이프라인 (권장 — 실서비스와 동일 코어)

[`sv_platform/pipeline_runner.py`](../../sv_platform/pipeline_runner.py) 의 `_build_response()` 가 job 결과에 **`eval_export`** 키를 포함한다.  
연동 패치·자동 eval 설정은 **「플랫폼 연동 가이드 (팀원용)」** 참고.

```bash
# POST /v1/verify → GET /v1/jobs/{id} 완료 후
jq '.result.eval_export' job.json > /tmp/eval_input.json
python structverify/eval/run_eval.py -f /tmp/eval_input.json --verbose
```

`run_eval.py`는 파일 최상위에 `eval_export`만 있어도 자동 unwrap 한다 (job `result` JSON 통째로 `-f` 가능).

### C. 공통 export 함수 (코드에서 직접)

```python
from structverify.eval.export import report_to_eval_json

report = await VerificationPipeline().run(text, "text")
eval_json = report_to_eval_json(report)
```

| API 응답 키 | 용도 |
|---|---|
| `result.claims[]` | 프론트 — evidence 경량화 (`_summarize_evidence`) |
| `result.eval_export` | eval — `results[]`, `graph_temporal`, `query_keyword` 등 풀셋 |

클레임(`results[]`) 항목 예시:

```json
{
  "sent_id": "b0001_s0000",
  "claim_text": "작년 연평균기온 14.8도...",
  "schema": { "indicator": "...", "value": 14.8, "unit": "도", "time_period": "2023" },
  "graph_temporal": { "expression": "작년", "resolved": "2023", "basis": "..." },
  "evidence": {
    "stat_table_id": "DT_...",
    "source_name": "...",
    "official_value": 13.7,
    "unit": "℃",
    "time_period": "2023",
    "query_keyword": "연평균기온 전체",
    "category_path": "..."
  },
  "verdict": "match",
  "explanation": "..."
}
```

| 필드 | 설명 |
|---|---|
| `graph_temporal` | L4.5 문서 시간 그래프 → 클레임 traverse 결과. `null`이면 **Temporal 3-Way**는 SKIP |
| `evidence.query_keyword` | L7 KOSIS 검색 키워드 — `export._evidence_dict()` 에서 `provenance.query_used` flatten |
| `evidence.category_path` | L7 KOSIS 카탈로그 분류 — `Evidence.category_path` |

`query_keyword` / `category_path` / `graph_temporal` 은 **파이프라인 코어가 아니라 `export.py`에서 JSON으로 flatten** 한다. L7 `Evidence` 객체에는 이미 존재한다.

---

## 실행 방법

프로젝트 루트 `.env`의 `CLOVASTUDIO_API_KEY` / `NCP_API_KEY`를 자동 로드한다 (`--judge` 시 필요).

```bash
# Phase 1: 내적 일관성만 (API 키 불필요)
python structverify/eval/run_eval.py -f test_outputs/pipeline_text_result.json --verbose

# Phase 1 + LLM Judge (HCX API 필요)
python structverify/eval/run_eval.py -f test_outputs/pipeline_text_result.json --judge --verbose

# FastAPI job result (eval_export unwrap)
python structverify/eval/run_eval.py -f job_result.json --verbose

# samples/ 에 JSON 넣었을 때 (-f 생략)
python structverify/eval/run_eval.py --verbose
```

| 옵션 | 설명 |
|---|---|
| `-f`, `--file` | eval JSON 또는 job `result` (eval_export 포함) 경로 |
| `--judge` | HCX LLM Judge 실행 |
| `--skip-consistency` | 내적 일관성 검사 생략 |
| `-v`, `--verbose` | FAIL/SKIP 케이스 상세 출력 |

결과는 `structverify/eval/results/eval_{timestamp}.json`에 저장된다.

### 파이프라인에서 직접 호출 (`run_eval()`)

DB·파일 없이 **메모리의 `report`를 인자로** eval 실행:

```python
from structverify.eval.run_eval import run_eval
await run_eval(report, label="job-123", verbose=True)
```

플랫폼 연동(`config.py`, `pipeline_runner.py` 패치, env, job API) 상세는 아래 **「플랫폼 연동 가이드 (팀원용)」** 섹션 참고.

### 콘솔 요약 표 (`run_eval.py`)

지표마다 아래 형식으로 출력한다 (`_print_summary`).

| 규칙 | 설명 |
|---|---|
| 2줄 라벨 | 1줄: **한글명** + PASS/FAIL/SKIP/Pass율 · 2줄: **영문명**만 |
| 구분선 | 헤더 직후, 지표 블록 **사이**에 `---` (마지막 지표 뒤에는 없음) |
| 컬럼 정렬 | 한글·전각 문자는 터미널 2칸 폭으로 계산 (`unicodedata.east_asian_width`) |
| Pass율 | `pass / (pass + fail)` — SKIP만 있으면 `N/A` |

표시명 정의: `structverify/eval/evaluators/common.py` → `METRIC_LABELS`, `format_metric_label(key, lang="ko"|"en")`

```
==============================================================
  파일: pipeline_text_result.json  (총 클레임: 2개)
==============================================================
  검사 항목                       PASS  FAIL  SKIP    Pass율
  ---------------------------------------------------------
  스키마 수치 추적성                 0     0     2      N/A
  Value Traceability
  ---------------------------------------------------------
  스키마 단위 추적성                 1     1     0    50.0%
  Unit Traceability
  ---------------------------------------------------------
  시계열 3-Way 일관성                0     0     2      N/A
  Temporal 3-Way (graph↔schema↔evidence)
  ---------------------------------------------------------
  스키마 완전성                      2     0     0   100.0%
  Schema Completeness
  ---------------------------------------------------------
  판정-차이율 일관성                 0     0     2      N/A
  Verdict-Diff Consistency
  ---------------------------------------------------------
  증거 관련성 (규칙)                 1     0     1   100.0%
  Evidence Relevance (rule)
==============================================================
```

`--judge` 실행 시 동일 표에 Judge 지표 3개가 이어서 출력된다.

`--verbose` FAIL 로그는 `[한글명 · 영문명] 사유` 한 줄 형식이다 (요약 표와 별도).

---

## 지표 이름 (한글 · 영문)

JSON 결과 파일의 키(`value_traceability` 등)와 아래 표가 대응한다. 콘솔 한·영 표시는 `METRIC_LABELS` 단일 소스를 따른다.

### Phase 1 — 내적 일관성

| 키 | 한글명 | 영문명 |
|---|---|---|
| `value_traceability` | 스키마 수치 추적성 | Value Traceability |
| `unit_traceability` | 스키마 단위 추적성 | Unit Traceability |
| `temporal_3way_consistency` | 시계열 3-Way 일관성 | Temporal 3-Way (graph↔schema↔evidence) |
| `schema_completeness` | 스키마 완전성 | Schema Completeness |
| `verdict_diff_consistency` | 판정-차이율 일관성 | Verdict-Diff Consistency |
| `evidence_relevance` | 증거 관련성 (규칙) | Evidence Relevance (rule) |

### Phase 1 — LLM Judge (`--judge`)

| 키 | 한글명 | 영문명 |
|---|---|---|
| `indicator_judge` | 지표 의미 판정 (HCX) | Indicator Judge (HCX) |
| `explanation_judge` | 설명 품질 판정 (HCX) | Explanation Judge (HCX) |
| `evidence_relevance_judge` | 증거 적합성 판정 (HCX) | Evidence Relevance Judge (HCX) |

### Phase 2 — 골든셋 (미구현)

| 키 | 한글명 | 영문명 |
|---|---|---|
| `verdict_macro_f1` | 판정 Macro F1 | Verdict Macro F1 |
| `mismatch_recall` | 오보 탐지율 (Mismatch Recall) | Mismatch Recall |
| `schema_field_exact_match` | 스키마 필드 정확도 | Schema Field Exact Match |
| `structverify_score` | StructVerify 통합 점수 | StructVerify Score |

---

## 평가 전략 개요

| 축 | 방법 | 골든셋 |
|---|---|---|
| 즉시 가능 | 내적 일관성 검사 + LLM-as-Judge | 불필요 |
| Phase 2 | Self-Instruct 골든셋 + FEVER 스타일 F1 | 자동 생성 |
| Phase 3 | 정기 샘플 Judge 트래킹 | 선택 |

---

## 정량 평가

### 1. 내적 일관성 검사 (골든셋 불필요) — **구현됨**

**목적**: 정답을 몰라도 산출물 내부의 논리 모순·레이어 단절을 수치로 탐지한다.

구현: `structverify/eval/evaluators/consistency.py` — `CONSISTENCY_CHECKS` 레지스트리에 등록된 6개 지표.

| 한글명 | 키 | 레이어 | 계산 방법 | FAIL 시 참고 |
|---|---|---|---|---|
| 스키마 수치 추적성 | `value_traceability` | L5 | `schema.value`가 원문 수치 목록에 있는지 | value null → SKIP |
| 스키마 단위 추적성 | `unit_traceability` | L5 | `schema.unit`이 원문에 포함되는지 (℃/도 변환은 오탐 가능) | — |
| **시계열 3-Way 일관성** | `temporal_3way_consistency` | L4.5↔L5↔L7 | 정규화 후 `graph.resolved` = `schema.time_period` = `evidence.time_period` | `broken_at` |
| 스키마 완전성 | `schema_completeness` | L5 | 필수 4필드(value/unit/indicator/time_period) 중 ≥75% 충족 | pass_rate만 참고 (verbose FAIL 제외) |
| 판정-차이율 일관성 | `verdict_diff_consistency` | L8 | verdict와 diff_pct가 verifier 규칙(≤10% match 등)과 일치 | value/evidence 없으면 SKIP |
| 증거 관련성 (규칙) | `evidence_relevance` | L7 | `schema.indicator` 토큰이 `evidence.query_keyword`에 포함 | evidence 없으면 SKIP |

**시점 정규화** (`common.normalize_time_period`):

- KOSIS `YYYYMM` (예: `202301`) → `2023`으로 축약해 schema·evidence와 비교
- 와일드카드(`****`) → 비교 불가(SKIP)

**Temporal 3-Way 해석**:

| 결과 | 의미 |
|---|---|
| **PASS (3-way)** | graph = schema = evidence 시점 일치 |
| **PASS (2-way)** | graph = schema 일치, evidence 없음 |
| **FAIL** `broken_at=L5_schema` | graph 해소 ≠ schema `time_period` |
| **FAIL** `broken_at=L7_evidence` | graph·schema 일치, evidence `time_period`만 다름 |
| **SKIP** | `graph_temporal.resolved` 없음 — 상대 시점(작년 등) 문장인데 그래프↔클레임 미연결 의심 |

> **제거됨 (2026-05)**: *Temporal Traceability* (원문 문자열에 `time_period` 포함 여부 / graph↔schema 2-way만).  
> 3-Way와 중복되고, `작년`→`2023` 같은 정상 변환에서 오탐 FAIL이 많아 **시계열 지표는 3-Way만** 사용한다.

**선택 이유**: 배포 직후 골든셋 없이 병목 레이어를 빠르게 찾을 수 있다. 시계열 품질은 graph·schema·KOSIS 증거가 한 줄로 맞는지 **3-Way**로 본다.

> **미구현**: L3 `is_check_worthy` ↔ `score` 모순 검사 (입력 JSON에 해당 필드 미포함)

---

### 2. FEVER 스타일 통합 평가 (골든셋 필요) — **미구현**

**목적**: 레이어별 정확도와 파이프라인 전체 정확도를 함께 측정한다.  
FEVER(Thorne et al., NAACL 2018) 평가 구조를 참고한다.

| 한글명 | 키 | 측정 대상 | 계산 방법 |
|---|---|---|---|
| 판정 Macro F1 | `verdict_macro_f1` | 최종 판정 | match/mismatch/unverifiable 3-class F1 |
| 오보 탐지율 | `mismatch_recall` | 최종 판정 | 실제 오보 중 mismatch로 판정된 비율 (**핵심 지표**) |
| 스키마 필드 정확도 | `schema_field_exact_match` | L5 | value/unit/time_period 필드별 정확도 |
| StructVerify 통합 점수 | `structverify_score` | 통합 | (Schema 정확) AND (Verdict 정확) → 둘 다 맞아야 1점 |

**선택 이유**: 증거 검색이 우연히 맞고 판정만 틀린 케이스를 걸러내기 위해 레이어 점수와 통합 점수를 분리한다.

---

### 3. 골든셋 자동 생성 (Self-Instruct) — **미구현**

**목적**: KOSIS 실제 수치 기반으로 match/mismatch/unverifiable 문장을 합성하고 정답 레이블을 포함한 평가셋을 자동 구축한다.

```
KOSIS 실제 수치 → LLM 문장 3종 합성 → 파이프라인 실행 → Verdict F1
```

RAGAS(Es et al., 2023) testset generation 방식을 통계 도메인에 맞게 변형할 예정이다.

---

## 정성 평가

### LLM-as-Judge (HCX) — **구현됨** (`--judge`)

**목적**: 규칙으로 측정 불가능한 **의미 영역**을 LLM 심판으로 평가한다.

| 한글명 | 키 | 레이어 | 판단 방식 | 구현 |
|---|---|---|---|---|
| 지표 의미 판정 (HCX) | `indicator_judge` | L5 | PASS/FAIL + 근거 먼저 서술 | ✅ |
| 설명 품질 판정 (HCX) | `explanation_judge` | L9 | 규칙 환각 검사 → 의심 시만 LLM | ✅ |
| 증거 적합성 판정 (HCX) | `evidence_relevance_judge` | L7 | indicator vs `source_name` PASS/FAIL | ✅ |
| 클레임 탐지 품질 | — | L3 | — | ❌ (골든셋 필요) |

**평가 모델**: `HCX-003` (`run_eval.py` → `model_tier="heavy"`)

**편향 완화**:

- Chain-of-Thought: 근거 먼저, 마지막 줄에 PASS/FAIL만
- 이진 판단 (1~5점 척도 지양)
- 프롬프트에 “우리 시스템 산출물” 명시하지 않음
- 설명 환각: 숫자 추출 규칙 1차 → 통과 시 LLM 호출 생략

**한계**: 생성·평가 모델이 동일 계열(HCX)이면 Self-Enhancement Bias 가능. 외부 LLM 사용 시 교차 검증 추가 예정.

---

## BLEU / ROUGE를 쓰지 않는 이유

BLEU/ROUGE는 정답 텍스트가 하나로 고정된 번역·요약 태스크용이다.  
설명(L9)은 동일 의미를 다양한 표현으로 쓸 수 있어 단어 겹침 점수가 신뢰도가 낮다. 설명 품질은 LLM-as-Judge로 대체한다.

---

## 평가 실행 순서 (권장)

```
Phase 1 (지금)
  VerificationPipeline.run()  (스크립트 또는 FastAPI — 동일 코어)
  → report_to_eval_json()  또는  job.result.eval_export
  → structverify/eval/run_eval.py [--verbose] [--judge]
  → Temporal 3-Way, Evidence Relevance 등으로 병목 진단
     (graph_temporal null, schema.value null, query_keyword 오매핑 등)

Phase 2 (다음)
  Self-Instruct 골든셋 자동 구축
  → Verdict F1, Mismatch Recall, StructVerify Score

Phase 3 (안정화 후)
  주 1회 샘플 30건 Judge 트래킹
  → indicator·설명·KOSIS 테이블 품질 추이
```

---

## 플랫폼 연동 가이드 (팀원용)

> **배경**: `structverify/eval/` 모듈은 eval 브랜치·별도 PR로 관리될 수 있고, **메인 브랜치에는 아직 반영되지 않았을 수 있다.**  
> eval만 checkout 해도 동작하려면 아래 **`sv_platform/` 쪽 패치**를 팀원이 직접 적용해야 한다.  
> 이 섹션이 그 “어디에 무엇을 넣는지” 단일 문서다.

### 연동 후 데이터 흐름

```
VerificationPipeline.run(text, source_type)
  → report (VerificationReport 객체)
  → _build_response(report)
       ├─ claims[]          … 프론트용 (evidence 경량화)
       ├─ eval_export       … eval 입력 JSON (report_to_eval_json)
       └─ eval_run          … (옵션) run_eval() 결과
  → job.result / API 응답
```

| 키 | 생성 시점 | 용도 |
|---|---|---|
| `eval_export` | verify 직후 항상 | eval **입력** — 파일/CLI/`run_eval(dict)` |
| `eval_run` | `PIPELINE__RUN_EVAL_AFTER_VERIFY=true` 일 때만 | eval **출력** — PASS/FAIL 집계 |

`eval_export` ≠ eval 결과. `eval_run.consistency` / `eval_run.judge` 가 실제 평가 결과다.

---

### 1. `sv_platform/config.py` — PipelineConfig 필드 추가

`PipelineConfig` 클래스에 아래 3필드를 넣는다 (기본값 `False` → 기존 동작 unchanged).

```python
class PipelineConfig(BaseModel):
    """VerificationPipeline 행동 옵션."""
    use_memory: bool = True
    domain_classify: bool = True
    multi_source_fallback: bool = True
    # ── eval 연동 (structverify/eval/) ──
    run_eval_after_verify: bool = False   # verify 직후 run_eval() 실행
    run_eval_judge: bool = False          # 위가 true일 때 LLM Judge 포함
    run_eval_verbose: bool = False        # FAIL/SKIP 상세 로그
```

Pydantic Settings 중첩 override (`env_nested_delimiter="__"`) 덕분에 `.env`에서:

```env
# 내적 일관성만 (HCX API 불필요)
PIPELINE__RUN_EVAL_AFTER_VERIFY=true
PIPELINE__RUN_EVAL_VERBOSE=true

# LLM Judge까지 (CLOVASTUDIO_API_KEY / NCP_API_KEY 필요)
PIPELINE__RUN_EVAL_AFTER_VERIFY=true
PIPELINE__RUN_EVAL_JUDGE=true
PIPELINE__RUN_EVAL_VERBOSE=true
```

코드에서 확인:

```python
from sv_platform.config import settings

settings.pipeline.run_eval_after_verify  # bool
settings.pipeline.run_eval_judge
settings.pipeline.run_eval_verbose
```

---

### 2. `sv_platform/pipeline_runner.py` — 함수 구성

연동에 필요한 함수는 **2개** (+ verify 진입점 2곳에 hook 1줄).

#### 2-A. `_build_response()` — `eval_export` 포함 (필수)

verify 결과 dict를 만들 때 **`report_to_eval_json(report)`** 로 eval 입력을 같이 실어 보낸다.  
프론트용 `claims[]` evidence는 `_summarize_evidence()`로 경량화 — eval용 필드(`query_keyword`, `graph_temporal` 등)는 `eval_export`에만 있다.

```python
def _build_response(report: Any) -> dict[str, Any]:
    from structverify.eval.export import report_to_eval_json

    # ... claims[] merge, verdict 정규화, evidence 경량화 ...

    return {
        "domain": domain,
        "anchor_year": anchor_year,
        "claims": merged_claims,
        "verdict_distribution": distribution,
        "eval_export": report_to_eval_json(report),  # ← eval 입력 (필수)
    }
```

**`eval_export` 없이** eval만 돌리면 `query_keyword`·`graph_temporal`이 빠져 Temporal 3-Way / Evidence Relevance가 대량 SKIP 된다.

#### 2-B. `_maybe_run_eval()` — 옵션 자동 실행 (선택)

verify 본문과 eval을 분리하는 얇은 헬퍼. eval 실패해도 verify 응답은 그대로 두려고 try/except로 감싼다.

```python
async def _maybe_run_eval(result: dict[str, Any], report: Any, *, label: str) -> None:
    """PIPELINE__RUN_EVAL_AFTER_VERIFY=true 일 때 run_eval(report) 실행."""
    if not settings.pipeline.run_eval_after_verify:
        return
    try:
        from structverify.eval.run_eval import run_eval

        result["eval_run"] = await run_eval(
            report,
            label=label,
            judge=settings.pipeline.run_eval_judge,
            verbose=settings.pipeline.run_eval_verbose,
        )
    except Exception as e:
        logger.exception("run_eval failed (%s)", label)
        result["eval_run"] = {"label": label, "error": str(e)}
```

- **`report` 객체**를 넘긴다 (dict/file 경유 불필요). `run_eval()` 내부에서 `report_to_eval_json()` 호출.
- `run_eval` import는 **함수 안** (lazy) — eval 플래그 꺼져 있으면 eval 모듈 로드 안 함.
- 반환 dict는 `result["eval_run"]`에 merge → DB `jobs.result` JSONB에 저장됨.

#### 2-C. verify 진입점 — hook 1줄씩

`_build_response()` 직후, return/commit 전에 호출:

```python
# run_verification() — 동기 API
report = await pipeline.run(source_data, source_type)
result = _build_response(report)
await _maybe_run_eval(result, report, label="sync")
return result

# run_verification_background() — 백그라운드 job
report = await pipeline.run(source_data, source_type)
result = _build_response(report)
await _maybe_run_eval(result, report, label=str(job_id))
# job.result = result  →  eval_run 포함
```

---

### 3. `structverify/eval/run_eval.py` — 공개 API

플랫폼·스크립트 모두 이 함수 하나로 통일:

```python
from structverify.eval.run_eval import run_eval

entry = await run_eval(
    source,              # VerificationReport | dict (eval_export / job.result)
    label="job-uuid",
    judge=False,         # True → HCX Judge (--judge)
    skip_consistency=False,
    verbose=False,
    save=True,           # structverify/eval/results/eval_{ts}.json 저장
)
```

**입력 정규화** (`coerce_eval_report` / `normalize_eval_input` — 호출자는 신경 쓸 필요 없음):

| `source` 형태 | 처리 |
|---|---|
| `VerificationReport` | `report_to_eval_json(source)` |
| `{ "results": [...] }` | 그대로 |
| `{ "eval_export": { "results": [...] } }` | unwrap |
| `{ "result": { ... } }` (job wrapper) | 재귀 unwrap |

**반환 `entry` 구조**:

```json
{
  "label": "550e8400-e29b-41d4-a716-446655440000",
  "saved_path": "structverify/eval/results/eval_20260519_151416.json",
  "domain": "climate",
  "anchor_year": 2024,
  "verdict_distribution": { "match": 1, "mismatch": 0, ... },
  "consistency": { "summary": { ... }, "details": [ ... ] },
  "judge": null
}
```

eval 실패 시 `eval_run`: `{ "label": "...", "error": "..." }`.

---

### 4. 사용 시나리오별 체크리스트

#### A. eval만 로컬에서 (플랫폼 패치 **불필요**)

```bash
python scripts/run_pipeline_Text.py
python structverify/eval/run_eval.py -f test_outputs/pipeline_text_result.json --verbose
```

#### B. 플랫폼 패치 + 수동 eval (자동 eval **끔**)

1. `_build_response` + `eval_export`만 적용  
2. verify 후 job JSON에서:

```bash
curl -s .../v1/jobs/{id} | jq '.result' > job_result.json
python structverify/eval/run_eval.py -f job_result.json --verbose
```

`run_eval.py`는 `eval_export` 자동 unwrap — `job_result.json` 통째로 `-f` 가능.

#### C. 플랫폼 패치 + 자동 eval (**권장**)

1. config 3필드 + `_maybe_run_eval` + hook 적용  
2. `.env`에 `PIPELINE__RUN_EVAL_AFTER_VERIFY=true`  
3. verify 완료 후 `GET /v1/jobs/{id}` → `result.eval_run` 확인  
4. 파일 저장은 `eval_run.saved_path` 또는 `structverify/eval/results/`

#### D. 로컬 스크립트에서 파이프라인+eval 한 번에

[`scripts/run_pipeline_Text.py`](../../scripts/run_pipeline_Text.py) 참고 (메인 미반영 시 동일 패턴 복사):

```python
from structverify.eval.run_eval import run_eval

report = await pipeline.run(text, "text")
# ... JSON 저장 ...
if args.eval:
    await run_eval(report, label="pipeline_text_result.json", judge=args.eval_judge, verbose=args.eval_verbose)
```

```bash
python scripts/run_pipeline_Text.py --eval --eval-verbose
python scripts/run_pipeline_Text.py --eval --eval-judge --eval-verbose
```

---

### 5. 연동 최소 diff 요약

| 파일 | 변경 | 필수? |
|---|---|---|
| `structverify/eval/` | eval 모듈 전체 | ✅ (이 README 포함) |
| `sv_platform/config.py` | `PipelineConfig` 3필드 | 자동 eval 시 |
| `sv_platform/pipeline_runner.py` | `_build_response` → `eval_export` | ✅ (eval 풀 필드) |
| `sv_platform/pipeline_runner.py` | `_maybe_run_eval` + hook 2곳 | 자동 eval 시 |
| `scripts/run_pipeline_Text.py` | `--eval` 플래그 | 로컬 편의 (선택) |
| `.env` | `PIPELINE__RUN_EVAL_*` | 자동 eval 시 |

**플랫폼 패치 없이** eval 모듈만으로도 CLI + JSON 파일 평가는 가능하다.  
**FastAPI job에서 바로 `eval_run`을 보고 싶을 때**만 config + pipeline_runner 패치가 필요하다.

---

## 이식 · 연동 (evaluators 독립 레포)

- `consistency.py` / `common.py`: JSON(`dict`)만 읽음 → **structverify import 불필요**
- `export.py`: `VerificationReport` → eval JSON — **백엔드에만 두거나** eval 레포로 복사 시 `structverify` 의존
- `llm_judge.py`: `structverify.utils.llm_client.LLMClient` 사용 → 이식 시 `httpx` 직접 호출로 교체

### FastAPI ↔ eval (요약)

| 계층 | 역할 |
|---|---|
| `VerificationPipeline.run()` | 검증 코어 (스크립트·FastAPI 동일) |
| `export.report_to_eval_json(report)` | `results[]`, `graph_temporal`, `query_keyword`, `category_path` flatten |
| `_build_response()` | `claims[]` (프론트) + **`eval_export`** (eval) |
| `_maybe_run_eval()` | (옵션) `run_eval(report)` → **`eval_run`** |

상세 패치 방법: **「플랫폼 연동 가이드 (팀원용)」** 섹션.

`result.claims[].evidence` 는 여전히 경량화(`_summarize_evidence`) — 프론트용.  
eval은 **`result.eval_export`** 또는 export JSON 파일만 사용한다.

**없을 때 eval 동작** (`eval_export` 미사용·구 export JSON):

- `query_keyword` 없음 → `evidence_relevance` **SKIP**
- `graph_temporal` null → **Temporal 3-Way** **SKIP**

---

## 참고 문헌

- Thorne et al. (2018). *FEVER: a large-scale dataset for Fact Extraction and VERification*. NAACL.
- Es et al. (2023). *RAGAS: Automated Evaluation of Retrieval Augmented Generation*. arXiv.
- Wang et al. (2022). *Self-Instruct: Aligning Language Models with Self-Generated Instructions*. arXiv.
