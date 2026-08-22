# US_PROJECT_KNOWLEDGE.md — 미국판 스크리너 지식 문서

> 최종 갱신: 2026-08-19 · 이 문서가 이 repo 의 단일 기준(설계 세부는 US_SCREENER_DESIGN.md,
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
| us_fundamentals.db | insider_tx | Form 4 내부자 P/S 거래(SEC 분기 구조화셋, 관계플래그·단가·**filed=PIT**, 분기 idempotent·회당 4분기 점진 백필 2019~) — 내부자 순매수 신호 재료. 2026-07-26 신설. **⚠️ 2026-08-19 실측 0행(SEC 403) — §7 참조** |
| us_fundamentals.db | earnings_events | 실적 발표일(8-K Item 2.02 판별 + 10-K/Q 폴백, **accepted 접수시각**으로 장전/장후 구분, filed≥2019, PK cik+accn append-only) — PEAD 이벤트 날짜. 2026-07-26 신설. **⚠️ 2026-08-19 실측 0행(SEC 403)** |
| us_fundamentals.db | xbrl_facts/cik_ticker | SEC XBRL 재무 벌크(주1회, 화이트리스트 12태그, end≥2019, **filed(공시일) 보존=PIT append-only**) — 저평가·SUE(PEAD)·재무모멘텀 재료. 2026-07-26 신설, 관측 전용. **⚠️ 2026-08-19 실측 0행(SEC 403)** |
| us_ohlcv.db | short_interest | FINRA 격주 공매도 (73파일 백필 완료) ⚠️ 별도 us_short.db 아님 — 수집기가 us_ohlcv.db 에 적재(2026-07-18 문서 교정: 이 오기를 믿은 틸트 배선이 runner 에서 조용히 생략되는 버그 유발) |

### §3-1. 실측 스냅샷 (2026-08-19, Release tar us-data.tar.gz · 러너 08-18 22:40 UTC 생성분)

| DB | 크기 | quick_check | 주요 테이블 행수 |
|---|---|---|---|
| us_ohlcv.db | 640MB | ok | daily_ohlcv 4,688,607(max date 20260818·7,434심볼) · short_interest 1,538,414(max settle 20260731) · score_daily 92,082 · valuation_rotate 17,660 · sector_cache 6,128 |
| us_seed.db | 37MB | ok | listing_daily 365,417 · listing_events 493 · index_membership 14,084 |
| us_market.db | 0.3MB | ok | market_daily 5,472 (SPX 775행, 2026년 결손 6일=전부 미국 공휴일 → 결손 아님) |
| **us_fundamentals.db** | **0.06MB** | ok | **xbrl_facts 0 · earnings_events 0 · insider_tx 0 · cik_ticker 0 — 전부 빈 테이블** |
| ↳ 재실측 8/21 | **441MB** | — | **403 해결 후**: xbrl_facts 1,672,410(filed 2019-01~2026-08·6,456개사) · earnings_events 416,370 · insider_tx 239,618(31분기 중 8, 백필 진행 중) · cik_ticker 10,387 |

score_daily 관측 누적(등록 전 관측이며 OOS 판정 재료 아님):
`us_mus_v0` 27거래일(20260713~20260818, 90,933행) · `us_rvdtc_a` 23거래일(20260717~20260818, 1,149행).

> 관측 컬럼 주의(실측): score_daily 컬럼은 mom12/upratio63/size_amt 뿐 — §1 "검증 전 팩터는
> 기록만" 규칙 대비 vol_cv·dd52w·rv63·dtc 가 빠져 있다. 다만 이들은 전부 가격/공매도 원천에서
> **사후 PIT 재계산이 가능**하고(vol_cv·dd52w 는 매일 커밋되는 docs/data/us_latest.csv 의 git
> 히스토리에도 남음), 재구성 불가한 '그날의 유니버스 구성'은 score_daily 에 있으므로 실질
> 손실은 없다. 스키마 확장은 선택 사항이지 9월 전 필수 과제가 아니다.

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

- 2026-07-26 (데이터 확장): "특급 모델 없나" 논의 결론 — 같은 가격 데이터 재조합은 한계
  (KR 5전5패·US 눌림목 스캔 h60 +0.9%p CI 경계 실측). 새 알파는 **직교 데이터**에서 →
  SEC EDGAR 3종 수집 결정: ① XBRL 재무(us_xbrl_collector, 완료) ② 실적발표일 8-K(us_earnings_collector, 완료)
  ③ Form 4 내부자(us_insider_collector, 완료 — 백필은 Actions ~1주 자동). 전부 관측 적재만, 검증은 본구축 PREREGISTER 후.
  덤: 우량추세(모멘텀 상위20%+200MA) 내 진입시점 스캔 — 신고가 +3.5%p vs 눌림 +4.3~4.5%p
  (h60 초과, 86주간앵커, 생존편향 미보정) · 짝차이 +0.93%p CI[-0.02,+1.91] = 기움.
  본구축 때 dd63 관측 컬럼으로 정식 검증 후보.

- 2026-08-09 (매도 규칙 스캔): us_mus_v0 top50 매수 후 청산 시점 13규칙 비교
  (research/RESEARCH_us_exit_scan_20260809.md · 96주간앵커 · in-sample · 생존편향 미보정 ·
  창 중첩으로 CI 과소). ① **20일 내 조기청산은 어떤 규칙도 h20을 못 이김** — 타이트할수록
  나쁨(TR10 −0.81%p · SL10 −0.59%p · TP10 −0.29%p, 전부 CI 0 포함). ② 순위이탈 조기매도도
  이득 없음(RANK300_20 −0.08%p CI[−0.16,−0.01] = 근소하게 해로운 기움). ③ **보유 20/40/60일은
  기간보정 시 무차별**(20d당 환산 초과 +2.85/+2.89/+2.51%p, 짝차이 CI 전부 0 포함) → 신호의
  초과수익이 20일 이후에도 감쇠하지 않음(7/18 결과와 정합). 실무 함의는 "왕복비용이 아까울수록
  긴 주기가 유리한 기움"이지 채택된 규칙이 아님. → **PREREGISTER 매도 규칙 후보 없음**
  (단순 고정 보유 유지가 현 데이터 부합). 레짐 기반 청산은 이번에도 미측정.

- 2026-08-19 (파이프라인 점검): Actions 35회 전부 성공 표시·라이브 페이지 date=20260818 정상.
  그러나 **SEC 3종(XBRL·실적일·Form 4) 수집이 한 건도 적재되지 않은 상태**를 실측 발견
  (§3-1). 원인은 Actions 로그 실측 — 세 수집기 모두 첫 요청부터
  `403 Client Error: Forbidden` (company_tickers.json / submissions.zip / 2019q1_form345.zip).
  세 수집기 전부 예외를 삼키고 exit 0(비치명 설계) → 워크플로는 녹색, 데이터는 0.
  **"수집기 완료 = 데이터 존재"가 아니었다** — 7/26 기록의 "완료"는 코드 완료였고 적재 검증이
  없었다. 교훈: 신규 수집기는 '행수 > 0' 게이트나 텔레그램 요약에 행수를 넣어 조용한 실패를
  드러내야 한다. 조치는 §7 참조. 9월 본구축의 '직교 데이터' 전략은 이 복구에 의존.

- 2026-08-19 (2차 팩터 스캔): 8/18 DB로 가격·거래량·공매도 8팩터 재스캔
  (research/RESEARCH_us_factor_scan2_20260819.md · 102주간앵커 · in-sample · 블록부트스트랩).
  ① 전 유니버스 유의: upratio126·dd252·dd63(신고가 근접)·vol_cv(재확인), rv63 은 95%만.
  ② **그러나 top50 증분은 전부 0**(짝차이 CI 전부 0 포함) — "같은 가격 데이터 재조합은
  한계" 3번째 재확인. ③ vol_cv 안정재 효과 이번 창 재현 미약(적중 +1%p) → v1_cv 후보
  기대치 하향. ④ **base top50 의 EW평균 초과 +1.63%p/20d CI[−0.34,+3.42] = 0 포함** —
  OOS "채택 안 함"가능성이 실질적. ⑤ 레짐 관측: SPX≤200MA(n=11)에서 상대초과 더 큼(참고).

- 2026-08-21 (SEC 403 해결 확인): 8/19 `SEC_USER_AGENT` env 패치(코드 0줄) 후 첫 주간
  XBRL 사이클에서 3종 전부 적재 성공 — us_fundamentals.db 0.06MB→441MB(위 §3-1 재실측).
  **원인 확정: UA 형식**(괄호·세미콜론·URL 포함 기본값 → `이름 이메일` 단순형으로 해결).
  insider 는 회당 4분기 점진 백필로 8/31분기 — 9월 초 완성 예상. filed=PIT 이므로 XBRL·
  실적일은 **과거 이력까지 즉시 스캔 가능**(실시간 축적 불요) → 9월 직교 데이터 스캔 재개방.

- 2026-08-21 (라이브 관측 첫 성적 + 데이터 결함 발견): score_daily 실기록 기준
  (20260713~20260820, 28거래일, 일일 리밸런스 EW·비용 0·종가체결) —
  **us_mus_v0 top50 −7.0% vs 가드유니버스 EW +2.4% = 초과 −9.4%p** · SPX +1.7%.
  us_rvdtc_a top10 은 유니버스와 무차이(−1.3% vs −1.3%), SPX 대비 −3.8%p.
  28거래일 ≈ 독립 h20 창 1.4개 — 통계적 결론 불가·노이즈 범위이나, **엣지의 증거가
  아직 없다는 사실은 그대로 기록**한다(스캔2의 "초과 CI 0 포함"과 정합). 관측은 계속.
  이 계산 중 **분할 미조정 결함 발견**(§7) — MNST 가짜 −50% 절벽 제거 후 수치임(제거 전 −10.2%p).

- 2026-08-22 (운영 규칙 신설 — 패치노트): 동작이 바뀌는 모든 변경(수집기·notify·
  워크플로·페이지·스코어)은 **patch_note/vNN_YYYYMMDD.md** 에 기록한다 — 무엇을/왜/
  바뀐 파일/검증/남은 한계. 규칙 정본은 patch_note/README.md. 소급 v01(8/19 SEC UA+필터)·
  v02(8/21 분할 재조정)·v03(8/22 텔레그램 DB 건강 요약, 조용한 실패 감시) 작성.
  research/ 산출물·docs/data 자동 커밋·문서만의 변경은 제외(§5 로 충분).

## §6. 캘린더

- **9월 본구축**: 더 쌓인 데이터로 스캔 재실행 → 후보 1~2개 PREREGISTER
  (유력 us_mus_v0, 대조 size제외판, 안정재 vol_cv 변형) → OOS 40거래일 판정.
  생존편향 보정(listing_events), size_amt→실시총 교체 검토, SEC XBRL 대량 작업.
  - ~~선결: SEC 403 복구~~ → **2026-08-21 해결 확인**(§5). XBRL·실적일은 filed=PIT 라
    과거 이력까지 즉시 스캔 가능. 직교 데이터(SUE/PEAD·저평가·내부자) 후보는
    **9월 동결 전 in-sample 스캔에서 유의할 때만** 편입 — 스캔 없이 급히 넣지 않는다.
    insider 백필 완성(9월 초) 후 스캔 권장.
  - PREREGISTER 초안: research/PREREGISTER_us_202609_draft.md (2026-08-19 작성, 미동결).
- 캘린더 공통: 한국판 8월 중순 v3 판정, 9월 wu 판정(§ dh-q7m3k PROJECT_KNOWLEDGE.md).

### §6-1. 용량 캘린더 (2026-07-26 점검)
- Release 자산은 **파일당 2GB** 제한. 현 tar ~0.8~1.2GB 추정(ohlcv 압축 + fundamentals).
  성장률(ohlcv +250MB/yr raw · xbrl +50~100MB/yr)로 **2028년 전후 한도 근접** →
  그때 DB별 tar 분할 업로드(워크플로 몇 줄)로 해결. 지금 조치 불요, 잊지만 말 것.
- 자산 개수는 --clobber 교체라 상시 2개(daily+weekly) 고정 — 누적 없음.
- Actions: public repo 무료 무제한 · 러너 디스크 14GB(피크 ~7GB) · RAM 7GB — 여유.
- git repo 자체는 코드+us_latest.csv 일일커밋(~100MB/yr, §5 기존 항목 — 본구축 때 재검토).

## §7. 트러블슈팅 (실측)

- DB 파일 단독 복사는 hot-copy 손상 위험 — 스냅샷은 sqlite backup API(`src.backup(dst)`),
  검증은 `PRAGMA quick_check`. 분석용 반출은 Release tar 를 그대로 받는 게 정본.
- yfinance: 심볼 표기 `.`→`-`(BRK.B→BRK-B), 워런트·유닛·라이츠는 증권명 키워드로 제외,
  rate limit 구멍은 `--retry-empty` 로 메움. 배당·분할 반영 수익률은 반드시 adj_close.
- Actions 60일 비활성 시 스케줄 자동 정지 — 매일 CSV 커밋이 활동으로 잡혀 사실상 무관하나,
  장기 중단 후엔 Actions 탭에서 re-enable.
- **분할 소급 미조정 (2026-08-21 실측·미해결·9월 전 수정 필요)**: us_ohlcv_collector 의
  7일 증분 창은 **백필 이후 발생한 주식분할을 과거 행에 소급 반영하지 못한다** —
  분할일에 adj_close 가짜 절벽 발생. 실측: MNST 2:1 분할(20260811, $91.43→$45.53,
  adj_close==close 로 조정 부재 확인). 정수비(±6%) 절벽 의심 20260701 이후 94건
  (일부는 실제 급등락 — 개별 확인 필요). 영향: ① mom12·upratio 등 점수가 분할 종목에서
  가짜 폭락으로 오염 ② 라이브 성과 추적 오염 ③ **방치 시 OOS 판정 데이터 자체가 오염**.
  **수정안 적용(2026-08-21, 사용자 승인)**: us_ohlcv_collector v2026-08-21 —
  ① 증분 fetch actions=True 로 창 안 분할/배당 감지→adjust_queue ② 매 실행 절벽 스캔
  (오탐=실제 급등락이어도 재수집은 같은 값 덮어쓰기라 무해, cliff_checked 로 1회만)
  ③ 큐 심볼 전체 이력 재수집 INSERT OR REPLACE, 회당 200 상한, 0행 3회면 포기(상폐 큐 점유 방지).
  오프라인 self-test 13항목 통과·백필 경로 무변경(0-diff)·실DB 스캔 실측 1,251절벽/761심볼
  (대부분 백필 이전 실제 급등락 — 1회 재수집 후 자동 종결, 초회 ~4일에 걸쳐 소화).
  잔여 한계: 증분 창을 벗어난 과거 배당의 소급 드리프트(연 1~2%)는 미감지 — 필요시
  연 1회 전체 재백필로 보정(미결정).
- **SEC 403 (2026-08-19 실측 → 2026-08-21 해결·원인=UA 형식 확정)**: GitHub Actions 러너에서 sec.gov 3개 엔드포인트 전부
  첫 요청부터 403. 같은 URL을 사용자 브라우저(가정용 IP·크롬 UA)와 별도 데이터센터 IP의
  일반 UA 클라이언트로 확인하면 **둘 다 200** → 데이터센터 IP 자체가 원인일 가능성은 낮고
  **User-Agent 형식이 유력 용의자**(추정). 현재 기본 UA
  `us-screener research (github.com/sj951027; seok5139@gmail.com)` 는 괄호·세미콜론·URL 을 포함 —
  SEC 권장 형식은 `이름 이메일` 단순형. 세 수집기 모두 `SEC_USER_AGENT` 환경변수를 이미
  읽으므로 **코드 수정 0줄**로 시험 가능: 워크플로 `Run collectors` 스텝 env 에
  `SEC_USER_AGENT: "us-screener seok5139@gmail.com"` 한 줄 추가 → 수동 실행 → 로그 확인.
  실패해도 비치명(exit 0). 그래도 403이면 다음 후보는 Accept-Encoding 헤더 누락 → 그 다음은
  러너 IP 대역 차단(이 경우 로컬/자가 러너 또는 다른 소스로 전환 검토).
- 텔레그램: 그룹 chat_id 는 음수(-100…). 봇은 @유저명 전체로만 검색됨. getUpdates 404 = 토큰 오타.
