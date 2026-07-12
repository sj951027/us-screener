# us-screener — 미국장 종목 스크리너

한국판(dh-q7m3k)에서 검증된 **규율**(관측 우선 · 스펙 동결+OOS 40거래일 판정 ·
포인트-인-타임 · 매직넘버 금지 · 수집과 계산 분리 · 자동매매 금지)을 계승한
미국 전체 상장(NYSE+NASDAQ) 대상 스크리너. 설계는 `US_SCREENER_DESIGN.md`.

## 현재 상태 (2026-07)

**씨앗 수집 단계** — 본구축(데이터 토대→팩터 스캔→사전등록)은 한국판 판정 시즌 후(9월~).
지금은 "백필 불가능한" 데이터만 매일 적재한다:

- `us_seed_collector.py` → `../us-screener-data/us_seed.db`
  - listing_daily / listing_events: 전체 상장 목록 스냅샷 + 상폐·신규 diff (NASDAQ Trader 공식)
  - index_membership / membership_events: S&P500·NDX100 구성 + 편출입 diff (위키피디아)

## 실행

```
run_us_seed.bat        (매일 1회, 아무 때나 — 작업 스케줄러 등록 권장)
```

## 데이터 폴더

`../us-screener-data/` — repo 밖 격리(한국판 dh-q7m3k-data 패턴). git 추적 안 함.

## 원칙 리마인더

전부 관측·연구용. 매수신호 아님. 모델 채택은 사전등록 + OOS 40거래일 + CI + Bonferroni
통과 후에만 — "채택 안 함"도 정당한 결론.
