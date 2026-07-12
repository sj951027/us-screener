# us-screener — 미국장 종목 스크리너

한국판(dh-q7m3k)에서 검증된 **규율**(관측 우선 · 스펙 동결+OOS 40거래일 판정 ·
포인트-인-타임 · 매직넘버 금지 · 수집과 계산 분리 · 자동매매 금지)을 계승한
미국 전체 상장(NYSE+NASDAQ) 대상 스크리너.
**지식 문서: `US_PROJECT_KNOWLEDGE.md`** (아키텍처·데이터·모델·결정로그·캘린더) ·
설계 세부: `US_SCREENER_DESIGN.md`.

## 현재 상태 (2026-07-12~)

**완전 자동 관측 단계** — GitHub Actions 가 매일(미국 거래일 마감 후, 한국 아침 07시)
수집→점수 관측 적재→페이지·텔레그램 갱신→백업까지 수행. 노트북 불필요.

- 표: https://sj951027.github.io/us-screener/us.html
- 데이터 정본: Releases → "US data store" → us-data.tar.gz (금요일마다 weekly 2세대 백업)
- 본구축(스캔 재실행→PREREGISTER→OOS 판정)은 9월~.

## 실행

자동(Actions cron). 수동은 Actions 탭 → collect-us-data → Run workflow
(수동 실행은 휴장일 가드 무시하고 텔레그램 전송).

로컬 씨앗 수집(선택): `run_us_seed.bat` — 로컬 `../us-screener-data/` 는 2026-07-10
백필본(구본)이며 정본은 Release 자산.

## 원칙 리마인더

전부 관측·연구용. 매수신호 아님. 모델 채택은 사전등록 + OOS 40거래일 + CI + Bonferroni
통과 후에만 — "채택 안 함"도 정당한 결론.
