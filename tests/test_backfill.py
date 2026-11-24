"""漏跑日補產、D-1 人事異動回填、週報補產與 --backfill 參數驗證測試。
全部不連網；所有寫檔都在暫存目錄（MlbTestBase 會 chdir 進去），不污染 repo 的 reports/、data/。"""

import contextlib
import io
import json
import sys
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_fetch_mlb as base  # noqa: E402

fetch_mlb = base.fetch_mlb
from mlb_daily import backfill, net, storage  # noqa: E402
from mlb_daily.config import BACKFILL_DAYS  # noqa: E402


def quiet_call(fn, *a, **kw):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        result = fn(*a, **kw)
    return result, out.getvalue(), err.getvalue()


def write_metadata(*days):
    storage.save_metadata("site/reports/metadata.json", {d: {"date": d, "type": "season"} for d in days})


class MissingDatesTests(unittest.TestCase):
    META = {"2026-11-10": {}, "2026-11-17": {}}

    def test_only_missing_within_window_excluding_d_and_today(self):
        # today=11-20：D=11-19 不補；候選 11-18、11-17、11-16（days=3）；11-17 已有
        self.assertEqual(backfill.missing_dates(date(2026, 11, 20), 3, self.META), ["2026-11-16", "2026-11-18"])

    def test_not_earlier_than_metadata_earliest(self):
        meta = {"2026-11-17": {}}
        self.assertEqual(backfill.missing_dates(date(2026, 11, 20), 5, meta), ["2026-11-18"])  # 11-16 以前不補

    def test_oldest_first_and_window_size(self):
        meta = {"2026-01-01": {}}
        result = backfill.missing_dates(date(2026, 11, 20), BACKFILL_DAYS, meta)
        self.assertEqual(len(result), BACKFILL_DAYS)
        self.assertEqual(result, sorted(result))
        self.assertEqual(result[0], "2026-11-12")
        self.assertEqual(result[-1], "2026-11-18")

    def test_year_boundary(self):
        meta = {"2026-12-01": {}}
        self.assertEqual(backfill.missing_dates(date(2027, 1, 2), 2, meta), ["2026-12-30", "2026-12-31"])

    def test_empty_or_bad_keys(self):
        self.assertEqual(backfill.missing_dates(date(2026, 11, 20), 3, {}), [])
        self.assertEqual(backfill.missing_dates(date(2026, 11, 20), 3, {"foo": {}}), [])


class RunBackfillTests(base.MlbTestBase):
    def setUp(self):
        super().setUp()
        self.weekly_notify = mock.patch("mlb_daily.notify.send_weekly_notification")
        self.weekly_notify_mock = self.weekly_notify.start()
        self.addCleanup(self.weekly_notify.stop)
        self.order = []
        self.games = {}  # date -> games 清單或例外

        def check(d):
            self.order.append(d)
            value = self.games.get(d, [])
            if isinstance(value, BaseException):
                raise value
            return True, value

        patches = {
            "check": mock.patch("mlb_daily.games.check_all_games_finalized", side_effect=check),
            "gen": mock.patch("mlb_daily.report.generate_report", side_effect=lambda d, g: {"date": d, "kind": "season"}),
            "rest": mock.patch("mlb_daily.report.generate_rest_day_report", side_effect=lambda d, r: {"date": d, "kind": "rest", "reason": r}),
            "off": mock.patch("mlb_daily.report.generate_offseason_report", side_effect=lambda d: {"date": d, "kind": "off"}),
            "classify": mock.patch("mlb_daily.season.classify_no_game_day", return_value=("offseason", None)),
            "update": mock.patch("mlb_daily.index_page.update_index_page"),
            "refresh": mock.patch("mlb_daily.backfill.refresh_snapshot_transactions", return_value=0),
        }
        self.m = {}
        for k, p in patches.items():
            self.m[k] = p.start()
            self.addCleanup(p.stop)

    def run_bf(self, today, days=7):
        return quiet_call(backfill.run_backfill, today, days)

    def assert_no_notify(self):
        self.discord.assert_not_called()
        self.weekly_notify_mock.assert_not_called()

    def test_no_metadata_does_nothing(self):
        code, out, _ = self.run_bf(date(2026, 11, 20))
        self.assertEqual(code, 0)
        self.assertIn("找不到", out)
        self.m["check"].assert_not_called()
        self.m["refresh"].assert_not_called()

    def test_only_missing_days_oldest_first(self):
        write_metadata("2026-11-10", "2026-11-17")
        code, _, _ = self.run_bf(date(2026, 11, 20), 3)
        self.assertEqual(code, 0)
        self.assertEqual(self.order, ["2026-11-16", "2026-11-18"])  # 不含 D（11-19）、不含已有的 11-17
        self.assertEqual([c.args[0]["date"] for c in self.m["update"].call_args_list], ["2026-11-16", "2026-11-18"])
        self.assert_no_notify()

    def test_year_boundary_and_prev_day_refresh(self):
        write_metadata("2026-12-01")
        code, _, _ = self.run_bf(date(2027, 1, 2), 3)  # D=2027-01-01、D-1=2026-12-31
        self.assertEqual(code, 0)
        self.assertEqual(self.order, ["2026-12-29", "2026-12-30", "2026-12-31"])
        self.assertNotIn("2027-01-01", self.order)
        # D-1 本次剛補產 → 不回填
        self.m["refresh"].assert_not_called()

    def test_prev_day_refreshed_when_not_backfilled(self):
        write_metadata("2026-12-01", "2026-12-31")  # D-1 已存在
        self.m["refresh"].return_value = 2
        code, out, _ = self.run_bf(date(2027, 1, 2), 2)
        self.assertEqual(code, 0)
        self.m["refresh"].assert_called_once_with("2026-12-31")
        self.assertIn("2 筆", out)

    def test_three_way_dispatch(self):
        write_metadata("2026-11-01")
        self.games["2026-11-16"] = [{"gamePk": 1}]
        self.m["classify"].side_effect = lambda d: ("rest_day", "季後賽休兵") if d == "2026-11-17" else ("offseason", None)
        code, _, _ = self.run_bf(date(2026, 11, 20), 3)
        self.assertEqual(code, 0)
        self.m["gen"].assert_called_once_with("2026-11-16", [{"gamePk": 1}])
        self.m["rest"].assert_called_once_with("2026-11-17", "季後賽休兵")
        self.m["off"].assert_called_once_with("2026-11-18")
        self.assertEqual(self.m["classify"].call_count, 2)  # 有賽事的日子不分類
        self.assert_no_notify()

    def test_single_day_failure_does_not_stop_others_and_exits_1(self):
        write_metadata("2026-11-01")
        self.games["2026-11-16"] = net.ApiError("boom")
        self.m["off"].side_effect = lambda d: (_ for _ in ()).throw(SystemExit(1)) if d == "2026-11-17" else {"date": d}
        code, out, err = self.run_bf(date(2026, 11, 20), 3)
        self.assertEqual(code, 1)
        self.assertEqual(self.order, ["2026-11-16", "2026-11-17", "2026-11-18"])
        self.assertEqual([c.args[0]["date"] for c in self.m["update"].call_args_list], ["2026-11-18"])
        self.assertIn("失敗 2 天（2026-11-16、2026-11-17）", out)
        self.assert_no_notify()

    def test_unexpected_exception_is_contained(self):
        write_metadata("2026-11-01")
        self.games["2026-11-16"] = RuntimeError("x")
        code, _, _ = self.run_bf(date(2026, 11, 20), 3)
        self.assertEqual(code, 1)
        self.assertEqual(self.order, ["2026-11-16", "2026-11-17", "2026-11-18"])
        self.assertEqual(self.m["update"].call_count, 2)

    def test_refresh_failure_is_not_fatal(self):
        write_metadata("2026-11-01", "2026-11-18")
        self.m["refresh"].side_effect = RuntimeError("x")
        code, _, err = self.run_bf(date(2026, 11, 20), 1)
        self.assertEqual(code, 0)
        self.assertIn("回填失敗", err)


class GenerateNoGameTests(base.MlbTestBase):
    def test_labels(self):
        with mock.patch("mlb_daily.season.classify_no_game_day", return_value=("rest_day", "休兵日")), \
                mock.patch("mlb_daily.report.generate_rest_day_report", return_value={"k": 1}) as rest:
            (meta, label), _, _ = quiet_call(backfill.generate_no_game_report, "2026-09-28")
        self.assertEqual((meta, label), ({"k": 1}, "休兵日快報"))
        rest.assert_called_once_with("2026-09-28", "休兵日")
        with mock.patch("mlb_daily.season.classify_no_game_day", return_value=("offseason", None)), \
                mock.patch("mlb_daily.report.generate_offseason_report", return_value={"k": 2}):
            (meta, label), _, _ = quiet_call(backfill.generate_no_game_report, "2026-12-01")
        self.assertEqual((meta, label), ({"k": 2}, "休賽季快報"))


def raw_tx(desc, person_id, name, code="SFA", day="2026-11-17", to=147):
    return {"date": day, "typeCode": code, "description": desc, "person": {"id": person_id, "fullName": name},
            "toTeam": {"id": to}}


class RefreshSnapshotTests(base.MlbTestBase):
    DAY = "2026-11-17"
    OLD_DESC = "Yankees signed free agent A One to a 2-year contract."
    NEW_DESC = "Yankees signed free agent B Two to a 1-year contract."

    def write_snapshot(self, **over):
        snap = {"schema": 1, "date": self.DAY, "has_games": True,
                "transactions": [{"type_code": "SFA", "date": self.DAY, "description": self.OLD_DESC,
                                  "description_zh": "洋基簽下自由球員 A One", "person_id": 1, "person_name": "A One",
                                  "persons": [{"id": 1, "name": "A One"}], "to_team_id": 147, "from_team_id": None,
                                  "team_ids": [147], "legs": []}],
                "news": [{"title": "N", "title_zh": "新聞", "link": "https://www.mlb.com/news/n",
                          "published": "2026-11-17T10:00:00+08:00", "image": None, "summary": ""}]}
        snap.update(over)
        path = Path("site/data/daily") / f"{self.DAY}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")
        return path

    def refresh(self, raw):
        with mock.patch("mlb_daily.transactions.fetch_raw_transactions", return_value=raw):
            return quiet_call(backfill.refresh_snapshot_transactions, self.DAY)[0]

    def test_adds_only_new_and_keeps_existing(self):
        path = self.write_snapshot()
        before = json.loads(path.read_text(encoding="utf-8"))
        added = self.refresh([raw_tx(self.OLD_DESC, 1, "A One"), raw_tx(self.NEW_DESC, 2, "B Two")])
        self.assertEqual(added, 1)
        after = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(after["transactions"][0], before["transactions"][0])  # 既有項目（含譯文）原樣
        self.assertEqual(after["transactions"][0]["description_zh"], "洋基簽下自由球員 A One")
        new = after["transactions"][1]
        self.assertEqual((new["description"], new["description_zh"], new["person_id"], new["to_team_id"]),
                         (self.NEW_DESC, None, 2, 147))
        self.assertEqual(new["persons"], [{"id": 2, "name": "B Two"}])
        self.assertEqual((after["has_games"], after["news"], after["schema"], after["date"]),
                         (before["has_games"], before["news"], 1, self.DAY))

    def test_idempotent_second_run(self):
        self.write_snapshot()
        raw = [raw_tx(self.NEW_DESC, 2, "B Two")]
        self.assertEqual(self.refresh(raw), 1)
        self.assertEqual(self.refresh(raw), 0)

    def test_no_new_leaves_file_untouched(self):
        path = self.write_snapshot()
        before = path.read_bytes()
        self.assertEqual(self.refresh([raw_tx(self.OLD_DESC, 1, "A One")]), 0)
        self.assertEqual(path.read_bytes(), before)

    def test_empty_result_leaves_file_untouched(self):
        path = self.write_snapshot()
        before = path.read_bytes()
        self.assertEqual(self.refresh([]), 0)
        self.assertEqual(path.read_bytes(), before)

    def test_missing_snapshot_skipped(self):
        self.assertEqual(self.refresh([raw_tx(self.NEW_DESC, 2, "B Two")]), 0)
        self.assertFalse(Path("site/data/daily").exists())

    def test_bad_format_skipped(self):
        for over in ({"schema": 99}, {"date": "2026-11-16"}, {"transactions": "x"}):
            path = self.write_snapshot(**over)
            before = path.read_bytes()
            self.assertEqual(self.refresh([raw_tx(self.NEW_DESC, 2, "B Two")]), 0, over)
            self.assertEqual(path.read_bytes(), before)
        path = self.write_snapshot()
        path.write_text("{broken", encoding="utf-8")
        self.assertEqual(self.refresh([raw_tx(self.NEW_DESC, 2, "B Two")]), 0)

    def test_translation_applied_to_new_items_only(self):
        self.write_snapshot()

        def fake_translate(news, views, tx_items, *a, **kw):
            for tx in tx_items:
                tx["description_zh"] = "譯:" + tx["description"]
            return True

        with mock.patch("mlb_daily.translate.apply_translations", side_effect=fake_translate) as tr:
            added = self.refresh([raw_tx(self.OLD_DESC, 1, "A One"), raw_tx(self.NEW_DESC, 2, "B Two")])
        self.assertEqual(added, 1)
        self.assertEqual([t["description"] for t in tr.call_args.args[2]], [self.NEW_DESC])
        txs = json.loads(Path(f"site/data/daily/{self.DAY}.json").read_text(encoding="utf-8"))["transactions"]
        self.assertEqual(txs[0]["description_zh"], "洋基簽下自由球員 A One")
        self.assertEqual(txs[1]["description_zh"], "譯:" + self.NEW_DESC)


class WeeklyBackfillTests(base.MlbTestBase):
    def setUp(self):
        super().setUp()
        self.ctx = {"rosterType": "40Man", "season": 2026, "rosterSeason": 2027}
        self.ctx_dates = []

        def resolve(d, *a, **kw):
            self.ctx_dates.append(d)
            return self.ctx

        self.generated = []

        def generate(today, week=None, force=False, **kw):
            self.generated.append((today, week, force))
            if week is None:
                return None  # 正常流程：今天無需產生
            return {"week": week, "path": f"reports/weekly/{week}.html"}

        for target, kw in (("mlb_daily.rosters.resolve_roster_context", {"side_effect": resolve}),
                           ("mlb_daily.weekly.generate_weekly_report", {"side_effect": generate}),
                           ("mlb_daily.index_page.refresh_index_page", {}),
                           ("mlb_daily.notify.send_weekly_notification", {})):
            p = mock.patch(target, **kw)
            m = p.start()
            self.addCleanup(p.stop)
            if target.endswith("send_weekly_notification"):
                self.weekly_notify = m
            elif target.endswith("refresh_index_page"):
                self.refresh_mock = m

    def set_index(self, *labels):
        for label in labels:
            storage.save_weekly_index({"week": label})

    def test_year_boundary_w53_and_only_missing(self):
        self.set_index("2026-W50", "2026-W52")
        today = date(2027, 1, 11)  # 週一；上一週 = 2027-W01
        got, out, _ = quiet_call(backfill.backfill_weekly_reports, today)
        self.assertEqual(got, ["2026-W51", "2026-W53", "2027-W01"])  # 舊到新；W52 已有
        self.assertEqual([g[1:] for g in self.generated], [("2026-W51", True), ("2026-W53", True), ("2027-W01", True)])
        self.assertEqual(self.ctx_dates, [date(2026, 12, 21), date(2027, 1, 4), date(2027, 1, 11)])  # 該週結束隔天（週一）
        for label in got:
            self.assertIn(f"weekly_backfilled={label}", out)
        self.assertNotIn("weekly_week=", out)
        self.assertNotIn("weekly_url=", out)

    def test_not_earlier_than_index_earliest(self):
        self.set_index("2026-W52")
        got, _, _ = quiet_call(backfill.backfill_weekly_reports, date(2027, 1, 11))
        self.assertEqual(got, ["2026-W53", "2027-W01"])  # W51 不晚於最早週 W52

    def test_empty_index_does_nothing(self):
        got, _, _ = quiet_call(backfill.backfill_weekly_reports, date(2027, 1, 11))
        self.assertEqual(got, [])
        self.assertEqual(self.generated, [])

    def test_in_season_not_backfilled(self):
        self.set_index("2026-W50")
        self.ctx = {"rosterType": "active", "season": 2026, "rosterSeason": 2026}
        got, _, _ = quiet_call(backfill.backfill_weekly_reports, date(2027, 1, 11))
        self.assertEqual(got, [])
        self.assertEqual(self.generated, [])

    def test_week_limit_is_four(self):
        self.set_index("2026-W01")
        got, _, _ = quiet_call(backfill.backfill_weekly_reports, date(2026, 11, 23))  # 上一週 = 2026-W47
        self.assertEqual(got, ["2026-W44", "2026-W45", "2026-W46", "2026-W47"])

    def test_no_snapshot_not_reported(self):
        self.set_index("2026-W50")
        with mock.patch("mlb_daily.weekly.generate_weekly_report", return_value=None):
            got, out, _ = quiet_call(backfill.backfill_weekly_reports, date(2027, 1, 11))
        self.assertEqual(got, [])
        self.assertNotIn("weekly_backfilled", out)

    def test_cli_weekly_prints_backfilled_not_week_or_url(self):
        self.set_index("2026-W50")
        write_metadata("2026-11-01")
        with mock.patch.object(sys, "argv", ["fetch_mlb.py", "--weekly", "--date", "2027-01-11"]):
            _, out, _ = quiet_call(fetch_mlb.main)
        self.assertIn("weekly_backfilled=2027-W01", out)
        self.assertNotIn("weekly_week=", out)
        self.assertNotIn("weekly_url=", out)
        self.weekly_notify.assert_not_called()
        self.refresh_mock.assert_called_once()  # 有補產時重產首頁

    def test_cli_weekly_with_week_skips_backfill(self):
        self.set_index("2026-W50")
        with mock.patch("mlb_daily.backfill.backfill_weekly_reports") as bf, \
                mock.patch.object(sys, "argv", ["fetch_mlb.py", "--weekly", "--week", "2026-W51", "--date", "2027-01-11"]):
            _, out, _ = quiet_call(fetch_mlb.main)
        bf.assert_not_called()
        self.assertIn("weekly_week=2026-W51", out)


class CliBackfillTests(base.MlbTestBase):
    def run_cli(self, *args):
        with mock.patch.object(sys, "argv", ["fetch_mlb.py", *args]), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as err:
            with self.assertRaises(SystemExit) as cm:
                fetch_mlb.main()
        return cm.exception.code, err.getvalue()

    def test_invalid_combinations(self):
        with mock.patch("mlb_daily.backfill.run_backfill") as rb:
            for args in (["--backfill-days", "3"], ["--backfill", "--backfill-days", "0"],
                         ["--backfill", "--backfill-days", "-2"], ["--backfill", "--weekly"],
                         ["--backfill", "--no-notify"], ["--backfill", "--check-wait"], ["--backfill", "--notify-only"],
                         ["--backfill", "--rosters"], ["--backfill", "--leaders"], ["--backfill", "--playoffs"],
                         ["--backfill", "--watchlist"], ["--backfill", "--week", "2026-W47"], ["--backfill", "--force"]):
                code, err = self.run_cli(*args)
                self.assertEqual(code, 1, args)
                self.assertIn("Error", err, args)
            rb.assert_not_called()

    def test_valid_invocation_passes_today_days_and_exit_code(self):
        with mock.patch("mlb_daily.backfill.run_backfill", return_value=1) as rb:
            code, _ = self.run_cli("--backfill", "--date", "2027-01-02", "--backfill-days", "3")
        self.assertEqual(code, 1)
        rb.assert_called_once_with(date(2027, 1, 2), 3)
        with mock.patch("mlb_daily.backfill.run_backfill", return_value=0) as rb:
            code, _ = self.run_cli("--backfill", "--date", "2027-01-02")
        self.assertEqual(code, 0)
        rb.assert_called_once_with(date(2027, 1, 2), BACKFILL_DAYS)

    def test_bad_date(self):
        with mock.patch("mlb_daily.backfill.run_backfill") as rb:
            code, _ = self.run_cli("--backfill", "--date", "nope")
        self.assertEqual(code, 1)
        rb.assert_not_called()


if __name__ == "__main__":
    unittest.main()
