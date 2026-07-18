# US_PROJECT_KNOWLEDGE.md — 미국판 스크리너 지식 문서

> 최종 갱신: 2026-07-12 · 이 문서가 이 repo 의 단일 기준(설계 세부는 US_SCREENER_DESIGN.md,
> 첫 스캔 근거는 research/RESEARCH_us_first_scan_20260712.md).
> **한국판(dh-q7m3k)과 완전 별개 프로젝트** — 코드·데이터·유니버스·점수·표시를 절대 섞지 않는다.
> 계승하는 것은 규율뿐이다.

## §1. 불변 규칙 (한국판 계승)

- **관측 우선**: 검증 전 팩터는 가중치 0으로 기록만 한다(score_daily). 점수식 변경 = 새 model id
  (기존 기록 소급수정 금지).
- **판정 절차**: 본구축 때 PREREGISTER(스펙 동결) → OOS 40거래일 → 부트스트랩 CI + 다중검정.
  그 전의 모든 수치는 in-sample 가설. "채택 안 함"도 정당한 결론.
- **포인트-인-타임 정직성**: 백테스트에 현재 시총·현재 상장목록 사용 금지(생존편향).
  listing_events 가 쌓이면 상폐 반영 재검증.
- **매직넘버 금지 · 조회 전용 · 자동매매 절대 금지 · 표시는 판정 기준이 아님**(테스트·관측
  도배는 유지하되 골대는 불변).

## §2. 아키텍처 (2026-07-12 완성 — 노트북 불필요)

```
GitHub Actions (cron 22:00 UTC 월~금 = 미국 마감 후, 한국 아침 07시)
  1) Release "data-store"에서 us-data.tar.gz 내려받아 이전 상태 복원
  2) 수집기 5종 실행 (씨앗→시세→지수→시총순환→FINRA공매도)
  3) us_page_data.py → docs/data/us_latest.csv 자동 커밋 (GitHub Pages 표 갱신)
                     + us_ohlcv.db score_daily 관측 적재
  4) us_notify_test.py → 텔레그램 top10 (휴장일 가드: 최신일≠오늘ET면 생략, 수동실행은 FORCE)
  5) 무결성 게이트(PRAGMA quick_check 전 DB) 통과 시에만 tar 재업로드
     + 금요일엔 us-data-weekly.tar.gz 2세대 백업
  실패 시: if:failure() 단계가 텔레그램으로 로그 링크 전송
```

- 표시: https://sj951027.github.io/us-screener/us.html (Pages, main /docs) — 검색·정렬·필터.
- Secrets: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID (repo Settings→Secrets→Actions).
  repo 는 public — 토큰을 코드·커밋에 절대 넣지 않는다.
- **최신 DB의 정본은 Release 자산**이다. 로컬 ../us-screener-data 는 2026-07-10 백필본(구본).
- cron 지연 무해: 시세 7일 창·씨앗 diff·순환수집 모두 자기치유 증분.

## §3. 데이터 인벤토리 (us-data.tar.gz 내 us-screener-data/)

| DB | 테이블 | 내용 |
|---|---|---|
| us_seed.db | listing_daily/listing_events | 전 상장목록 스냅샷+상폐·신규 diff (NASDAQ Trader) |
| | index_membership/membership_events | S&P500·NDX100 구성+편출입 (위키피디아) |
| us_ohlcv.db | daily_ohlcv | 전종목 3y 일봉 (close=비조정, adj_close=조정) 450만+행 |
| | valuation_rotate | 시총·주식수 순환 스냅샷(600/일, ~12일 한 바퀴) |
| | **score_daily** | 매일 전 유니버스 점수·순위·팩터 (model,date,symbol PK) — OOS 판정 재료 |
| us_market.db | market_daily | SPX·NDX·COMP·VIX·DXY·US10Y·USDKRW |
| us_short.db | short_interest | FINRA 격주 공매도 (73파일 백필 완료) |

## §4. 모델 상태

- **us_mus_v0** (관측 중, 등록 아님): mom12 + upratio63 + size_amt 순위합.
  가드 $5·거래대금$1M·무변동컷. 첫 스캔 in-sample: top50 h20 +88.5% 누적·적중 74%
  (23앵커·생존편향 미보정·상승장 편중 — 근거 research/ 문서).
- 핵심 교차시장 발견: **upratio63(꾸준함)** 이 한국·미국 양쪽에서 재현. lv(저변동)는 미국에서 약함.
- **vol_cv**(거래량 꾸준함): 유일한 유의 거래량 팩터. 점수 미포함 관측 컬럼 — 본구축 때
  안정재 변형(적중 74→83%)으로 별도 검증 예정.
- 기각(양 시장 재현): OBV 매집, Amihud 비유동성(역전), FIP, 단기모멘텀, 거래량 폭발,
  매수압력 프록시(역효과 기움).
- **us_rvdtc_a** (관측 중, 2026-07-18 배선): us_mus_v0 top50 중 고변동(rv63↑)+저공매도
  (FINRA dtc↓, 결제일+14일 PIT 지연) 순위합 상위 10 — '급등형 틸트'. 2차 스캔(94주간앵커,
  in-sample): day-IC h20 rv63 +0.063 CI[+0.004,+0.122]·dtc −0.041 CI[−0.082,−0.001],
  +20%/20d 급등 적중 21.1%(유니버스 3.2배) **단 −20% 급락 12.8%(2배)·평균은 top10과 무차이
  = 복권형(변동 증폭)**. 문헌 부합(short interest anomaly·vol anomaly 고변동×저공매도 롱).
  표시 us_tilt.html·기록 score_daily 뿐(가중치 0). 본구축 때 PREREGISTER 후보.

## §5. 결정 로그

- 2026-07-12: 유니버스는 S&P500 아닌 **전체 상장**(잘 오를 종목 탐색이 목적).
  실행은 GitHub Actions(무료·노트북 독립). 데이터 보관은 git 커밋이 아닌 Release 자산.
  텔레그램은 새 봇+공개 그룹, 메시지는 순위만 간결히(상세는 페이지).
  휴장일 중복알림 구멍 발견→가드. 손상 DB 업로드 방지 게이트+주간 백업. score_daily 신설.
  us_latest.csv 매일 커밋(~100MB/년 히스토리)은 본구축 때 재검토.
- 2026-07-12 (판단축): 페이지에 **섹터**(rotate 의 sector_cache — 캐시 미보유 심볼만 1회 조회,
  첫 바퀴 ~12일에 채워짐)와 **S&P500·NDX 뱃지**(us_seed index_membership) 추가. 전부 점수
  미포함 관측 컬럼. ⚠️ 한국식 '수급 좋은 것 고르기'는 US 스캔에서 역효과 기움(updown_vol
  −3.0%, pv_corr 유의 음성) — 이 컬럼들은 리스크 확인·분산 판단용이지 상승 근거가 아님.
- SEC 재무(XBRL)는 본구축 때(영구 아카이브라 기다려도 손실 0), FINRA 는 선적재(백필 유한).
- 2026-07-18: 급등형 틸트 us_rvdtc_a 관측 배선(§4) — us_page_data 가 us_tilt.csv 생성 +
  score_daily 적재, 전용 페이지 us_tilt.html(복권형 경고 도배), 텔레그램에 링크 +
  **관측 누적 일수 표시**(score_daily 모델별 · 판정 재료가 얼마나 채워졌는지) 추가.
  타이밍 스캔 결론(같은 날): 진입 시점 변경 이득 없음(신호가 실행에 강건), 레짐 게이트는
  스냅 데이터로 측정 불가(본구축 때 us_market.db 로), 보유기간 곡선은 감쇠 없이 h20 지속.

## §6. 캘린더

- **9월 본구축**: 더 쌓인 데이터로 스캔 재실행 → 후보 1~2개 PREREGISTER
  (유력 us_mus_v0, 대조 size제외판, 안정재 vol_cv 변형) → OOS 40거래일 판정.
  생존편향 보정(listing_events), size_amt→실시총 교체 검토, SEC XBRL 대량 작업.
- 캘린더 공통: 한국판 8월 중순 v3 판정, 9월 wu 판정(§ dh-q7m3k PROJECT_KNOWLEDGE.md).

## §7. 트러블슈팅 (실측)

- DB 파일 단독 복사는 hot-copy 손상 위험 — 스냅샷은 sqlite backup API(`src.backup(dst)`),
  검증은 `PRAGMA quick_check`. 분석용 반출은 Release tar 를 그대로 받는 게 정본.
- yfinance: 심볼 표기 `.`→`-`(BRK.B→BRK-B), 워런트·유닛·라이츠는 증권명 키워드로 제외,
  rate limit 구멍은 `--retry-empty` 로 메움. 배당·분할 반영 수익률은 반드시 adj_close.
- Actions 60일 비활성 시 스케줄 자동 정지 — 매일 CSV 커밋이 활동으로 잡혀 사실상 무관하나,
  장기 중단 후엔 Actions 탭에서 re-enable.
- 텔레그램: 그룹 chat_id 는 음수(-100…). 봇은 @유저명 전체로만 검색됨. getUpdates 404 = 토큰 오타.
