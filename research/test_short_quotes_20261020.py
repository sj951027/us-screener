"""v23 회귀 테스트 — FINRA 격주 공매도 파일의 큰따옴표 형식(2026-09-15 결제분부터)을 파싱하고, 사고로 '완료' 표기된
결제일을 다시 받도록 되돌리는지(네트워크 0). `python research/test_short_quotes_20261020.py`"""
import pathlib
import sqlite3
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import us_short_collector as sc  # noqa: E402

HEADER = ("accountingYearMonthNumber|symbolCode|issueName|issuerServicesGroupExchangeCode|marketClassCode|"
          "currentShortPositionQuantity|previousShortPositionQuantity|stockSplitFlag|averageDailyVolumeQuantity|"
          "daysToCoverQuantity|revisionFlag|changePercent|changePreviousNumber|settlementDate")
PLAIN = HEADER + "\n20260831|A|Agilent Technologies Inc.|A|NYSE|5677007|5170553||2713701|2.09||9.79|506454|2026-08-31\n"
QUOTED = ("|".join(f'"{c}"' for c in HEADER.split("|")) + "\n"
          '"20260930"|"A"|"Agilent Technologies Inc."|"A"|"NYSE"|"5240309"|"5078629"||"2950874"|"1.78"||"3.18"|"161680"|"2026-09-30"\n'
          '"20260930"|"AA"|"Alcoa Corporation"|"A"|"NYSE"|"11725447"|"11984650"||"4113310"|"2.85"||"-2.16"|"-259203"|"2026-09-30"\n')


class ShortQuotes(unittest.TestCase):
    def test_plain_format_unchanged(self):
        rows = sc.parse_file(PLAIN, "20260831")
        self.assertEqual(rows, [("20260831", "A", 5677007.0, 2713701.0, 2.09, "NYSE")])

    def test_quoted_format_parsed(self):
        rows = sc.parse_file(QUOTED, "20260930")
        self.assertEqual(rows[0], ("20260930", "A", 5240309.0, 2950874.0, 1.78, "NYSE"))
        self.assertEqual(rows[1][:2], ("20260930", "AA"))
        self.assertTrue(all(len(r[0]) == 8 and r[0].isdigit() for r in rows))

    def test_requeue_only_dates_without_good_rows(self):
        con = sqlite3.connect(":memory:")
        for d in sc.DDL:
            con.execute(d)
        con.executemany("INSERT INTO short_interest VALUES (?,?,?,?,?,?)", [
            ("20260831", "A", 1.0, 1.0, 1.0, "NYSE"),          # 정상
            ('"2026093', '"A"', None, None, None, '"NYSE"'),   # 사고 행(0930 파일)
        ])
        con.executemany("INSERT INTO short_files_done VALUES (?,?)", [("20260831", 1), ("20260930", 1)])
        self.assertEqual(sc.requeue_malformed(con), ["20260930"])
        self.assertEqual([d for (d,) in con.execute("SELECT date FROM short_files_done")], ["20260831"])
        self.assertEqual(con.execute("SELECT COUNT(*) FROM short_interest").fetchone()[0], 2)   # 행은 지우지 않는다
        self.assertEqual(sc.requeue_malformed(con), [])   # 두 번째 호출은 아무것도 안 함


if __name__ == "__main__":
    unittest.main()
