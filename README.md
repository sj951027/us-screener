# us-screener — 미국장 종목 스크리너

한국판(dh-q7m3k)에서 검증된 **규율**(관측 우선 · 스펙 동결+OOS 40거래일 판정 ·
포인트-인-타임 · 매직넘버 금지 · 수집과 계산 분리 · 자동매매 금지)을 계승한
미국 전체 상장(NYSE+NASDAQ) 대상 스크리너.
**지식 문서: `US_PROJECT_KNOWLEDGE.md`** (아키텍처·데이터·모델·결정로그·캘린더) ·
설계 세부: `US_SCREENER_DESIGN.md`.

## 현재 상태 (2026-09-27 갱신)

**완전 자동 관측 단계** — GitHub Actions 가 화~토 03:17 UTC(한국 12:17 예정, GitHub 지연으로 실제 도착은 오후)에
전날 미국 세션을 수집→점수 관측 적재→페이지 갱신→정본 업로드→텔레그램까지 수행. 노트북 불필요.
관측 모델은 전부 미등록(us_mus_v0·us_rvdtc_a) — 매수신호 아님. 운영 이력·결정은 `US_PROJECT_KNOWLEDGE.md` §5,
동작 변경 기록은 `patch_note/`(마지막 v19), 장애 대응은 §7.

- 표: https://sj951027.github.io/us-screener/us.html
- 데이터 정본: Releases → "US data store" → us-data.tar.gz (금요일마다 weekly 2세대 백업)
- 본구축(스캔 재실행→PREREGISTER→OOS 판정)은 9월~.

## 실행

자동(Actions cron). 수동은 Actions 탭 → collect-us-data → Run workflow
(수동 실행은 휴장일 가드 무시하고 텔레그램 전송).

로컬에서 수집기를 돌리지 않는다(Release 정본과 갈라짐 — CLAUDE.md). `run_us_seed.bat` 은 7월 부트스트랩 잔재.
로컬 분석은 Release `us-data.tar.gz` 를 받아 읽기 전용으로.

## 원칙 리마인더

전부 관측·연구용. 매수신호 아님. 모델 채택은 사전등록 + OOS 40거래일 + CI + Bonferroni
통과 후에만 — "채택 안 함"도 정당한 결론.
