# -*- coding: utf-8 -*-
"""
verdict_us_round1.py — 2026년 가을 1라운드 OOS 판정(사전등록 research/PREREGISTER_us_20260927.md §2~§5 그대로)
=====================================================================================================
읽기 전용. 정본 Release tar 를 푼 폴더의 us_ohlcv.db(score_daily·daily_ohlcv·adjust_queue)·us_market.db(SPX)만 읽는다.
스펙 값은 전부 사전등록에서 온 것이다 — 이 파일에서 고르거나 조정하지 않는다(바꾸려면 새 라운드).

    python research/verdict_us_round1.py --data-dir <us_ohlcv.db 가 있는 폴더> [--out research/VERDICT_us_round1_YYYYMMDD.md]
    python research/verdict_us_round1.py --data-dir <폴더> --allow-incomplete     # D39 수집 전 시험 실행(판정 아님)

주 통계량(§3-1): 앵커 D0…D19 각각에서
    x_a = (top50 동일가중 h20 수익률 − 왕복 비용 0.2%p) − (그 앵커에 us_mus_v0 가 점수 매긴 전 종목 동일가중 h20 수익률)
  수익률 = adj_close[D(a+20)] / adj_close[D(a)] − 1, 종가 체결. h20 종가가 없는 종목은 창 안의 마지막 거래 종가로 청산(제외 안 함).
채택(§3-2): stationary block bootstrap(평균 블록 20, 10,000회, 시드 20261001) 양측 97.5% CI 하한 > 0 그리고 x̄ > 0.
보조(§3-3, 기록만): Δ(ind − v1)·CI, day-IC, 적중률, 절대수익, SPX 대비, 최대낙폭, 유니버스 크기, 게이트 통과 업종 수.
무효(§4): 점수 빈 날 > 3 · 유니버스 ±30% 이상 변동일 > 5 · 판정 입력 경로 변경(git log 첨부).
"""
import argparse
import datetime as dt
import sqlite3
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent

# ── 사전등록 스펙(§1~§4) — 값의 출처는 PREREGISTER_us_20260927.md, 여기서 바꾸지 않는다 ──
D0 = "20261001"                 # §2
D19_EXPECT, D39_EXPECT = "20261028", "20261125"   # §2 달력(대조용 — 다르면 경고)
N_OOS, N_ANCHOR, H = 40, 20, 20                   # §2
COST_ROUNDTRIP = 0.002          # §3-1 왕복 0.2%p
BLOCK_MEAN, N_BOOT, SEED = 20, 10_000, 20261001   # §3-2
N_CANDIDATES = 2
ALPHA = 0.05 / N_CANDIDATES     # §3-2 Bonferroni → 97.5% CI
ALPHA_AUX = 0.05                # 보조 지표 CI(채택 판단에 안 씀)
TOP_N = 50
MODELS = {"us_mus_v1": "us_mus_v0", "us_mus_v1_ind": "us_mus_v1_ind"}   # 후보 id → score_daily model(§1)
BASE_MODEL = "us_mus_v0"        # 가드 유니버스 = 이 모델이 그 앵커에 점수 매긴 종목 전체
VOID_EMPTY_DAYS, VOID_UNIV_FRAC, VOID_UNIV_DAYS = 3, 0.30, 5            # §4
IND_TOP_Q, IND_MIN_N = 0.8, 10  # §1 업종 게이트 정의(us_page_data 와 같은 값 — 게이트 통과 업종 수 재구성용)
STRAY_FRAC = 0.1                # us_calendar v09: 시세 행수가 창 최대의 10% 미만인 날은 거래일이 아니다(지수 봉이 없을 때)
PREREG_COMMIT = "0467adc"       # 사전등록 확정 커밋(v20·v21·사전등록)
INPUT_PATH_FILES = ["us_ohlcv_collector.py", "us_page_data.py", "us_calendar.py", "us_price_repair.py",
                    "us_market_series.py", "us_rotate_collector.py", "requirements.txt"]   # §4


def sb_indices(n, block_mean, n_boot, seed):
    """stationary bootstrap(Politis·Romano) 인덱스 (n_boot × n). 첫 칸은 균등 추출, 이후 매 칸 확률 1/block_mean 로 새 블록
    (균등 추출), 아니면 직전 인덱스 + 1(끝에서 처음으로 순환). 모든 통계량이 같은 인덱스를 쓴다(짝 비교·재현)."""
    rng = np.random.default_rng(seed)
    p = 1.0 / block_mean
    idx = np.empty((n_boot, n), dtype=np.int64)
    idx[:, 0] = rng.integers(0, n, n_boot)
    for t in range(1, n):
        jump = rng.random(n_boot) < p
        idx[:, t] = np.where(jump, rng.integers(0, n, n_boot), (idx[:, t - 1] + 1) % n)
    return idx


def boot_ci(x, idx, alpha):
    """x(앵커 순서) 평균의 percentile CI. NaN 이 있으면 계산하지 않는다(None)."""
    x = np.asarray(x, dtype=float)
    if len(x) == 0 or np.isnan(x).any():
        return None
    means = x[idx].mean(axis=1)
    lo, hi = np.quantile(means, [alpha / 2, 1 - alpha / 2])
    return float(lo), float(hi)


def spearman(a, b):
    m = a.notna() & b.notna()
    if m.sum() < 3:
        return np.nan
    ra, rb = a[m].rank(), b[m].rank()
    if ra.std() == 0 or rb.std() == 0:
        return np.nan
    return float(np.corrcoef(ra, rb)[0, 1])


def max_drawdown(v):
    v = np.asarray(v, dtype=float)
    return float((v / np.maximum.accumulate(v) - 1).min())


def gate_industries(base_rows):
    """업종 게이트 통과 업종 수 재구성(score_daily 의 그날 industry·mom12 — us_page_data 와 같은 규칙: 구성 ≥10, EW mom12 상위 20%)."""
    g = base_rows.dropna(subset=["industry", "mom12"])
    g = g[g["industry"] != ""].groupby("industry")["mom12"].agg(["mean", "count"])
    g = g[g["count"] >= IND_MIN_N]
    if g.empty:
        return 0
    return int((g["mean"] >= g["mean"].quantile(IND_TOP_Q)).sum())


def compute(days, px, scores, spx, pending=()):
    """days: D0 부터의 거래일 목록(오름차순). px: adj_close(행=날짜, 열=심볼). scores: score_daily 행(model,date,symbol,rank,mom12,industry).
    spx: 날짜→종가. pending: 판정 시점 재조정 대기 심볼(민감도용). 반환: 앵커 표(DataFrame)와 요약(dict)."""
    px = px.reindex(days)
    n_anchor = max(0, min(N_ANCHOR, len(days) - H))
    by = {(m, d): g for (m, d), g in scores.groupby(["model", "date"])}
    rows = []
    for i in range(n_anchor):
        a, e = days[i], days[i + H]
        win = px.iloc[i:i + H + 1].ffill()          # 창 안 마지막 거래 종가로 청산(§3-1)
        p0, p1 = px.loc[a], win.loc[e]
        ret = p1 / p0 - 1
        base = by.get((BASE_MODEL, a))
        if base is None or base.empty:
            rows.append({"anchor": a, "end": e, "n_univ": 0})
            continue
        univ = base["symbol"].tolist()
        r_u = ret.reindex(univ)
        rec = {"anchor": a, "end": e, "n_univ": len(univ),
               "n_no_start": int(r_u.isna().sum()),                                  # 앵커 종가 없음 → 계산 불가(평균에서 빠짐)
               "n_stale_end": int((px.loc[e].reindex(univ).isna() & r_u.notna()).sum()),   # h20 종가 없음 → 마지막 종가 청산
               "ret_univ": float(r_u.mean()),
               "ret_univ_ex": float(r_u[~r_u.index.isin(pending)].mean()),
               "mdd_univ": max_drawdown((win.reindex(columns=univ) / p0.reindex(univ)).mean(axis=1)),
               "ret_spx": float(spx[e] / spx[a] - 1) if a in spx.index and e in spx.index else np.nan,
               "ic": spearman(-base.set_index("symbol")["rank"].astype(float), r_u),
               "n_gate_ind": gate_industries(base)}
        for cid, model in MODELS.items():
            g = by.get((model, a))
            top = [] if g is None else g[g["rank"] <= TOP_N]["symbol"].tolist()
            r_t = ret.reindex(top)
            rec[f"n_{cid}"] = len(top)
            rec[f"ret_{cid}"] = float(r_t.mean()) if top else np.nan
            rec[f"x_{cid}"] = rec[f"ret_{cid}"] - COST_ROUNDTRIP - rec["ret_univ"]
            r_tx = r_t[~r_t.index.isin(pending)]
            rec[f"x_ex_{cid}"] = (float(r_tx.mean()) - COST_ROUNDTRIP - rec["ret_univ_ex"]) if len(r_tx) else np.nan
            rec[f"n_pending_{cid}"] = int(r_t.index.isin(pending).sum())
            rec[f"mdd_{cid}"] = max_drawdown((win.reindex(columns=top) / p0.reindex(top)).mean(axis=1)) if top else np.nan
        rows.append(rec)
    A = pd.DataFrame(rows)
    out = {"n_anchor": n_anchor, "models": {}}
    if n_anchor == 0 or "ret_univ" not in A:
        return A, out
    idx = sb_indices(n_anchor, BLOCK_MEAN, N_BOOT, SEED)
    out["boot_same_as_mean"] = float((np.diff(idx, axis=1) % n_anchor == 1).all(axis=1).mean())   # 한 블록(순환 이동)인 재표본 비율
    for cid in MODELS:
        x = A[f"x_{cid}"].to_numpy(dtype=float)
        ci = boot_ci(x, idx, ALPHA)
        xbar = float(np.nanmean(x)) if not np.isnan(x).all() else np.nan
        out["models"][cid] = {
            "xbar": xbar, "ci": ci, "n": int((~np.isnan(x)).sum()),
            "adopt": bool(ci is not None and ci[0] > 0 and xbar > 0),
            "hit": float((x > 0).mean()), "ret_top": float(A[f"ret_{cid}"].mean()),
            "vs_spx": float((A[f"ret_{cid}"] - COST_ROUNDTRIP - A["ret_spx"]).mean()),
            "mdd_mean": float(A[f"mdd_{cid}"].mean()), "mdd_worst": float(A[f"mdd_{cid}"].min()),
            "xbar_ex_pending": float(A[f"x_ex_{cid}"].mean()), "n_pending": int(A[f"n_pending_{cid}"].sum()),
            "n_min": int(A[f"n_{cid}"].min()), "n_max": int(A[f"n_{cid}"].max())}
    d = (A["x_us_mus_v1_ind"] - A["x_us_mus_v1"]).to_numpy(dtype=float)
    out["delta"] = {"mean": float(np.nanmean(d)), "ci": boot_ci(d, idx, ALPHA_AUX)}
    out["ic"] = {"mean": float(A["ic"].mean()), "ci": boot_ci(A["ic"], idx, ALPHA_AUX)}
    out["univ"] = {"ret": float(A["ret_univ"].mean()), "spx": float(A["ret_spx"].mean()),
                   "mdd_mean": float(A["mdd_univ"].mean()), "mdd_worst": float(A["mdd_univ"].min()),
                   "n_stale_end": int(A["n_stale_end"].sum()), "n_no_start": int(A["n_no_start"].sum())}
    return A, out


def void_checks(days, scores):
    """§4 의 데이터 조건 2개. 반환 dict(각 항목 값·한도·통과 여부)."""
    oos = days[:N_OOS]
    cnt = scores.groupby(["model", "date"]).size()
    empty = {m: [d for d in oos if cnt.get((m, d), 0) == 0] for m in set(MODELS.values())}
    n_empty = len({d for v in empty.values() for d in v})
    n0 = cnt.get((BASE_MODEL, oos[0]), 0) if oos else 0
    big = [d for d in oos if n0 and abs(cnt.get((BASE_MODEL, d), 0) / n0 - 1) >= VOID_UNIV_FRAC]
    sizes = [cnt.get((BASE_MODEL, d), 0) for d in oos]
    return {"empty": empty, "n_empty": n_empty, "empty_ok": n_empty <= VOID_EMPTY_DAYS,
            "univ_d0": int(n0), "univ_min": int(min(sizes, default=0)), "univ_max": int(max(sizes, default=0)),
            "univ_big_days": big, "univ_ok": len(big) <= VOID_UNIV_DAYS}


def load(data_dir):
    data_dir = Path(data_dir)
    con = sqlite3.connect(f"file:{(data_dir / 'us_ohlcv.db').as_posix()}?mode=ro", uri=True)
    mcon = sqlite3.connect(f"file:{(data_dir / 'us_market.db').as_posix()}?mode=ro", uri=True)
    spx = pd.read_sql_query("SELECT date, close FROM market_daily WHERE series='SPX' AND date>=? ORDER BY date",
                            mcon, params=(D0,)).set_index("date")["close"]
    counts = pd.read_sql_query("SELECT date, COUNT(*) n FROM daily_ohlcv WHERE date>=? GROUP BY date", con,
                               params=(D0,)).set_index("date")["n"]
    # 거래일(us_calendar v21 과 같은 뜻): 지수 봉이 있으면 거래일, 없으면 시세 행수가 창 최대의 10% 이상인 날
    days = sorted(set(spx.index) | set(counts[counts >= STRAY_FRAC * counts.max()].index)) if len(counts) else []
    days = days[:N_OOS]
    scores = pd.read_sql_query(
        "SELECT model, date, symbol, rank, mom12, industry FROM score_daily WHERE date>=? AND model IN (?,?)",
        con, params=(D0, *sorted(set(MODELS.values()))))
    last = days[-1] if days else D0
    px = pd.read_sql_query("SELECT symbol, date, adj_close FROM daily_ohlcv WHERE date>=? AND date<=?", con,
                           params=(D0, last)).pivot(index="date", columns="symbol", values="adj_close")
    try:
        pending = {s for (s,) in con.execute("SELECT symbol FROM adjust_queue")}
    except sqlite3.OperationalError:
        pending = set()
    meta = {"max_price_date": con.execute("SELECT MAX(date) FROM daily_ohlcv").fetchone()[0],
            "max_score_date": con.execute("SELECT MAX(date) FROM score_daily WHERE model=?", (BASE_MODEL,)).fetchone()[0],
            "n_spx_days": int(len(spx)), "counts": counts.reindex(days).fillna(0).astype(int).to_dict()}
    return days, px, scores, spx, pending, meta


def git_path_log():
    try:
        r = subprocess.run(["git", "log", "--oneline", f"{PREREG_COMMIT}..HEAD", "--", *INPUT_PATH_FILES],
                           cwd=HERE.parent, capture_output=True, text=True, encoding="utf-8", timeout=60)
        head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=HERE.parent, capture_output=True,
                              text=True, timeout=60).stdout.strip()
        return r.returncode == 0, r.stdout.strip(), head
    except Exception as e:
        return False, f"git 실행 실패: {e}", "?"


def pct(v, d=2):
    return "—" if v is None or (isinstance(v, float) and np.isnan(v)) else f"{v * 100:+.{d}f}%"


def ci_txt(ci, fmt):
    return "—" if ci is None else f"[{fmt(ci[0])}, {fmt(ci[1])}]"


def render(days, A, S, V, meta, pending, complete, today):
    L = []
    w = L.append
    title = "VERDICT — 2026년 가을 1라운드 OOS 판정" if complete else "시험 실행(판정 아님) — 1라운드 OOS 중간 계산"
    w(f"# {title} ({today})\n")
    w("> 스펙: research/PREREGISTER_us_20260927.md §2~§5 그대로. 생성: `python research/verdict_us_round1.py` (읽기 전용).")
    w(f"> 데이터: 정본 Release tar · 시세 최신일 {meta['max_price_date']} · us_mus_v0 점수 최신일 {meta['max_score_date']} · "
      f"OOS 거래일 {len(days)}/{N_OOS}일({days[0] if days else '—'}~{days[-1] if days else '—'}) · 앵커 {S['n_anchor']}/{N_ANCHOR}개.")
    if not complete:
        w("> **D39 까지의 데이터가 모이지 않았다 — 아래 수치는 스크립트 동작 확인용이며 판정이 아니다.**")
    w("")
    cal_ok = len(days) >= N_OOS and days[N_ANCHOR - 1] == D19_EXPECT and days[N_OOS - 1] == D39_EXPECT
    void_data = not (V["empty_ok"] and V["univ_ok"])
    ok_git, gitlog, head = git_path_log()
    void_path = bool(gitlog) or not ok_git
    void = void_data or void_path

    w("## 1. 결론\n")
    if complete:
        if void:
            w("**판정 불가(void)** — §2 의 무효 점검 참조.\n")
        w("| 후보 | x̄(비용 차감 초과, n) | 97.5% CI(블록 부트스트랩) | CI 하한 > 0 | 방향(+) | 결론 |")
        w("|---|---|---|---|---|---|")
        for cid, m in S["models"].items():
            ci = m["ci"]
            res = "판정 불가(void)" if void else ("**채택**" if m["adopt"] else "**채택 안 함**")
            w(f"| `{cid}` | {pct(m['xbar'])}p (n={m['n']}) | {ci_txt(ci, lambda v: pct(v) + 'p')} | "
              f"{'—' if ci is None else ('예' if ci[0] > 0 else '아니오')} | {'예' if m['xbar'] > 0 else '아니오'} | {res} |")
        w("")
        w("- x̄ = 앵커 20개의 `(top50 동일가중 h20 − 왕복 0.2%p) − 가드 유니버스 동일가중 h20` 평균. 수익률은 adj_close 종가.")
        w("- 채택은 두 조건(CI 하한 > 0 · 방향 +) 모두 충족일 때만. 경계값은 '기움'이지 채택이 아니다(사전등록 §3-2).")
    else:
        w("판정 없음(데이터 미완).")
    w("")

    w("## 2. 무효(void) 점검 (§4)\n")
    w(f"- 거래일 달력: D19 = {days[N_ANCHOR - 1] if len(days) >= N_ANCHOR else '—'}(사전등록 {D19_EXPECT}) · "
      f"D39 = {days[N_OOS - 1] if len(days) >= N_OOS else '—'}(사전등록 {D39_EXPECT}) → {'일치' if cal_ok else '**불일치/미완**'}")
    for m, ds in V["empty"].items():
        w(f"- score_daily 빈 날 `{m}`: {len(ds)}일{(' ' + str(ds)) if ds else ''}")
    w(f"  - 합계 {V['n_empty']}일 (한도 {VOID_EMPTY_DAYS}일 초과 시 void) → {'통과' if V['empty_ok'] else '**void**'}")
    w(f"- 가드 유니버스 크기: D0 {V['univ_d0']:,} · 범위 {V['univ_min']:,}~{V['univ_max']:,} · D0 대비 ±{VOID_UNIV_FRAC:.0%} 이상인 날 "
      f"{len(V['univ_big_days'])}일 (한도 {VOID_UNIV_DAYS}일 초과 시 void) → {'통과' if V['univ_ok'] else '**void**'}")
    w(f"- 판정 입력 경로 변경: `git log --oneline {PREREG_COMMIT}..HEAD({head}) -- {' '.join(INPUT_PATH_FILES)}` → "
      + ("**출력 없음(변경 없음)** → 통과" if ok_git and not gitlog else "**변경 있음/확인 실패 → void 검토**"))
    if gitlog:
        w("```\n" + gitlog + "\n```")
    w("")

    if S["n_anchor"]:
        w("## 3. 보조 지표 (§3-3 — 기록만, 채택 판단에 쓰지 않음)\n")
        dl, ic, u = S["delta"], S["ic"], S["univ"]
        w(f"- Δ = x̄(us_mus_v1_ind) − x̄(us_mus_v1) = {pct(dl['mean'])}p, 95% CI "
          f"{ci_txt(dl['ci'], lambda v: pct(v) + 'p')} (업종 게이트의 증분)")
        w(f"- day-IC(us_mus_v0 순위 vs h20, 스피어만) 평균 {ic['mean']:+.3f}, 95% CI "
          f"{ci_txt(ic['ci'], lambda v: format(v, '+.3f'))}")
        w(f"- 가드 유니버스 동일가중 h20 평균 {pct(u['ret'])} · SPX h20 평균 {pct(u['spx'])} · 유니버스 최대낙폭 평균 {pct(u['mdd_mean'])} / 최악 {pct(u['mdd_worst'])}")
        w("")
        w("| 후보 | 적중률(x_a>0) | top50 절대수익(비용 전) | SPX 대비(비용 후) | 최대낙폭 평균 / 최악 | 바스켓 종목 수 |")
        w("|---|---|---|---|---|---|")
        for cid, m in S["models"].items():
            w(f"| `{cid}` | {m['hit']:.0%} | {pct(m['ret_top'])} | {pct(m['vs_spx'])}p | {pct(m['mdd_mean'])} / {pct(m['mdd_worst'])} | {m['n_min']}~{m['n_max']} |")
        w("")
        w("### 앵커별\n")
        w("| # | 앵커 | 종료 | 유니버스 | 게이트 업종 | 유니버스 h20 | SPX h20 | v1 top50 | x(v1) | ind 수 | ind top | x(ind) | IC |")
        w("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
        for i, r in A.iterrows():
            if not r.get("n_univ"):
                w(f"| D{i} | {r['anchor']} | {r['end']} | 0 | — | — | — | — | — | — | — | — | — |")
                continue
            w(f"| D{i} | {r['anchor']} | {r['end']} | {int(r['n_univ']):,} | {int(r['n_gate_ind'])} | {pct(r['ret_univ'])} | {pct(r['ret_spx'])} | "
              f"{pct(r['ret_us_mus_v1'])} | {pct(r['x_us_mus_v1'])}p | {int(r['n_us_mus_v1_ind'])} | {pct(r['ret_us_mus_v1_ind'])} | "
              f"{pct(r['x_us_mus_v1_ind'])}p | {r['ic']:+.3f} |")
        w("")

        w("## 4. 데이터 점검과 한계\n")
        w(f"- h20 종가가 없어 창 안 마지막 종가로 청산한 (앵커, 종목) {u['n_stale_end']:,}건 · 앵커 종가가 없어 계산에서 빠진 {u['n_no_start']:,}건"
          f"(유니버스 연인원 {int(A['n_univ'].sum()):,}건 중).")
        w(f"- 판정 시점 재조정 대기(adjust_queue) {len(pending)}종목 — 분할·배당 반영이 덜 된 adj_close 가 섞였을 수 있다. top50 연인원 중 해당 "
          + " · ".join(f"`{cid}` {m['n_pending']}건" for cid, m in S["models"].items())
          + ". 민감도(해당 종목을 바스켓·유니버스에서 뺀 x̄, 참고): "
          + " · ".join(f"`{cid}` {pct(m['xbar_ex_pending'])}p" for cid, m in S["models"].items()) + ".")
        w(f"- 블록 부트스트랩의 성질: 평균 블록 길이({BLOCK_MEAN})가 표본 길이({S['n_anchor']})와 같아 재표본의 {S['boot_same_as_mean']:.0%} 가 원 계열의 순환 이동"
          "(평균이 x̄ 와 같음)이다. 앵커 창이 20거래일씩 겹쳐 독립 표본은 사실상 2개 안팎이므로, 이 CI 는 불확실성을 좁게 잡는 쪽이다. "
          "사전등록 스펙 그대로 계산했고 해석은 그 한계 안에서 한다.")
        w("- 한 번의 40거래일 창(한 가지 시장 국면)이다. 비용은 왕복 0.2%p 가정, 종가 체결 가정(슬리피지·세금 미반영). sector_cache 는 그날 기록한 분류.")
        w("- 조회 전용 · 자동매매 없음. 채택 여부와 무관하게 표시는 관측이다.")
    return "\n".join(L) + "\n", void


def main():
    ap = argparse.ArgumentParser(description="1라운드 OOS 판정(사전등록 스펙 그대로, 읽기 전용)")
    ap.add_argument("--data-dir", required=True, help="정본 tar 를 푼 폴더(us_ohlcv.db · us_market.db)")
    ap.add_argument("--out", default=None, help="결과 md 경로(기본: research/VERDICT_us_round1_<오늘>.md)")
    ap.add_argument("--allow-incomplete", action="store_true", help="D39 수집 전 시험 실행(판정 아님)")
    args = ap.parse_args()
    days, px, scores, spx, pending, meta = load(args.data_dir)
    complete = len(days) >= N_OOS and all(meta["counts"].get(d, 0) > 0 for d in days[:N_OOS])
    if not complete and not args.allow_incomplete:
        print(f"❌ OOS 거래일 {len(days)}/{N_OOS}일(시세 최신일 {meta['max_price_date']}) — D39 수집 전이다. "
              "시험 실행은 --allow-incomplete.")
        sys.exit(2)
    A, S = compute(days, px, scores, spx, pending)
    V = void_checks(days, scores)
    today = dt.date.today().strftime("%Y%m%d")
    text, void = render(days, A, S, V, meta, pending, complete, today)
    out = Path(args.out) if args.out else HERE / f"VERDICT_us_round1_{today}.md"
    out.write_text(text, encoding="utf-8")
    print(text)
    print(f"저장: {out}")


if __name__ == "__main__":
    main()
