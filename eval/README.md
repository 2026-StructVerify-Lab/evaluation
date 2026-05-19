# StructVerify Evaluation

StructVerify 파이프라인의 레이어별 산출물 및 최종 팩트체크 결과를 평가하는 코드 모음.

> 현재는 **백엔드 레포** (`backend/eval/`) 에서 개발·실행하고, 안정화 후 **독립 eval 레포**로 이식하는 것을 전제로 한다.

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
eval/
  README.md
  run_eval.py            # 실행 진입점, 콘솔 요약 표 출력
  data/samples/          # 파이프라인 산출 JSON (입력)
  evaluators/
    common.py            # 집계, 시점 정규화, METRIC_LABELS(한·영 표시명) 등
    consistency.py       # 내적 일관성 (규칙 기반)
    llm_judge.py         # HCX-as-Judge (정성)
    __init__.py
  results/               # eval_{timestamp}.json (출력)
```

---

## 입력 데이터

[`scripts/run_pipeline_Text.py`](../scripts/run_pipeline_Text.py) 가 생성하는 JSON을 그대로 사용한다.

```bash
conda activate structverify
python scripts/run_pipeline_Text.py
cp test_outputs/pipeline_text_result.json eval/data/samples/
```

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
| `evidence.query_keyword` | L7 KOSIS 검색에 실제 사용된 키워드 (`Evidence.provenance.query_used`를 flatten) |
| `evidence.category_path` | L7 KOSIS 카탈로그 분류 경로 (`Evidence.category_path`, 도메인 가드·Judge 참고) |

---

## 실행 방법

프로젝트 루트 `.env`의 `CLOVASTUDIO_API_KEY` / `NCP_API_KEY`를 자동 로드한다 (`--judge` 시 필요).

```bash
# Phase 1: 내적 일관성만 (API 키 불필요)
python eval/run_eval.py --verbose

# Phase 1 + LLM Judge (HCX API 필요)
python eval/run_eval.py --judge --verbose

# 특정 파일만
python eval/run_eval.py -f test_outputs/pipeline_text_result.json --verbose

# Judge만 (일관성 검사 생략)
python eval/run_eval.py --judge --skip-consistency
```

| 옵션 | 설명 |
|---|---|
| `-f`, `--file` | 평가할 JSON 경로 (기본: `eval/data/samples/*.json` 전체) |
| `--judge` | HCX LLM Judge 실행 |
| `--skip-consistency` | 내적 일관성 검사 생략 |
| `-v`, `--verbose` | FAIL/SKIP 케이스 상세 출력 |

결과는 `eval/results/eval_{timestamp}.json`에 저장된다.

### 콘솔 요약 표 (`run_eval.py`)

지표마다 아래 형식으로 출력한다 (`_print_summary`).

| 규칙 | 설명 |
|---|---|
| 2줄 라벨 | 1줄: **한글명** + PASS/FAIL/SKIP/Pass율 · 2줄: **영문명**만 |
| 구분선 | 헤더 직후, 지표 블록 **사이**에 `---` (마지막 지표 뒤에는 없음) |
| 컬럼 정렬 | 한글·전각 문자는 터미널 2칸 폭으로 계산 (`unicodedata.east_asian_width`) |
| Pass율 | `pass / (pass + fail)` — SKIP만 있으면 `N/A` |

표시명 정의: `eval/evaluators/common.py` → `METRIC_LABELS`, `format_metric_label(key, lang="ko"|"en")`

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

구현: `eval/evaluators/consistency.py` — `CONSISTENCY_CHECKS` 레지스트리에 등록된 6개 지표.

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
  run_pipeline_Text.py → samples/ 복사
  → run_eval.py [--verbose] [--judge]
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

## 이식 시 주의사항

### eval 코드 (독립 레포)

- `consistency.py` / `common.py`: JSON(`dict`)만 읽음 → **독립 레포 이식 용이** (`structverify` import 없음)
- `llm_judge.py`: 현재 `structverify.utils.llm_client.LLMClient` 사용 → 이식 시 `httpx` 직접 호출로 교체
- `evaluators/` 함수 시그니처·`CONSISTENCY_CHECKS` / `JUDGE_CHECKS` 레지스트리는 유지하고 LLM 호출부만 분리할 것

### 백엔드(FastAPI) 파이프라인 → eval JSON 연동

eval은 **파이프라인이 보낸 JSON**만 읽는다. [`scripts/run_pipeline_Text.py`](../scripts/run_pipeline_Text.py) 는 eval용 필드를 이미 넣지만, **실서비스 응답** [`sv_platform/pipeline_runner.py`](../sv_platform/pipeline_runner.py) 의 `_summarize_evidence()` 는 `source_name`·`official_value` 등만 남기고 **`query_keyword` / `category_path` 를 빼고 있다.**  
FastAPI·배치·프론트용 경량화와 eval 샘플 export는 **스키마를 맞춰야** 한다.

**반드시 evidence 블록에 포함할 필드** (eval Phase 1에서 사용):

| JSON 필드 | 라이브러리 출처 | 쓰는 eval 지표 |
|---|---|---|
| `query_keyword` | `ev.provenance.query_used` | `evidence_relevance` (규칙), `evidence_relevance_judge` |
| `category_path` | `ev.category_path` | (향후 도메인·테이블 검증; Judge 프롬프트 보조) |

참고 구현 (`run_pipeline_Text.py` — `Evidence` 객체에서 flatten):

```python
"evidence": {
    "stat_table_id":  ev.stat_table_id,
    "source_name":    ev.source_name,
    "official_value": ev.official_value,
    "unit":           ev.unit,
    "time_period":    ev.time_period,
    "query_keyword":  ev.provenance.query_used if ev.provenance else None,
    "category_path":  ev.category_path,
} if ev else None,
```

API가 이미 `evidence`를 `dict`로 넘기는 경우 (`_summarize_evidence` 확장 예):

```python
KEEP_KEYS = (
    "source_name", "stat_table_id", "stat_name",
    "official_value", "unit", "time_period",
    "category_path",   # 추가
)
# provenance가 dict로 남아 있으면 flatten
prov = evidence.get("provenance")
if isinstance(prov, dict) and prov.get("query_used"):
    summary["query_keyword"] = prov["query_used"]
elif evidence.get("query_keyword"):
    summary["query_keyword"] = evidence["query_keyword"]
```

**없을 때 eval 동작**

- `query_keyword` 없음 → `evidence_relevance` **SKIP** (`provenance 미저장`)
- `category_path` 없음 → 규칙 지표는 통과 가능하나, 파이프라인 도메인 가드·Judge 맥락이 약해짐

같은 이유로 eval 연동 시 **`graph_temporal`** (`document_graph.temporal_provenance`) export 도 [`run_pipeline_Text.py`](../scripts/run_pipeline_Text.py) 와 동일하게 맞출 것. 없으면 **Temporal 3-Way** 가 전부 SKIP 된다.

**권장**: 운영 API 응답용 경량 evidence와, `eval/data/samples/` 용 **eval export** (필드 superset)를 분리하거나, `_summarize_evidence`에 eval 플래그를 두어 위 필드를 optional로 포함한다.

---

## 참고 문헌

- Thorne et al. (2018). *FEVER: a large-scale dataset for Fact Extraction and VERification*. NAACL.
- Es et al. (2023). *RAGAS: Automated Evaluation of Retrieval Augmented Generation*. arXiv.
- Wang et al. (2022). *Self-Instruct: Aligning Language Models with Self-Generated Instructions*. arXiv.
