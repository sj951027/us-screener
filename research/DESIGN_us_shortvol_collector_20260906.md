# DESIGN — FINRA 일별 공매도 거래량 수집기 (2026-09-06 초안 · 미배선 · 승인 대기)

## 왜
- 사용자 방향(2026-09-06): 새 모델은 새 재료에서. 무료·과거 파일 공개·바로 3년치 스캔 가능한
  소스 중 첫 순위. 기존 short_interest(격주 잔고)와 다른 정보 — 매일 "그날 체결 중 공매도 비율".
- 문헌: 일별 공매도 비율이 높을수록 이후 단기 부진(Boehmer-Jones-Zhang 계열). 롱온리에서는
  '비율이 낮은/급감한' 쪽 후보. 검증 전엔 관측 전용.

## 무엇을 (draft_us_shortvol_collector.py — research/ 에 초안)
- 소스 `https://cdn.finra.org/equity/regsho/daily/CNMSshvol{YYYYMMDD}.txt` (통합 NMS, 2018-08~).
- 저장 `us_shortvol.db` `short_volume_daily(date, symbol, short_vol, short_exempt_vol, total_vol, market)`
  PK(date,symbol) + `shortvol_files_done`. **daily_ohlcv 심볼로 제한**(용량).
- 일일: 최근 10일 창 탐침(휴장일·미게시는 404/짧은 응답으로 통과). 백필: `--backfill-from 2023-05-01`
  회당 150파일(≈ 6회 실행으로 3.3년 완성). idempotent·비치명·raw 보존 폴백·self-test 6항목 통과.
- 심볼 표기 `.`→`-` 통일(BRK.B→BRK-B, ohlcv 와 조인용).

## 배선 계획 (승인 후 = 패치노트 v07)
1. 파일을 루트 `us_shortvol_collector.py` 로 이동(초안 헤더 문구 제거).
2. 워크플로 `Run collectors` 에 `python us_shortvol_collector.py --backfill-from 2023-05-01` 1줄
   (백필 완료 후엔 인자 없이도 동작하나, 있어도 done 건너뜀이라 그대로 둬도 무해).
3. 텔레그램 DB 건강 요약(v03)에 "공매도거래량 최신일" 항목 추가(조용한 실패 감시) — 선택.
4. 실데이터 첫 러너 로그의 "💾 short_volume_daily … 누적 n행" 으로 적재 검증(8/19 교훈: 완료≠데이터).

## 한계·리스크 (추정 표기)
- 컨테이너에서 cdn.finra.org 차단이라 **실파일 포맷을 직접 확인 못 함** — FINRA 포맷 가이드
  기준(파이프·헤더·트레일러)으로 방어 파싱, 실패 시 raw_finra/ 보존. 첫 러너 로그 확인 필수.
- 게시 시각(18:00 ET 이전)이 러너(22:00 UTC=18:00 ET)와 겹침 → 당일 파일은 다음 실행에서.
  스캔 PIT 규칙: **거래일 D 파일은 D+1 이후 앵커에서만 사용.**
- 용량: 3.3년 백필 ≈ 6M 행·~250MB(추정), 연 ~80MB 증가 → 2GB 한도 캘린더(§6-1) 약간 앞당김.
  2018년까지 전부 받는 것은 ~1GB 라 보류.
- 비상업 이용 조건(FINRA 안내) — 개인 조회 전용 프로젝트라 부합(법률 판단 아님).

## 스캔 계획 (백필 완료 후)
svr5·svr20(비율 5·20일 평균), Δsvr(20일 대비 5일), 전 유니버스 day-IC(사전 부호 −)·십분위·
mus top50 4번째 항/제외 짝비교 — 기존 프로토콜 그대로, 신규 검정 수만큼 Bonferroni.
