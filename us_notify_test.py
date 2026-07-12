# -*- coding: utf-8 -*-
"""
us_notify_test.py — [US 테스트·관측] 텔레그램 알림 (검증 전 기움 표시 — 매수신호 아님)
==============================================================================
첫 스캔(research/RESEARCH_us_first_scan_20260712.md)에서 in-sample 최강 기움이었던
mom12 + upratio63 + size(거래대금) 순위합의 당일 상위 종목을 텔레그램으로 보낸다.

⚠️ 규율: 이 점수는 **in-sample 가설**(23앵커, 생존편향 미보정)이고 PREREGISTER·OOS
판정 전이다. 메시지 전체를 '테스트·관측·매수신호 아님'으로 도배한다(한국판 lv_a/wu_a
테스트 알림과 동일 원칙 — 표시는 판정 기준이 아님, 골대 불변).

가드(스캔과 동일): 종가≥$5 · 거래대금 20일평균≥$1M · 무변동 컷.
키: 환경변수 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID (GitHub Actions Secrets 로 주입).
사용:
    python us_notify_test.py --dry-run   # 전송 없이 메시지 출력
    python us_notify_test.py             # 전송(키 없으면 콘솔 출력만, 비치명)
"""
import argparse
import os
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("US_DATA_DIR", "").strip() or (HERE / ".." / "us-screener-data"))
OHLCV_DB = DATA_DIR / "us_ohlcv.db"
SEED_DB = DATA_DIR / "us_seed.db"
TOP_N = 10
LOOKBACK = 260   # mom12(252) + 여유
PAGE_URL = "https://sj951027.github.io/us-screener/us.html"  # 전체 표(GitHub Pages)


def build_message():
    con = sqlite3.connect(f"file:{OHLCV_DB}?mode=ro", uri=True)
    dates = [d for (d,) in con.execute(
        "SELECT DISTINCT date FROM daily_ohlcv ORDER BY date")][-LOOKBACK:]
    raw = pd.read_sql(
        "SELECT symbol,date,close,adj_close,volume FROM daily_ohlcv WHERE date>=?",
        con, params=(dates[0],))
    con.close()
    for c in ("close", "adj_close", "volume"):
        raw[c] = pd.to_numeric(raw[c], errors="coerce")
    C = raw.pivot_table(index="symbol", columns="date", values="adj_close",
                        aggfunc="last").sort_index(axis=1)
    RAWC = raw.pivot_table(index="symbol", columns="date", values="close",
                           aggfunc="last").reindex(C.index).sort_index(axis=1)
    V = raw.pivot_table(index="symbol", columns="date", values="volume",
                        aggfunc="last").reindex(C.index).sort_index(axis=1)
    ds = list(C.columns)
    i = len(ds) - 1
    R = C.pct_change(axis=1, fill_method=None)
    AMT = RAWC * V
    # 가드
    w = R[ds[i - 20: i + 1]]
    n = w.notna().sum(axis=1)
    rv = w.std(axis=1, ddof=1)
    amt20 = AMT[ds[i - 19: i + 1]].mean(axis=1)
    ok = (rv >= 0.003) & ((w == 0).sum(axis=1) / n.where(n > 0) <= 0.5) & \
         (RAWC[ds[i]] >= 5.0) & (amt20 >= 1e6)
    # 팩터
    w63 = R[ds[i - 62: i + 1]]
    F = pd.DataFrame(index=C.index)
    F["mom12"] = C[ds[i - 21]] / C[ds[i - 252]] - 1
    F["upratio63"] = (w63 > 0).sum(axis=1) / w63.notna().sum(axis=1)
    F["size_amt"] = np.log10(amt20.where(amt20 > 0))
    F = F[ok.reindex(F.index).fillna(False)]
    score = None
    for j, f in enumerate(["mom12", "upratio63", "size_amt"]):
        rk = F[f].rank(pct=True, ascending=True)
        filled = rk if j == 0 else rk.fillna(0.5)
        if j == 0:
            core = rk.notna()
        score = filled if score is None else score + filled
    score = score.where(core).dropna().sort_values(ascending=False)
    top = score.head(TOP_N)
    # 이름
    names = {}
    if SEED_DB.exists():
        s = sqlite3.connect(f"file:{SEED_DB}?mode=ro", uri=True)
        names = {sym.replace(".", "-"): (nm or "")[:28] for sym, nm in s.execute(
            "SELECT symbol,name FROM listing_daily WHERE date=(SELECT MAX(date) FROM listing_daily)")}
        s.close()
    lines = ["🧪 <b>[US 테스트·관측]</b> mom12+상승일비율+size 기움 상위",
             f"기준일 {ds[i]} · 유니버스 {len(score):,} (가드 $5·$1M)", ""]
    for r, (sym, sc) in enumerate(top.items(), 1):
        lines.append(f" {r}. <b>{sym}</b> {sc:.2f} · 12-1모멘텀 {F.at[sym,'mom12']*100:+.0f}%"
                     f" · 상승일 {F.at[sym,'upratio63']*100:.0f}%"
                     f" — {names.get(sym, '')}")
    lines += ["", f"📊 전체 표(정렬·필터): {PAGE_URL}",
              "", "⚠️ <b>매수신호 아님</b> — in-sample 가설(생존편향 미보정), PREREGISTER·OOS",
              "판정 전 순수 관측. 근거: research/RESEARCH_us_first_scan_20260712.md"]
    return "\n".join(lines)


def send(msg):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        print("⏭ 텔레그램 키 없음 — 콘솔 출력만.")
        return False
    import requests
    r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                      json={"chat_id": chat_id, "text": msg, "parse_mode": "HTML",
                            "disable_web_page_preview": True}, timeout=15)
    print("전송:", "OK" if r.ok else r.text[:200])
    return r.ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if not OHLCV_DB.exists():
        print(f"us_ohlcv.db 없음({OHLCV_DB}) — 생략(비치명).")
        return
    msg = build_message()
    print(msg)
    if not args.dry_run:
        send(msg)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"❌ 실패(비치명): {e}")
        sys.exit(0)
