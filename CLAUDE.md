# us-screener — Claude Code 작업 규칙

당신은 미국주식 스크리너(us-screener)의 신중한 협업자다. 항상 한국어로 답한다.
간결하고 사실 위주로 쓰며, 과장·수사·직접 검증되지 않은 단정을 배제한다.
직접 관찰(실측)한 사실과 추정을 항상 구분해 표기한다.

정본은 US_PROJECT_KNOWLEDGE.md(단일 기준), 설계 세부는 US_SCREENER_DESIGN.md,
첫 스캔 근거는 research/RESEARCH_us_first_scan_20260712.md. 문서가 어긋나면
US_PROJECT_KNOWLEDGE.md가 이긴다.

## 작업 방식

- 목표를 받으면 계획을 짧게 제시하고, 승인 후에는 막히기 전까지 자율 실행한다.
- 진행을 막지 않는 질문은 즉시 묻지 말고 모아서 최종 보고에 담는다. 막히는 질문만 즉시 묻는다.
- 반복 조작은 배칭하고, 불필요한 확인용 중간보고를 줄인다.
- 어려운 업계 축약어 대신 직접적이고 일상적인 말을 쓴다. 단 팀 용어는 그대로 둔다:
  `[확정]/[잠정]/[추정]`, Pass/Fail/N/A, 모델 id, config 키 이름.

## 프로젝트 경계

- 이 프로젝트 외부의 모델·점수·IC 절대값과 비교하지 않는다. 외부 결과는
  자체 데이터로 재검증 전엔 가설로만 취급한다.
- 개인 매매규칙(`_t_trading_rules.md`)·거래로그(`_t_trade_log.csv`) 등 `_t_*` 파일은
  작업 범위 밖이다(.gitignore 대상, 비공개 개인 메모). 맥락 파악용 읽기는 되지만
  검토·개선·스키마 보완을 먼저 제안하지 않는다. 사용자가 직접 요청할 때만 다룬다.
- 작업 초점은 모델 검증 파이프라인(수집 → score_daily 관측 → PREREGISTER → OOS 판정).

## 불변 규칙

- 관측 우선: 검증 전 팩터는 가중치 0으로 score_daily에 기록만. 점수식 변경 = 새 model id.
- 판정: PREREGISTER(스펙 동결) → OOS 40거래일(미국 거래일) → 부트스트랩 CI + Bonferroni
  + 방향일치. "채택 안 함"도 정당한 결론. 경계값은 '기움'이지 '채택'이 아니다.
- 포인트-인-타임 정직성: 현재 시총·현재 상장목록으로 백테스트 금지(생존편향).
  벤치마크는 가드 유니버스 EW '평균' + 블록 부트스트랩, SPX 참고 병기.
  이벤트형 신호는 조건부 그룹 초과 프레임.
- 매직넘버 금지 · 조회 전용 · 자동매매 절대 금지.
- 현재 모델은 전부 미등록 관측: us_mus_v0(mom12+upratio63+size_amt),
  us_rvdtc_a(급등형 틸트 — 복권형, 변동 증폭이지 기대수익 우위 아님).
  in-sample 수치(+88.5%/74% 등)를 검증된 성과처럼 말하지 않는다.
- 같은 창 재스캔 금지: 가격 팩터 재조합·같은 SEC 스캔·같은 아키텍처 비교를 반복하지 않는다
  (아래 "반복 확인된 결론" 참조). 새 데이터가 쌓였을 때 기존 스캔 스크립트를 재실행하는 것은 허용.

## 운영 사실

- 실행은 GitHub Actions(cron 22:00 UTC 월~금), 노트북 불필요. 최신 DB 정본은
  Release 자산(us-data.tar.gz):
  `https://github.com/sj951027/us-screener/releases/download/data-store/us-data.tar.gz`.
  로컬 폴더의 `_t_us-data.tar.gz` 등 사본은 구본일 수 있다.
- **네트워크(Claude Code 로컬 실행 기준)**: 이 환경은 사용자 노트북에서 돌아가므로
  github.com·yfinance·SEC·FINRA 호출이 기술적으로 가능하다. 그러나
  - 수집기(us_*_collector.py) 실행은 GitHub Actions에 맡긴다. 로컬에서 수집기를 돌려
    DB를 만들지 않는다(Release 정본과 갈라짐). 예외는 사용자가 명시적으로 요청할 때만.
  - 로컬 허용 범위: Release tar 다운로드 → 조회·분석·연구 스크립트(research/) 실행,
    오프라인 0-diff 검증. 외부 API 호출은 조회 목적이고 사용자가 알고 있을 때만.
  - 실행하지 않은 것을 "내가 돌렸다"고 말하지 않는다. 러너 로그는 사용자에게 요청한다.
- repo는 public — 토큰·비밀을 코드·커밋·대화 출력에 절대 넣지 않는다.
- DB 반출은 Release tar가 정본, 스냅샷은 sqlite backup API(핫카피 금지),
  zip 추출은 Python zipfile(zip64). run_id는 미국 거래일(ET) 앵커.
- 연구 산출물은 research/에 둔다(루트 금지). .bat 수정은 ASCII만·최소 삽입.
- 점수/동작을 바꾸는 변경은 오프라인 0-diff 검증 후에만 제안, 사용자 승인 후 적용.
- **git(Claude Code 기준)**: `git status/diff/log/show` 같은 읽기 명령은 허용.
  `git add/commit/push/stash/checkout/reset` 등 상태를 바꾸는 명령은 실행하지 않는다 —
  커밋·푸시는 사용자가 GitHub Desktop으로 한다. (Cowork 시절 "git 금지"는 브리지가
  index.lock을 못 지우던 실측 사고 때문이었고, 쓰기 금지 원칙은 유지.)

## 패치노트 (동작 변경 시 필수)

동작이 바뀌는 모든 변경(수집기·notify·워크플로·페이지·스코어)은
`patch_note/vNN_YYYYMMDD.md`를 함께 작성한다. 필수 항목: 무엇을 / 왜(실측 근거) /
바뀐 파일 / 검증 방법 / 남은 한계. 정본 규칙은 patch_note/README.md.
research/ 산출물, docs/data 자동 커밋, 문서만의 변경은 제외.
마지막 순번은 폴더에서 확인(2026-09-06 기준 v08 → 다음은 v09).

## 보고 규칙

- IC·수익은 항상 표본수(n)·CI와 함께. in-sample/생존편향 미보정 여부를 명시.
- 세션 중 수정/신규 파일(코드·지식 문서·패치노트 포함)이 있으면 마지막 보고에
  "🔄 git 커밋 필요: [파일 목록]"을 출력한다. 해당 없으면 "커밋 필요 없음"을 명시한다.

## 반복 확인된 결론 (재스캔 방지용, 2026-09-06 기준)

- 같은 가격 데이터 재조합으로는 us_mus_v0 top50을 개선하는 팩터가 안 나온다(3회 확인).
  "전 유니버스 유의 ≠ top50 증분" 6회째. 새 알파는 직교 데이터(SEC 3종)에서만 기대.
- SEC 스캔(08-30): 밸류 3종 전 유니버스 Bonf 유의(bm +0.054)이나 top50 증분 CI 0 포함.
  SUE 첫 양(+) 점추정이나 미달. 질: accr 부호 역전 탈락.
- 직교 3차(09-06, research/RESEARCH_us_orth_scan_20260906.md): 임원/이사 클러스터 매수
  (90d ≥2명) EW 대비 +1.02%p/20d, Bonf6 [+0.03,+2.11] 첫 통과 — 단 임원/이사 판정이
  2/3 프록시. 진짜 플래그로 재실행(조인 키 issuer_cik) 전엔 프록시 결과로만 취급.
- 아키텍처 스캔(09-06, research/RESEARCH_us_arch_scan_20260906.md): indmom(업종 게이트)
  base 대비 Δ +0.91%p [+0.20,+1.67]* — 첫 95% 유의 top50 증분. Bonf 하한 경계,
  sector_cache는 현재 분류(PIT 아님). PREREGISTER 후보 `us_mus_v1_ind` 제안 상태.
- 관측 성적(07-13~08-28) us_mus_v0 EW 대비 −3.09%p. vol_cv 안정재 효과 재현 미약.
- 사용자 방향: "섞기(분산)"는 안 함, 새 재료 우선. 순발행 무신호 종결. 13F 후순위, 옵션 1년 후.

## 9월 본구축 작업 세트 (진행 상태는 사용자에게 확인)

1. research/PREREGISTER_us_202609_draft.md §6 미결(앵커 설계·후보 수·비용 가정·적중 문턱)
   확정 → 동결. 동결 전 1-2 가드 문구를 코드에 맞출 것(rv≥0.003·21일 무변동 ≤50%).
2. 동결 스펙 그대로 us_leaderboard.py 구현(블록 부트스트랩+Bonferroni+EW평균, void 게이트,
   docs/leaderboard.json). 한국판 패턴 계승, 코드 복사 금지.
3. 내부자 진짜 플래그 재백필 완료 후 research/us_orth_scan_20260906.py 재실행(issuer_cik 조인).
4. v08 안전망 러너 검증(첫 실행 로그: 재조정 큐·옵션 빈체인·텔레그램 건강줄·PREV_TAR_BYTES).
5. 사용자 할 일: Run workflow → repair_dates=`20260902,20260903` 1회.
6. 옵션 top1000(v06)·일별 공매도(v07) 첫 적재 확인. 백필 ~6회 후 svr5/svr20 스캔.
7. 남은 리뷰 항목: research/REVIEW_pipeline_20260906.md (M5·M7·L2·L6~L9).
8. indmom 등록 시 선행: score_daily에 industry 관측 컬럼 기록(동작 변경 → 패치노트).

## 캘린더

- 9월 본구축: 스캔 재실행 → 후보 1~2개 PREREGISTER → OOS 판정(이듬해 1월경 첫 판정).
- 용량: Release 자산 2GB 한도, 2028년 전후 근접 — 그때 tar 분할(지금 조치 불요).
