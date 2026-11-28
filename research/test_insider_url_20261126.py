"""v24 회귀 테스트 — SEC 내부자 분기 파일의 경로가 바뀌어도(옛 경로 404) 새 경로에서 받는지, 둘 다 404 일 때만
'미공개'로 보는지(네트워크 0). `python research/test_insider_url_20261126.py`"""
import contextlib
import io
import pathlib
import sqlite3
import sys
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import us_insider_collector as ins  # noqa: E402

OLD, NEW = "/structureddata/", "/datastandardsinnovation/"


class Resp:
    def __init__(self, code, content=b""):
        self.status_code, self.content = code, content

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    """published = {분기: 경로 조각} — 그 경로에서만 200(고정 zip), 나머지는 404. 요청 URL 을 기록한다."""
    def __init__(self, published, code_other=404):
        self.published, self.code_other, self.calls = published, code_other, []

    def get(self, url, headers=None, timeout=None):
        self.calls.append(url)
        for q, part in self.published.items():
            if f"/{q}_form345.zip" in url and part in url:
                return Resp(200, ins._fixture_zip())
        return Resp(self.code_other)


def mem():
    con = sqlite3.connect(":memory:")
    for ddl in ins.DDL:
        con.execute(ddl)
    return con


class InsiderUrl(unittest.TestCase):
    def test_old_path_still_first(self):
        s = FakeSession({"2025q1": OLD})
        with contextlib.redirect_stdout(io.StringIO()):
            n = ins.load_quarter(mem(), s, "2025q1")
        self.assertEqual(n, 2)
        self.assertEqual(len(s.calls), 1)          # 옛 경로에서 받으면 새 경로는 두드리지 않는다
        self.assertIn(OLD, s.calls[0])

    def test_falls_back_to_new_path(self):
        con, s = mem(), FakeSession({"2026q2": NEW})
        with contextlib.redirect_stdout(io.StringIO()):
            n = ins.load_quarter(con, s, "2026q2")
        self.assertEqual(n, 2)
        self.assertEqual([OLD in s.calls[0], NEW in s.calls[1]], [True, True])
        self.assertEqual(con.execute("SELECT quarter, rows FROM insider_files_done").fetchall(), [("2026q2", 2)])

    def test_all_404_is_unpublished(self):
        s = FakeSession({})
        self.assertIsNone(ins.load_quarter(mem(), s, "2026q4"))
        self.assertEqual(len(s.calls), len(ins.URLS))

    def test_non_404_error_is_not_unpublished(self):
        # 403/5xx 는 '미공개'가 아니다 — 예외로 올려 main() 의 비치명 경고에 걸리게 한다(완료 표기 없음)
        con, s = mem(), FakeSession({}, code_other=403)
        with self.assertRaises(RuntimeError):
            ins.load_quarter(con, s, "2026q2")
        self.assertEqual(con.execute("SELECT COUNT(*) FROM insider_files_done").fetchone()[0], 0)

    def test_run_loads_q2_q3_then_stops_at_unpublished(self):
        # 실측 상황 재현: 2026q1 까지 완료, q2·q3 는 새 경로에만 있고 q4 는 미공개
        con = mem()
        qs = ["2025q4", "2026q1", "2026q2", "2026q3", "2026q4"]
        con.execute("INSERT INTO xbrl_meta VALUES ('insider_parser_ver', ?)", (ins.PARSER_VER,))
        con.executemany("INSERT INTO insider_files_done VALUES (?,?,?)", [(q, 1, "2026-09-01") for q in qs[:2]])
        s = FakeSession({"2026q2": NEW, "2026q3": NEW})
        with patch.object(ins, "quarters_until_today", return_value=qs), patch("requests.Session", return_value=s), \
                contextlib.redirect_stdout(io.StringIO()) as log:
            ins.run(con)
        done = {q for (q,) in con.execute("SELECT quarter FROM insider_files_done")}
        self.assertEqual(done, set(qs[:4]))
        self.assertIn("2026q4: 아직 미공개", log.getvalue())
        self.assertEqual(con.execute("SELECT COUNT(DISTINCT quarter) FROM insider_tx").fetchone()[0], 2)


if __name__ == "__main__":
    unittest.main()
