# -*- coding: utf-8 -*-
"""
us_event_scan_followup_20261128.py — 8-K 항목 스캔의 통제 검증 (in-sample, 관측 전용)
=================================================================================
us_event_scan_20261128.py 에서 8-K 항목 그룹이 전부 유니버스 평균보다 낮게, 애널리스트 그룹은 전부 높게 나왔다
(애널리스트는 '다루는 종목' 평균과 비교하면 0). 방향이 한쪽으로 쏠리면 신호가 아니라 비교 대상의 구성 차이일 수 있다.
여기서는 같은 스캔을 비교 대상만 바꿔 다시 본다.
  frame U  : 가드 유니버스 전체 평균 대비(원 스캔과 같음 — 재현 확인용)
  frame F  : '공시하는 회사' 평균 대비 — 직전 1년에 8-K(실적 포함)를 1건 이상 낸 종목만(ETF·해외기업 6-K 제외 효과)
  frame FS : frame F 안에서 거래대금 5분위가 같은 종목 평균 대비(크기 통제)
  frame FSL: frame FS 를 거래대금 상위 절반(큰 종목)에서만
  placebo  : 항목과 무관하게 '직전 주에 비실적 8-K 를 낸 종목' 전체
  강건성  : frame U 를 앵커별 1%·99% 윈저화 평균 / 중앙값으로 다시 계산, 그룹 종목 중 유니버스 중앙값 아래인 비율
            (시세 DB 에 재조정이 덜 된 절벽·극단값이 있어 평균만으로는 못 믿는다 — fwd 최대 +6,800% 실측)
  사전 수익: 그룹의 직전 5·20거래일 수익 중앙값(유니버스 중앙값 대비) — 공시 전에 이미 오르내렸는지(단기 반전·악재 지속 구분용)
  신규 커버: 애널리스트 action='init'(원 스캔에 없던 1개 추가 확인) — 유니버스·'다루는 종목' 대비
  top50    : us_mus_v0 식 상위 50 에서 '직전 20거래일에 EXCL 항목 공시가 있는 종목'을 빼고 다음 순위로 채웠을 때의 h20 차이
정직성: 원 스캔과 같음(in-sample·생존편향 미보정·현재 cik 매핑·종가 체결·비용 0). EXCL 은 원 스캔 결과를 보고 고른 것이라
  top50 결과는 같은 표본에서 고르고 채점한 값이다 — 가설 정리용이지 근거가 아니다.

    python research/us_event_scan_followup_20261128.py --data-dir <정본 tar 를 푼 폴더>
"""
import argparse
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
H, STEP, NBOOT, SEED = 20, 5, 10_000, 20261128
BLOCK = H // STEP
ITEMS = ["3.02", "5.03", "1.01", "5.07", "8.01", "7.01", "2.03", "5.02"]   # 원 스캔에서 검정한 8개(평균 그룹 ≥10)
EXCL = ["3.02", "5.03", "1.01"]     # 원 스캔에서 효과가 가장 컸던 3개(지분 사모 발행·정관 변경·중요 계약) — 사후 선택
EXCL_LOOKBACK = H                   # 제외 필터가 보는 기간 = 보유 기간과 같은 20거래일
RARE = ["2.01", "1.02", "3.03", "3.01", "4.01"]   # 주간 그룹이 평균 2.5~7개라 원 스캔에서 빠진 항목 — 직전 20거래일로 넓혀 평균 ≥10 이 되면 본다
#   2.01 인수·처분 완료 · 1.02 중요 계약 해지 · 3.03 주주 권리 변경 · 3.01 상장 유지 기준 미달 통지 · 4.01 감사인 교체
FILER_DAYS = 365
N_Q = 5
TOP_N = 50
T0 = time.time()


def log(*a):
    print(f"[{time.time() - T0:5.0f}s]", *a, flush=True)


def ro(path):
    return sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)


def boot_ci(x, alphas, rng):
    x = np.asarray(x, dtype=float)
    x = x[np.isfinite(x)]
    n = len(x)
    if n < 2 * BLOCK:
        return {a: (np.nan, np.nan) for a in alphas}
    nblk = int(np.ceil(n / BLOCK))
    starts = rng.integers(0, n, (NBOOT, nblk))
    sel = (starts[:, :, None] + np.arange(BLOCK)[None, None, :]).reshape(NBOOT, -1)[:, :n] % n
    means = x[sel].mean(axis=1)
    return {a: (float(np.quantile(means, a / 2)), float(np.quantile(means, 1 - a / 2))) for a in alphas}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True)
    D = Path(ap.parse_args().data_dir)

    con = ro(D / "us_ohlcv.db")
    df = pd.read_sql_query("SELECT symbol, date, close, adj_close, volume FROM daily_ohlcv", con)
    C = df.pivot(index="symbol", columns="date", values="adj_close")
    RC = df.pivot(index="symbol", columns="date", values="close")
    V = df.pivot(index="symbol", columns="date", values="volume")
    del df
    cnt = C.notna().sum()
    keep = cnt >= 0.1 * cnt.max()
    C, RC, V = C.loc[:, keep], RC.loc[:, keep], V.loc[:, keep]
    ds = sorted(C.columns)
    Cf = C.ffill(axis=1, limit=H)
    anchors = list(range(63, len(ds) - H, STEP))
    log("시세", C.shape, "앵커", len(anchors))

    fc = ro(D / "us_fundamentals.db")
    ct = pd.read_sql_query("SELECT cik, ticker FROM cik_ticker", fc)
    ct["ticker"] = ct["ticker"].str.upper()
    ct = ct[ct["ticker"].isin(C.index)]
    ev = pd.read_sql_query("SELECT cik, accepted, filed, items, is_earnings FROM earnings_events WHERE form='8-K'", fc)
    ev["d8"] = ev["accepted"].where(ev["accepted"].fillna("") != "", ev["filed"]).str.slice(0, 10).str.replace("-", "", regex=False)
    ev = ev.merge(ct, on="cik")
    allf = ev[["ticker", "d8"]].drop_duplicates().sort_values("d8").reset_index(drop=True)       # 8-K 를 낸 사실(실적 포함)
    ne = ev[(ev["is_earnings"] == 0) & (ev["items"].fillna("") != "")].copy()
    ne["item"] = ne["items"].str.split(",")
    ne = ne.explode("item")
    ne["item"] = ne["item"].str.strip()
    ne = ne[ne["d8"] >= ds[0]][["ticker", "d8", "item"]].drop_duplicates().sort_values("d8").reset_index(drop=True)
    allf_d, ne_d = allf["d8"].to_numpy(), ne["d8"].to_numpy()
    etf = set()
    try:
        sc = ro(D / "us_seed.db")
        last = sc.execute("SELECT MAX(date) FROM listing_daily").fetchone()[0]
        etf = {s for (s,) in sc.execute("SELECT symbol FROM listing_daily WHERE date=? AND etf='Y'", (last,))}
    except sqlite3.OperationalError:
        pass

    ac = ro(D / "us_analyst.db")
    rc = pd.read_sql_query("SELECT symbol, ts, action FROM rating_changes", ac)
    rc["d8"] = rc["ts"].str.slice(0, 10).str.replace("-", "", regex=False)
    rc = rc[rc["symbol"].isin(C.index)].sort_values("d8").reset_index(drop=True)
    rc_d = rc["d8"].to_numpy()
    init = {"U": [], "COV": [], "n": []}
    pre = {n: {"p5": [], "p20": []} for n in ITEMS + ["placebo(비실적 8-K 아무거나)"]}
    rob = {n: {"wins": [], "median": [], "below": []} for n in ITEMS + ["placebo(비실적 8-K 아무거나)"]}

    names = ITEMS + ["placebo(비실적 8-K 아무거나)"]
    res = {f: {n: [] for n in names} for f in ("U", "F", "FS", "FSL")}
    rare = {f: {n: [] for n in RARE} for f in ("U", "FS", "n")}
    comp = []          # 유니버스 구성(앵커별): 전체·공시회사·ETF 수와 평균 수익
    top = []           # top50 제외 필터
    for i in anchors:
        t, lo = ds[i], ds[i - STEP]
        c63 = C[ds[i - 62:i + 1]]
        amt20 = (RC[ds[i - 19:i + 1]] * V[ds[i - 19:i + 1]]).mean(axis=1)
        ok = (RC[t] >= 5) & (amt20 >= 1e6) & (c63.notna().sum(axis=1) >= 60) & (c63.std(axis=1, ddof=1) > 0)
        idx = ok[ok].index
        fwd = (Cf.loc[idx, ds[i + H]] / C.loc[idx, t] - 1).dropna()
        lo_f = (pd.Timestamp(t) - pd.Timedelta(days=FILER_DAYS)).strftime("%Y%m%d")
        filers = set(allf.iloc[np.searchsorted(allf_d, lo_f, "left"):np.searchsorted(allf_d, t, "left")]["ticker"].unique())
        f_fwd = fwd[fwd.index.isin(filers)]
        q = pd.qcut(amt20.reindex(f_fwd.index).rank(method="first"), N_Q, labels=False)
        dm = f_fwd - f_fwd.groupby(q).transform("mean")            # 같은 거래대금 분위 평균 대비
        big = q[q >= N_Q / 2].index                                   # 거래대금 상위 절반(5분위 중 위 3개 → 중앙값 이상)
        comp.append({"t": t, "n_univ": len(fwd), "n_filer": len(f_fwd), "n_etf": int(fwd.index.isin(etf).sum()),
                     "r_univ": fwd.mean(), "r_filer": f_fwd.mean(), "r_etf": fwd[fwd.index.isin(etf)].mean(),
                     "r_other": fwd[~fwd.index.isin(filers) & ~fwd.index.isin(etf)].mean()})
        e = ne.iloc[np.searchsorted(ne_d, lo, "left"):np.searchsorted(ne_d, t, "left")]
        e20 = ne.iloc[np.searchsorted(ne_d, ds[i - H], "left"):np.searchsorted(ne_d, t, "left")]
        for it in RARE:
            syms = list(set(e20[e20["item"] == it]["ticker"]))
            m_u, m_s = fwd.reindex(syms).dropna(), dm.reindex(syms).dropna()
            rare["U"][it].append(m_u.mean() - fwd.mean() if len(m_u) else np.nan)
            rare["FS"][it].append(m_s.mean() if len(m_s) else np.nan)
            rare["n"][it].append(len(m_u))
        groups = {it: set(e[e["item"] == it]["ticker"]) for it in ITEMS}
        groups[names[-1]] = set(e["ticker"])
        wins = fwd.clip(fwd.quantile(0.01), fwd.quantile(0.99))
        p5 = (C.loc[fwd.index, t] / C.loc[fwd.index, ds[i - 5]] - 1)
        p20 = (C.loc[fwd.index, t] / C.loc[fwd.index, ds[i - 20]] - 1)
        r = rc.iloc[np.searchsorted(rc_d, lo, "left"):np.searchsorted(rc_d, t, "left")]
        cov = set(rc.iloc[np.searchsorted(rc_d, lo_f, "left"):np.searchsorted(rc_d, t, "left")]["symbol"].unique())
        m_i = fwd.reindex(list(set(r[r["action"] == "init"]["symbol"]))).dropna()
        init["U"].append(m_i.mean() - fwd.mean() if len(m_i) else np.nan)
        init["COV"].append(m_i.mean() - fwd[fwd.index.isin(cov)].mean() if len(m_i) else np.nan)
        init["n"].append(len(m_i))
        for n, syms in groups.items():
            m_u = fwd.reindex(list(syms)).dropna()
            pre[n]["p5"].append(p5.reindex(m_u.index).median() - p5.median() if len(m_u) else np.nan)
            pre[n]["p20"].append(p20.reindex(m_u.index).median() - p20.median() if len(m_u) else np.nan)
            rob[n]["wins"].append(wins.reindex(m_u.index).mean() - wins.mean() if len(m_u) else np.nan)
            rob[n]["median"].append(m_u.median() - fwd.median() if len(m_u) else np.nan)
            rob[n]["below"].append((m_u < fwd.median()).mean() if len(m_u) else np.nan)
            m_f = f_fwd.reindex(list(syms)).dropna()
            m_s = dm.reindex(list(syms)).dropna()
            m_l = dm.reindex([s for s in syms if s in big]).dropna()
            res["U"][n].append(m_u.mean() - fwd.mean() if len(m_u) else np.nan)
            res["F"][n].append(m_f.mean() - f_fwd.mean() if len(m_f) else np.nan)
            res["FS"][n].append(m_s.mean() if len(m_s) else np.nan)
            res["FSL"][n].append(m_l.mean() if len(m_l) else np.nan)

        if i >= 252:   # us_mus_v0 식(mom12 는 252거래일 필요) — us_orth_scan_20260906.mus_score 와 같은 계산
            w63 = c63.loc[idx].pct_change(axis=1, fill_method=None)
            F = pd.DataFrame(index=idx)
            F["mom12"] = C.loc[idx, ds[i - 21]] / C.loc[idx, ds[i - 252]] - 1
            F["upratio63"] = (w63 > 0).sum(axis=1) / w63.notna().sum(axis=1)
            F["size_amt"] = np.log10(amt20.reindex(idx).where(amt20.reindex(idx) > 0))
            sc_ = None
            for j, f in enumerate(["mom12", "upratio63", "size_amt"]):
                rk = F[f].rank(pct=True)
                core = rk.notna() if j == 0 else core
                sc_ = rk if sc_ is None else sc_ + rk.fillna(0.5)
            score = sc_.where(core).dropna().sort_values(ascending=False)
            score = score[score.index.isin(fwd.index)]
            x = ne.iloc[np.searchsorted(ne_d, ds[i - EXCL_LOOKBACK], "left"):np.searchsorted(ne_d, t, "left")]
            flagged = set(x[x["item"].isin(EXCL)]["ticker"])
            base = score.index[:TOP_N]
            filt = [s for s in score.index if s not in flagged][:TOP_N]
            hit = [s for s in base if s in flagged]
            top.append({"t": t, "n_flag": len(hit), "base": fwd[base].mean(), "filt": fwd[filt].mean(),
                        "flagged_ret": fwd[hit].mean() if hit else np.nan, "base_others": fwd[[s for s in base if s not in flagged]].mean()})
    log("앵커 루프 끝")

    rng = np.random.default_rng(SEED)
    alphas = (0.05, 0.05 / len(ITEMS))
    print(f"\n8-K 항목 그룹의 h20 초과수익(%p) — 비교 대상별. 앵커 {len(anchors)}개 · Bonferroni α=0.05/{len(ITEMS)} (** 표시)")
    print(f"{'항목':<30}" + "".join(f"{f:>26}" for f in ("U 전체 유니버스", "F 공시회사", "FS 공시회사·크기통제", "FSL 큰 절반만")))
    rows = []
    for n in names:
        line = f"{n:<30}"
        rec = {"item": n}
        for f in ("U", "F", "FS", "FSL"):
            x = np.array(res[f][n], dtype=float)
            ci = boot_ci(x, alphas, rng)
            m = np.nanmean(x)
            star = "**" if (ci[alphas[1]][0] > 0 or ci[alphas[1]][1] < 0) else ("* " if (ci[0.05][0] > 0 or ci[0.05][1] < 0) else "  ")
            line += f"  {m * 100:>+6.2f} [{ci[0.05][0] * 100:>+6.2f},{ci[0.05][1] * 100:>+6.2f}]{star}"
            rec.update({f"{f}_mean": m, f"{f}_lo95": ci[0.05][0], f"{f}_hi95": ci[0.05][1],
                        f"{f}_lo_bonf": ci[alphas[1]][0], f"{f}_hi_bonf": ci[alphas[1]][1]})
        rows.append(rec)
        print(line)
    pd.DataFrame(rows).to_csv(HERE / "us_event_scan_followup_frame_20261128.csv", index=False, encoding="utf-8-sig", float_format="%.5f")

    print("\n강건성(frame U)과 공시 전 수익 — 단위 %p")
    print(f"{'항목':<30}{'윈저화 평균':>10}{'중앙값 차':>10}{'중앙값 아래':>10}{'직전 5일':>10}{'직전 20일':>10}")
    for n in names:
        print(f"{n:<30}{np.nanmean(rob[n]['wins']) * 100:>+12.2f}{np.nanmean(rob[n]['median']) * 100:>+12.2f}{np.nanmean(rob[n]['below']):>12.0%}"
              f"{np.nanmean(pre[n]['p5']) * 100:>+12.2f}{np.nanmean(pre[n]['p20']) * 100:>+12.2f}")
    ci_u, ci_c = boot_ci(init["U"], (0.05,), rng)[0.05], boot_ci(init["COV"], (0.05,), rng)[0.05]
    print(f"애널리스트 신규 커버(init, 평균 {np.mean(init['n']):.1f}종목/앵커): 유니버스 대비 {np.nanmean(init['U']) * 100:+.2f}%p [{ci_u[0] * 100:+.2f},{ci_u[1] * 100:+.2f}]"
          f" · 다루는 종목 대비 {np.nanmean(init['COV']) * 100:+.2f}%p [{ci_c[0] * 100:+.2f},{ci_c[1] * 100:+.2f}]")
    cp = pd.DataFrame(comp)
    a_r = (0.05, 0.05 / len(RARE))
    print(f"\n희귀 항목(직전 {H}거래일 안 공시, Bonferroni α=0.05/{len(RARE)}) — h20 초과수익 %p")
    for it in RARE:
        out = f"  {it}  평균 {np.mean(rare['n'][it]):>5.1f}종목"
        for f, lab in (("U", "유니버스 대비"), ("FS", "공시회사·크기통제")):
            x = np.array(rare[f][it], dtype=float)
            ci = boot_ci(x, a_r, rng)
            star = "**" if (ci[a_r[1]][0] > 0 or ci[a_r[1]][1] < 0) else ("* " if (ci[0.05][0] > 0 or ci[0.05][1] < 0) else "  ")
            out += f" | {lab} {np.nanmean(x) * 100:>+6.2f} [{ci[0.05][0] * 100:>+6.2f},{ci[0.05][1] * 100:>+6.2f}]{star}"
        print(out)
    print(f"\n유니버스 구성(앵커 평균): 전체 {cp.n_univ.mean():,.0f} · 공시회사 {cp.n_filer.mean():,.0f} · ETF {cp.n_etf.mean():,.0f}"
          f" | h20 평균 수익: 전체 {cp.r_univ.mean() * 100:+.2f}% · 공시회사 {cp.r_filer.mean() * 100:+.2f}% · ETF {cp.r_etf.mean() * 100:+.2f}%"
          f" · 그 밖(해외기업 등) {cp.r_other.mean() * 100:+.2f}%   (ETF 표시는 현재 상장목록 기준)")

    tp = pd.DataFrame(top)
    d = (tp["filt"] - tp["base"]).to_numpy()
    ci = boot_ci(d, (0.05,), rng)[0.05]
    g = (tp["flagged_ret"] - tp["base_others"]).to_numpy()
    cig = boot_ci(g, (0.05,), rng)[0.05]
    print(f"\ntop50 제외 필터(직전 {EXCL_LOOKBACK}거래일에 {EXCL} 공시가 있으면 빼고 다음 순위로 채움) — 앵커 {len(tp)}개 {tp.t.iloc[0]}→{tp.t.iloc[-1]}")
    print(f"  top50 중 해당 종목 수: 평균 {tp.n_flag.mean():.2f}개(0개인 앵커 {(tp.n_flag == 0).mean():.0%})")
    print(f"  Δ(필터 − 기본) h20 = {np.nanmean(d) * 100:+.3f}%p, 95% CI [{ci[0] * 100:+.3f}, {ci[1] * 100:+.3f}]")
    print(f"  top50 안에서 해당 종목 − 나머지 = {np.nanmean(g) * 100:+.2f}%p, 95% CI [{cig[0] * 100:+.2f}, {cig[1] * 100:+.2f}] (해당 종목이 있는 앵커 {int(np.isfinite(g).sum())}개)")
    tp.to_csv(HERE / "us_event_scan_followup_top50_20261128.csv", index=False, encoding="utf-8-sig", float_format="%.5f")


if __name__ == "__main__":
    main()
