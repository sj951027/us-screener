"""판정 스크립트(research/verdict_us_round1.py) 산식 검증 — 손으로 계산되는 합성 데이터(네트워크·DB 0).
`python research/test_verdict_round1_20261126.py`"""
import pathlib
import sys
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import verdict_us_round1 as v  # noqa: E402

DAYS = [f"D{i:02d}" for i in range(40)]          # 가짜 거래일 40개(문자열 정렬 = 시간순)
G = {"A": 0.010, "B": 0.005, "C": 0.000, "E": -0.005}   # 종목별 하루 수익률(일정) → h20 = (1+g)^20 − 1, 앵커와 무관


def prices(n_days=40, dead=None):
    """dead = (심볼, 마지막 거래 인덱스) — 그 뒤는 시세 없음(상장폐지)."""
    px = pd.DataFrame({s: [(1 + g) ** t for t in range(n_days)] for s, g in G.items()}, index=DAYS[:n_days])
    if dead:
        px.loc[px.index[dead[1] + 1:], dead[0]] = np.nan
    return px


def score_rows(days, order=("A", "B", "C", "E"), ind=("A",), skip=()):
    rows = []
    for d in days:
        if d in skip:
            continue
        rows += [("us_mus_v0", d, s, r, 0.1, "Ind") for r, s in enumerate(order, 1)]
        rows += [("us_mus_v1_ind", d, s, r, 0.1, "Ind") for r, s in enumerate(ind, 1)]
    return pd.DataFrame(rows, columns=["model", "date", "symbol", "rank", "mom12", "industry"])


H20 = {s: (1 + g) ** 20 - 1 for s, g in G.items()}
UNIV = float(np.mean(list(H20.values())))


class Verdict(unittest.TestCase):
    def test_bootstrap_indices(self):
        a = v.sb_indices(20, 20, 500, 123)
        self.assertEqual(a.shape, (500, 20))
        self.assertTrue(((a >= 0) & (a < 20)).all())
        self.assertTrue((a == v.sb_indices(20, 20, 500, 123)).all())           # 시드 재현
        cont = (np.diff(a, axis=1) % 20 == 1).mean()                           # 이어 붙는 비율 ≈ 1 − 1/20 (+ 우연히 다음 칸으로 뛴 경우)
        self.assertTrue(0.93 < cont < 0.98, cont)

    def test_main_statistic_by_hand(self):
        with patch.object(v, "TOP_N", 2):                                       # top2 = A·B
            A, S = v.compute(DAYS, prices(), score_rows(DAYS), pd.Series(dtype=float))
        self.assertEqual(S["n_anchor"], 20)
        x_v1 = (H20["A"] + H20["B"]) / 2 - 0.002 - UNIV
        x_ind = H20["A"] - 0.002 - UNIV
        m1, m2 = S["models"]["us_mus_v1"], S["models"]["us_mus_v1_ind"]
        self.assertAlmostEqual(m1["xbar"], x_v1, places=12)
        self.assertAlmostEqual(m2["xbar"], x_ind, places=12)
        self.assertAlmostEqual(m1["ci"][0], x_v1, places=12)                    # 모든 앵커가 같은 값 → CI 가 한 점
        self.assertTrue(m1["adopt"] and m2["adopt"])
        self.assertAlmostEqual(S["delta"]["mean"], x_ind - x_v1, places=12)
        self.assertAlmostEqual(A["ic"].iloc[0], 1.0, places=12)                 # 순위 순서 = 수익률 순서
        self.assertEqual(m1["hit"], 1.0)

    def test_cost_makes_zero_edge_negative(self):
        with patch.object(v, "TOP_N", 50):                                      # top50 = 유니버스 전체 → 초과 0 − 비용
            _, S = v.compute(DAYS, prices(), score_rows(DAYS), pd.Series(dtype=float))
        m = S["models"]["us_mus_v1"]
        self.assertAlmostEqual(m["xbar"], -0.002, places=12)
        self.assertFalse(m["adopt"])

    def test_delisted_liquidated_at_last_close(self):
        px = prices(dead=("A", 10))                                             # A 는 10번째 날이 마지막 거래
        with patch.object(v, "TOP_N", 1):
            A, S = v.compute(DAYS, px, score_rows(DAYS[:11] + DAYS[20:], ind=("A",)), pd.Series(dtype=float))
        r0 = A.iloc[0]                                                          # 앵커 D00 → 종료 D20: A 는 D10 종가로 청산
        self.assertAlmostEqual(r0["ret_us_mus_v1"], (1 + G["A"]) ** 10 - 1, places=12)
        self.assertEqual(r0["n_stale_end"], 1)
        self.assertEqual(r0["n_no_start"], 0)
        self.assertEqual(A.iloc[15]["n_univ"], 0)                               # 점수가 없는 앵커(D15)는 빈 줄
        self.assertIsNone(S["models"]["us_mus_v1"]["ci"])                       # 빈 앵커가 있으면 CI 를 만들지 않는다
        self.assertFalse(S["models"]["us_mus_v1"]["adopt"])

    def test_incomplete_window_uses_closed_anchors_only(self):
        A, S = v.compute(DAYS[:30], prices(30), score_rows(DAYS[:30]), pd.Series(dtype=float))
        self.assertEqual((S["n_anchor"], len(A)), (10, 10))

    def test_void_checks(self):
        sc = score_rows(DAYS, skip=("D03", "D04"))
        V = v.void_checks(DAYS, sc)
        self.assertEqual(V["n_empty"], 2)
        self.assertTrue(V["empty_ok"])
        V = v.void_checks(DAYS, score_rows(DAYS, skip=("D03", "D04", "D05", "D06")))
        self.assertFalse(V["empty_ok"])                                         # 4일 > 한도 3일
        big = pd.concat([score_rows(DAYS[:34]), score_rows(DAYS[34:], order=("A", "B"))])   # 마지막 6일 유니버스 −50%
        V = v.void_checks(DAYS, big)
        self.assertEqual(len(V["univ_big_days"]), 6)
        self.assertFalse(V["univ_ok"])

    def test_gate_industries(self):
        base = pd.DataFrame({"industry": [f"I{i}" for i in range(10) for _ in range(10)] + ["small"] * 3,
                             "mom12": [i / 10 for i in range(10) for _ in range(10)] + [9.0] * 3})
        self.assertEqual(v.gate_industries(base), 2)                            # 구성 ≥10 인 10개 업종의 상위 20%(작은 업종은 제외)


if __name__ == "__main__":
    unittest.main()
