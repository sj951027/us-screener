# REVIEW — 파이프라인 전체 코드 리뷰 (2026-09-06)

> 대상: 수집기 10종·us_page_data·us_notify_test·워크플로·페이지. 방법: 코드 읽기 + Release tar
> (09-05분) 실DB 대조. 병렬 리뷰 3건을 본인이 재검증(아래 "실측" = DB/코드로 확인, "추정" = 미확인).
> 이미 알려진 것(큐 굶음 §7, 내부자 플래그 v04)은 인접 결함만 추가.

## 높음 (데이터 정본·판정 재료를 망칠 수 있음)

| # | 위치 | 무슨 일이 일어나나 | 근거 | 최소 수정 |
|---|---|---|---|---|
| H1 | collect-data.yml:36-40, 122 | `gh release download` 가 네트워크/API 오류로 실패해도 "이전 데이터 없음 — 새로 시작" 분기로 진행 → 수집기가 빈 DB 생성 → quick_check 는 통과(손상 아님) → `--clobber` 로 3년 정본을 하루짜리로 덮음. 금요일이면 주간 백업까지 같은 tar 로 덮임 | 코드 경로 실측, 발생 이력은 없음 | 자산 존재 확인(`gh release view`)과 다운로드 실패를 구분해 실패면 `exit 1`; 업로드 전 새 tar 크기가 직전의 50% 미만이면 중단; 주간 백업은 직전 일일본으로 |
| H2 | us_page_data.py:76-81,166 | 시세 수집이 rate-limit 로 일부 배치를 건너뛴 날, 유니버스가 반토막 난 채 score_daily 에 `INSERT OR IGNORE` 로 박제. 다음날 7일 창이 시세를 채워도 점수는 복구 안 됨 | **실측**: score_daily 0901=3356 → **0902=1766 → 0903=2328** → 0904=3361 (같은 날 daily_ohlcv 는 6554행으로 정상 = 사후 보충됨). 0902 누락 심볼이 알파벳 J~Z 로 잘림. 텔레그램 top10·틸트 풀도 그날 오염 | 적재 전 완전성 게이트: 당일 행수 < 전일의 90% 면 CSV·score_daily·틸트 생략 + ⚠️ 출력(알림에도) |
| H3 | collect-data.yml:88,116 | `git pull --rebase && git push` 실패(동시 실행 등) 시 이후 스텝이 건너뛰어져 그날 수집한 DB 가 tar 에 반영 안 됨 → score_daily 그 날짜 영구 결손 | 코드 경로 실측; `concurrency` 미설정 | Upload 스텝을 Commit 앞으로(또는 Commit 에 `continue-on-error: true`); `concurrency: {group: collect-us, cancel-in-progress: false}` |
| H4 | us_ohlcv_collector.py:376-380,395 | 청크 전부 rate-limit 이면 "신규 0행" 후 exit 0. 알림은 `latest ≠ today` 를 휴장으로 간주해 생략 → 아무도 모름. 7일 창이 "오늘" 기준이라 7일 넘는 장애는 영구 갭 | 코드 경로 실측; 08-27 run 통째 부재(listing_daily 에 20260827 없음)는 7일 창 안이라 치유됨 | 창 시작 = `MAX(date) − 7d`; 평일 total==0 이면 ⚠️ 명시 + 알림 건강줄에 "시세 최신일 행수/전일 비율" |

## 중간

| # | 위치 | 내용 | 근거 | 수정 |
|---|---|---|---|---|
| M1 | us_options_collector.py:142,229 | yfinance `Ticker.options` 가 오류 시 예외 대신 `()` 반환 → 0행이 ok 도 fail 도 아님 → 결손 무음 | **실측**: snapshot_log 8/31 planned 518·done 348·**fail 0**·cut 0. 그날 모델 top50 적재 0/50(9/1 14/50, 9/3 47/50, 9/4 50/50) | 빈 체인 → 1회 재시도 후 `empty` 카운터; snapshot_log 컬럼 추가; done/planned<0.8 경고 |
| M2 | us_page_data.py:210-216 · us_short_collector.py | FINRA 잔고 파일은 `BRKB`·`BACPRB` 표기, 유니버스는 `BRK-B`·`BAC-PB` → dtc 매칭 실패, 틸트 풀에서 조용히 탈락 | **실측**: 유니버스 내 `-` 포함 77종목 dtc 매칭 0/77; 전체 6552 중 428 미매칭 | 조회 키 정규화 `s.replace("-P","PR").replace("-","")` |
| M3 | us_ohlcv_collector.py:220,357,383 | yfinance 1.7 은 티커 1개 청크도 MultiIndex 유지 → `sub = df` 경로에서 KeyError → except → 0행 무음. 재조정 배치 나머지가 1개면 3회 실패 후 `cliff_checked` 표기(데이터 미수정인 채) | 픽스처 재현(리뷰 에이전트), 현재 증분 나머지 9~16 이라 잠복 | `if isinstance(df.columns, pd.MultiIndex): sub = df[s]` |
| M4 | us_ohlcv_collector.py:388,404,214 | 큐 인접 3건: ① `INSERT OR IGNORE` 라 cliff 로 이미 있는 심볼에 진짜 split 이벤트가 와도 승격 불가 ② 배치 fetch 예외 시 attempts 미증가(무한 재시도) ③ 큐 절벽 1,306건 중 1,284건이 양쪽 adj==close(미조정 분할) | **실측**(DB) | `ON CONFLICT DO UPDATE` 로 split 승격; 예외도 attempts+1; 굶음 수정(§7)과 함께 |
| M5 | us_xbrl_collector.py:78,189 | PK 에 `start` 가 없어 같은 (cik,tag,end) 의 3개월값과 YTD 값 중 먼저 온 것만 남음 → 현재 Q2·Q3 는 전부 YTD | **실측**: AAPL NI end=2024-06-29 → 79.0B(9개월 누계). SUE 스캔은 이미 YTD 차분으로 대응했으나 3개월 원값은 유실 | `start`(또는 SEC frame) 컬럼 추가·PK 포함 + 재적재(`--force`, 큰 작업 — 9월 후반) |
| M6 | us_xbrl_collector.py:107 | fy/fp 는 '공시의' 회계기간이라 전년 비교분이 fy+1 로 중복 적재 | **실측**: AAPL end=2024-03-30 이 fy2024 Q2 와 fy2025 Q2 로 2행 | 기간 식별은 `end`(+start) 로; fy/fp 조회 금지(스캔 코드 주의) |
| M7 | us_earnings_collector.py:218 | `accepted` 는 UTC — 장전/장후를 UTC 고정 임계로 나누면 겨울(EST)에 오분류 | **실측**: `20:xxZ` 접수 8-K 가 4~10월 30,307건(16시 EDT 장후) vs 11~3월 3,830건(15시 EST **장중**) | 적재 시 `accepted_et` 컬럼(ZoneInfo America/New_York) 추가 |
| M8 | us_insider_collector.py:142 / 스캔 코드 | insider `symbol` 은 발행사 자유입력('Z AND ZG', 'NYSE: SCS', 'NONE') → 심볼 조인 시 23% 유실 | **실측**: 858,373행 중 195,002행 미매칭, 그중 45,644행은 issuer_cik→cik_ticker 로 복구 가능 | 스캔·배선 시 조인 키를 issuer_cik 로(내부자 재실행 때 반영) |
| M9 | us_seed_collector.py:124-136 | NDX100 구성이 40일째 0행인데 경고 없음(표 파싱 실패 비치명) → 페이지 NDX 뱃지 영구 미표시 | **실측**: index_membership 에 SP500 만 | idx 별 0건이면 ⚠️ 출력 + 건강줄 |
| M10 | us_notify_test.py:230 | 휴장 가드가 "최신일==오늘" 만 봐서 부분 수집(H2)은 통과, 전면 실패는 조용히 생략 | 코드 실측 | 건강줄에 시세 최신일 행수/전일 비율 + 평일인데 최신일≠오늘이면 "수집 실패 의심" 1줄 |

## 낮음

| # | 위치 | 내용 | 수정 |
|---|---|---|---|
| L1 | us_ohlcv_collector.py:104-110 | 0값 행 저장(adj_close=0 101행: DEC 2023-07, PCG-P* 20240614) → 수익률 inf | `close<=0 or adj_close<=0` 스킵 |
| L2 | us_rotate_collector.py:75 · us_short_collector.py:124 | 스냅샷 날짜를 UTC 로 라벨(자정 넘김 시 +1일: valuation_rotate 에 토요일 20260829 존재) | seed 의 `et_today()` 재사용 |
| L3 | us_seed_collector.py:100-103 | 두 상장목록 파일 중 하나가 빈 본문이면 수천 건 가짜 DISAPPEARED/NEW 이벤트(정정 불가) — 미발생 | 파일당 ≥1000행·전일 대비 ±5% 검증 |
| L4 | collect-data.yml:125 | 주간 백업 요일을 업로드 시점 UTC 로 판정 — 금요일 run 이 2시간 넘기면 토요일 → 생략(추정) | 잡 시작 시 요일을 env 로 고정 |
| L5 | us_page_data.py:111 | 우선주 종목명 빈칸 66종목(`$→-P` 매핑 누락) | `.replace("$","-P")` |
| L6 | us_xbrl_collector.py:197 · us_short_collector.py:141 | n_parsed=0 이어도 last_success 갱신 / 403 을 404 와 동일 취급(5주 넘는 차단 시 파일 영구 누락) | 조건부 갱신 / 코드 집계 |
| L7 | us_insider_collector.py:182,293 | 복수 신고인 filing 첫 owner 만 저장(규모 미검증) / 첫 404 에서 break(중간 분기 영구 404 시 이후 스킵, 현재는 정상 종료 확인) | setdefault→append / break→continue |
| L8 | docs/us.html:186 | 이름에 `"` 가 들어오면 CSV 열 밀림(현재 0건) | page_data 에서 `"` 치환 |
| L9 | score_daily 7월분 | 08-21 재조정 도입 전 미조정 역분할 오염 110행(mom12<−0.99) — IGNORE 원칙상 잔존 | OOS 판정 시 `mom12<−0.9` 행 제외 규칙 사전 명시 |

## 이상 없음으로 확인한 것
mom12·upratio63·가드 구현이 PREREGISTER 정의와 일치, mom12 NaN 은 core 마스크로 제외(중앙 채움 아님),
dtc 지연(settlement ≤ D−14) 정상, ET 변환·DST(ZoneInfo) 정상, 비밀값 노출 없음, 무결성 게이트 glob 이
us_shortvol.db 포함, PK 중복·순위 갭·NaN 점수 0건, rotate 커서 정상, cik_ticker 다중 클래스 정상,
insider 분기 적재 idempotent, 옵션 plan_capacity 영구 축소 아님, requirements 충족.

## 오늘 바로 반영한 것
- us_shortvol_collector.py (첫 러너 실행 전, v07 에 추기): 시리즈 없는 우선주·소문자 접미 처리,
  403/5xx 연속 시 `❌` 출력, 0행 파일 done 미표기, self-test 10항목.

## 제안 — v08 "안전망" 묶음 (승인 시 한 번에)
H1·H3·L4(워크플로), H2·M2·M10·L5(page_data·notify), H4·M3·M4·L1 + §7 큐 굶음(ohlcv 수집기),
M1(옵션), M9·L3(seed). 각각 오프라인 self-test/픽스처 추가, 0-diff 확인(점수식 불변) 후 적용.
M5(xbrl start 재적재)·M7(accepted_et)·M8(cik 조인)은 9월 후반 별도 라운드.
