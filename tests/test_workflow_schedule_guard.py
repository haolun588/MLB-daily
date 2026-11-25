"""daily_report.yml check job 的 schedule 延遲防護：schedule 在台北 13 點前一律跳過，且需在 DATE 計算之前。"""

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_fetch_mlb as base  # noqa: E402

REPO_ROOT = base.REPO_ROOT


class ScheduleDelayGuardTests(unittest.TestCase):
    def setUp(self):
        yml = (REPO_ROOT / ".github" / "workflows" / "daily_report.yml").read_text(encoding="utf-8")
        start = yml.index("Check if report already exists")
        end = yml.index("run-generator:")
        self.step = yml[start:end]

    def test_guard_checks_schedule_event_and_taipei_hour(self):
        self.assertIn('"$GITHUB_EVENT_NAME" = "schedule"', self.step)
        self.assertIn("TZ=Asia/Taipei date +%H", self.step)
        self.assertIn("10#", self.step)
        self.assertRegex(self.step, r"-lt 13")
        self.assertIn("::notice::", self.step)

    def test_guard_skips_and_exits(self):
        m = re.search(r'-lt 13 \]; then(.*?)\n\s*fi\n', self.step, re.S)
        self.assertIsNotNone(m)
        self.assertIn("skip=true", m.group(1))
        self.assertIn("exit 0", m.group(1))

    def test_guard_before_date_calculation(self):
        guard = self.step.index('"$GITHUB_EVENT_NAME" = "schedule"')
        date_calc = self.step.index('DATE="$(TZ=Asia/Taipei date -d yesterday')
        manual = self.step.index('[ -n "$INPUT_DATE" ]')
        self.assertLess(manual, guard)
        self.assertLess(guard, date_calc)


if __name__ == "__main__":
    unittest.main()
