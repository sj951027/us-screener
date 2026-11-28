# -*- coding: utf-8 -*-
"""
us_event_scan_pit_20261128.py — 8-K 항목 효과가 '그 시점 유니버스'에서도 남는지 (in-sample, 관측 전용)
==========================================================================================
왜: us_event_scan(·followup) 의 가드는 과거 날짜의 종가 ≥ $5 를 **지금 DB 의 종가**로 판정한다. 야후 종가는 분할 반영값이라,
  당시 $0.30 에 거래되다 나중에 1:20 병합(리버스 스플릿)한 종목은 과거 종가가 $6 으로 보여 가드를 통과한다.
  그런 종목(동전주 → 병합 → 계속 하락)이 바로 3.01(상장 유지 미달 통지)·3.03·5.03(병합용 정관 변경)·3.02(사모 증자) 그룹에 몰린다.
  즉 과거 구간의 큰 음(−)의 효과에는 '당시엔 유니버스에 없었을 종목'이 섞여 있다.
방법: score_daily 가 실제로 기록된 날(2026-07-13~)만 쓴다 — 그날 us_mus_v0 가 점수를 매긴 종목 = 당시 가격으로 가드를 통과한 진짜 유니버스.
  주간 앵커, 직전 20거래일 안에 해당 항목 8-K(비실적)를 낸 종목 그룹의 h20 − 유니버스 평균. 같은 앵커에서 '지금 DB 가격 가드' 유니버스로도
  계산해 그룹 크기와 효과를 나란히 본다.
한계: 앵커 수가 적다(약 15개, 한 국면) — 방향 확인용이지 검정이 아니다. cik→티커는 현재 매핑.

    python research/us_event_scan_pit_20261128.py --data-dir <정본 tar 를 푼 폴더>
"""
import argparse
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

H, STEP = 20, 5
ITEMS = ["3.01", "3.03", "3.02", "5.03", "1.01", "2.01", "8.01", "7.01", "5.02"]
DILUTE = ["3.01", "3.03", "3.02", "5.03"]     # 상장 유지 미달·권리 변경·사모 증자·정관 변경 — 원 스캔에서 가장 컸던 묶음(사후 선택)


def ro(path):
    return sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True)
    D = Path(ap.parse_args().data_dir)
    con = ro(D / "us_ohlcv.db")
    d0 = con.execute("SELECT MIN(date) FROM score_daily WHERE model='us_mus_v0'").fetchone()[0]
    start = con.execute("SELECT MIN(date) FROM (SELECT DISTINCT date FROM daily_ohlcv WHERE date < ? ORDER BY date DESC LIMIT 70)", (d0,)).fetchone()[0]
    df = pd.read_sql_query("SELECT symbol, date, close, adj_close, volume FROM daily_ohlcv WHERE date >= ?", con, params=(start,))
    C = df.pivot(index="symbol", columns="date", values="adj_close")
    RC = df.pivot(index="symbol", columns="date", values="close")
    V = df.pivot(index="symbol", columns="date", values="volume")
    cnt = C.notna().sum()
    keep = cnt >= 0.1 * cnt.max()
    C, RC, V = C.loc[:, keep], RC.loc[:, keep], V.loc[:, keep]
    ds = sorted(C.columns)
    Cf = C.ffill(axis=1, limit=H)
    sd = pd.read_sql_query("SELECT date, symbol, rank FROM score_daily WHERE model='us_mus_v0'", con)
    univ = {d: set(g["symbol"]) for d, g in sd.groupby("date")}
    top50 = {d: list(g[g["rank"] <= 50]["symbol"]) for d, g in sd.groupby("date")}
    t50, share = [], []

    fc = ro(D / "us_fundamentals.db")
    ct = pd.read_sql_query("SELECT cik, ticker FROM cik_ticker", fc)
    ct["ticker"] = ct["ticker"].str.upper()
    ev = pd.read_sql_query("SELECT cik, accepted, filed, items FROM earnings_events WHERE form='8-K' AND is_earnings=0 AND items!='' AND filed>=?",
                           fc, params=(f"{start[:4]}-{start[4:6]}-{start[6:]}",))
    ev["d8"] = ev["accepted"].where(ev["accepted"].fillna("") != "", ev["filed"]).str.slice(0, 10).str.replace("-", "", regex=False)
    ev = ev.merge(ct, on="cik")
    ev["item"] = ev["items"].str.split(",")
    ev = ev.explode("item")
    ev["item"] = ev["item"].str.strip()
    ev = ev[["ticker", "d8", "item"]].drop_duplicates().sort_values("d8").reset_index(drop=True)
    ed = ev["d8"].to_numpy()

    i0 = ds.index(d0)
    anchors = [i for i in range(max(i0, 63), len(ds) - H, STEP) if ds[i] in univ]
    names = ITEMS + ["증자·병합류 묶음", "비실적 8-K 아무거나"]
    out = {k: {n: [] for n in names} for k in ("pit", "pit_med", "pit_n", "now", "now_n", "only_now_n", "only_now")}
    for i in anchors:
        t = ds[i]
        amt20 = (RC[ds[i - 19:i + 1]] * V[ds[i - 19:i + 1]]).mean(axis=1)
        c63 = C[ds[i - 62:i + 1]]
        ok = (RC[t] >= 5) & (amt20 >= 1e6) & (c63.notna().sum(axis=1) >= 60) & (c63.std(axis=1, ddof=1) > 0)
        fwd_all = (Cf[ds[i + H]] / C[t] - 1)
        f_now = fwd_all.reindex(ok[ok].index).dropna()                 # 지금 DB 가격으로 판정한 가드 유니버스
        f_pit = fwd_all.reindex(list(univ[t])).dropna()                # 그날 실제로 점수를 받은 유니버스
        e = ev.iloc[np.searchsorted(ed, ds[i - H], "left"):np.searchsorted(ed, t, "left")]
        groups = {it: set(e[e["item"] == it]["ticker"]) for it in ITEMS}
        groups[names[-2]] = set(e[e["item"].isin(DILUTE)]["ticker"])
        groups[names[-1]] = set(e["ticker"])
        share.append((~f_now.index.isin(f_pit.index)).mean())
        tp = f_pit.reindex(top50[t]).dropna()
        fl = tp[tp.index.isin(groups[names[-2]])]
        t50.append((len(fl), fl.mean() - tp[~tp.index.isin(fl.index)].mean() if len(fl) else np.nan))
        for n, syms in groups.items():
            a, b = f_pit.reindex(list(syms)).dropna(), f_now.reindex(list(syms)).dropna()
            only = b[~b.index.isin(f_pit.index)]
            out["pit"][n].append(a.mean() - f_pit.mean() if len(a) else np.nan)
            out["pit_med"][n].append(a.median() - f_pit.median() if len(a) else np.nan)
            out["pit_n"][n].append(len(a))
            out["now"][n].append(b.mean() - f_now.mean() if len(b) else np.nan)
            out["now_n"][n].append(len(b))
            out["only_now_n"][n].append(len(only))
            out["only_now"][n].append(only.mean() - f_now.mean() if len(only) else np.nan)
    print(f"앵커 {len(anchors)}개 {ds[anchors[0]]}→{ds[anchors[-1]]} · 직전 {H}거래일 안 공시 그룹 · h{H} 초과수익 %p (표본이 적어 CI 는 내지 않는다)")
    print(f"{'항목':<22}{'그날 유니버스':>12}{'(중앙값)':>10}{'평균n':>7}{'양(+) 앵커':>10} |{'지금가격 가드':>12}{'평균n':>7} |{'지금 가드에만 있는 종목':>16}{'평균n':>7}")
    for n in names:
        x = np.array(out["pit"][n], dtype=float)
        print(f"{n:<22}{np.nanmean(x) * 100:>+14.2f}{np.nanmean(out['pit_med'][n]) * 100:>+11.2f}{np.mean(out['pit_n'][n]):>8.1f}"
              f"{int((x > 0).sum()):>6}/{int(np.isfinite(x).sum()):<4} |{np.nanmean(out['now'][n]) * 100:>+14.2f}{np.mean(out['now_n'][n]):>8.1f} |"
              f"{np.nanmean(out['only_now'][n]) * 100:>+18.2f}{np.mean(out['only_now_n'][n]):>8.1f}")
    npit = np.mean([len(univ[ds[i]]) for i in anchors])
    print(f"유니버스 크기(앵커 평균): 그날 실제 {npit:,.0f} · '지금 가격 가드' 종목 중 그날 유니버스에 없던 비율 {np.mean(share):.1%}")
    k = np.array([a for a, _ in t50], dtype=float)
    g = np.array([b for _, b in t50], dtype=float)
    print(f"그날 us_mus_v0 top50 안의 증자·병합류 종목: 앵커 평균 {k.mean():.2f}개(0개인 앵커 {(k == 0).mean():.0%})"
          f" · 해당 종목 − 나머지 top50 = {np.nanmean(g) * 100:+.2f}%p (해당 종목이 있는 앵커 {int(np.isfinite(g).sum())}개)")


if __name__ == "__main__":
    main()
