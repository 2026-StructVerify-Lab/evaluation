# StructVerify Evaluation
StructVerify 파이프라인의 레이어별 산출물 및 최종 팩트체크 결과를 평가하는 코드 모음.
---
## 평가 목적
StructVerify는 뉴스·보고서 등 비정형 텍스트에서 수치 주장을 탐지하고,
KOSIS 공식 통계와 대조해 match / mismatch / unverifiable 판정을 내리는 파이프라인이다.
이 레포는 아래 두 가지를 측정한다.
1. **최종 판정 정확도** — 파이프라인 전체가 올바른 verdict를 내리는가
2. **레이어별 산출물 품질** — 각 단계의 출력이 입력과 논리적으로 일치하는가
---
## 평가 전략 개요
정답 레이블(골든셋) 유무에 따라 두 축으로 나눈다.
골든셋 없이 즉시 측정 가능 → 내적 일관성 검사 + LLM-as-Judge 골든셋 구축 후 측정 가능 → 전통 분류 지표 (F1, Precision, Recall)

---
## 정량 평가
### 1. 내적 일관성 검사 (골든셋 불필요)
**목적**: 정답을 몰라도 산출물 내부의 논리 모순을 수치로 탐지한다.
| 지표 | 측정 레이어 | 계산 방법 |
|---|---|---|
| Schema Value Traceability | L5 스키마 유도 | 추출된 value가 원문 수치 목록에 존재하는 비율 |
| Schema Unit Traceability | L5 스키마 유도 | 추출된 unit이 원문에 존재하는 비율 |
| Temporal Traceability | L5 스키마 유도 | time_period가 temporal_provenance 해소 결과와 일치하는 비율 |
| Schema Completeness | L5 스키마 유도 | 필수 필드(value/unit/indicator/time_period) 중 null이 아닌 비율 |
| Verdict-Diff Consistency | L8 검증 | verdict와 diff_pct가 규칙(≤10%→match 등)과 일치하는 비율 |
| Check-Worthy Score Consistency | L3 클레임 탐지 | is_check_worthy=true인데 score<0.5인 모순 케이스 비율 |
**선택 이유**: 골든셋 없이 파이프라인 배포 직후부터 측정 가능하며, 어느 레이어가 병목인지 빠르게 진단할 수 있다.
---
### 2. FEVER 스타일 통합 평가 (골든셋 필요)
**목적**: 레이어별 정확도와 파이프라인 전체 정확도를 함께 측정한다.
FEVER(Thorne et al., NAACL 2018)의 평가 구조를 참고했다.
| 지표 | 측정 대상 | 계산 방법 |
|---|---|---|
| Verdict Macro F1 | 최종 판정 | match/mismatch/unverifiable 3-class F1 |
| Mismatch Recall | 최종 판정 | 실제 오보 중 mismatch로 판정된 비율 (핵심 지표) |
| Schema Field Exact Match | L5 스키마 유도 | value/unit/time_period 필드별 정확도 |
| StructVerify Score | 통합 | Schema 정확 AND Verdict 정확 → 둘 다 맞아야 1점 |
**선택 이유**: 증거 검색이 우연히 맞고 판정이 틀린 케이스를 걸러내기 위해 레이어별 점수와 통합 점수를 분리한다. `Mismatch Recall`을 핵심 지표로 두는 이유는 오보를 맞다고 판정하는 False Negative가 서비스 신뢰를 가장 크게 훼손하기 때문이다.
---
### 3. 골든셋 자동 생성 (Self-Instruct)
**목적**: 사람이 직접 레이블링하지 않고 KOSIS 실제 수치 기반으로 평가용 데이터셋을 자동 생성한다.
KOSIS 실제 수치 → LLM으로 match/mismatch/unverifiable 문장 3종 합성 → 정답 레이블이 포함된 평가 데이터셋 자동 구축

**선택 이유**: 통계 도메인 특성상 사람이 직접 어노테이션하려면 전문 지식이 필요하다. KOSIS 수치 자체를 정답 소스로 삼아 생성 비용을 낮춘다. RAGAS(Es et al., 2023)의 testset generation 방식을 우리 도메인에 맞게 변형한 것이다.
---
## 정성 평가
### LLM-as-Judge (골든셋 불필요)
**목적**: 규칙으로 측정 불가능한 의미 영역을 강력한 외부 LLM이 평가한다.
| 평가 항목 | 측정 레이어 | 판단 기준 |
|---|---|---|
| Indicator 의미 정확성 | L5 스키마 유도 | 추출된 indicator가 원문 통계 지표의 의미를 정확히 표현하는가 |
| 설명문 논리 충실성 | L9 설명 생성 | 설명이 verdict 근거를 논리적으로 서술하는가 |
| 설명문 환각율 | L9 설명 생성 | 설명에 원문/증거에 없는 수치가 포함되는가 |
| 클레임 탐지 품질 | L3 클레임 탐지 | 탐지된 클레임이 실제로 수치 기반 사실 주장인가 |

**평가 모델**: HCX-007
**편향 완화 설계**:
- 평가 기준을 점수별로 명확히 정의한 루브릭 사용
- 점수 전에 근거를 먼저 서술하도록 Chain-of-Thought 강제
- 복잡한 항목은 1~5점 척도 대신 PASS/FAIL 이진 판단으로 단순화
- 평가 프롬프트에서 "우리 시스템 산출물"임을 명시하지 않음
**한계**: 생성 모델과 평가 모델이 동일(HCX)하여 Self-Enhancement Bias 가능성이 있다.
---
## BLEU / ROUGE를 쓰지 않는 이유
BLEU와 ROUGE는 번역·요약처럼 정답 텍스트가 하나로 고정된 태스크용 지표다.
설명 생성(L9)은 동일한 판정 근거를 다양한 표현으로 서술할 수 있어 단어 겹침 기반 점수가 의미를 반영하지 못한다.
설명 품질 평가는 LLM-as-Judge로 대체한다.
---
## 평가 실행 순서 (권장)
Phase 1 (즉시) 내적 일관성 검사 → 파이프라인 병목 레이어 진단

Phase 2 (골든셋 생성 후) Self-Instruct로 평가셋 자동 구축 → FEVER 스타일 Verdict F1 + StructVerify Score 측정

Phase 3 (안정화 후) LLM-as-Judge 정기 샘플 평가 (주 1회 30개) → indicator 품질, 설명 품질 트래킹

---
## 참고 문헌
- Thorne et al. (2018). *FEVER: a large-scale dataset for Fact Extraction and VERification*. NAACL.
- Es et al. (2023). *RAGAS: Automated Evaluation of Retrieval Augmented Generation*. arXiv.
- Wang et al. (2022). *Self-Instruct: Aligning Language Models with Self-Generated Instructions*. a