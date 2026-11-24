"""levels.py 档位表合法性测试（纯标准库，不依赖引擎/GPU）。

目标是防止档位配置出现非法值、重复 id、Human SL 档缺少 profile、
职业档 visits 与 README 宣称不符等回归。
"""
import unittest

from levels import DEFAULT_LEVEL, LEVELS, LEVEL_BY_ID, public_levels

REQUIRED_KEYS = {"id", "name", "short", "group", "visits", "human", "desc"}
HUMAN_GROUPS = {"入门", "进阶", "段位"}
PRO_GROUPS = {"职业"}
PRO_VISITS = {"pro2": 400, "pro5": 800, "pro9": 1600}
HUMAN_LEVEL_IDS = ["k15", "k10", "k5", "k1", "d1", "d3"]


class LevelsShapeTests(unittest.TestCase):
    def test_level_count_and_unique_ids(self):
        self.assertEqual(len(LEVELS), 9)
        ids = [level["id"] for level in LEVELS]
        self.assertEqual(len(ids), len(set(ids)))
        names = [level["name"] for level in LEVELS]
        self.assertEqual(len(names), len(set(names)))

    def test_required_keys_and_types(self):
        for level in LEVELS:
            self.assertTrue(REQUIRED_KEYS <= set(level), level.get("id"))
            self.assertIsInstance(level["id"], str)
            self.assertTrue(level["id"])
            self.assertIsInstance(level["name"], str)
            self.assertTrue(level["name"].strip())
            self.assertIsInstance(level["short"], str)
            self.assertTrue(level["short"].strip())
            self.assertIsInstance(level["visits"], int)
            self.assertGreater(level["visits"], 0)
            self.assertIsInstance(level["human"], bool)
            self.assertIsInstance(level["desc"], str)
            self.assertTrue(level["desc"].strip())

    def test_groups(self):
        groups = {level["group"] for level in LEVELS}
        self.assertEqual(groups, HUMAN_GROUPS | PRO_GROUPS)

    def test_human_levels_have_valid_profile(self):
        human = [level for level in LEVELS if level["human"]]
        self.assertEqual([level["id"] for level in human], HUMAN_LEVEL_IDS)
        for level in human:
            profile = level.get("profile")
            self.assertIsInstance(profile, str, level["id"])
            self.assertRegex(profile, r"^rank_\d+[kd]$")
            temp = level.get("temperature")
            self.assertIsInstance(temp, float, level["id"])
            self.assertGreater(temp, 0.0)
            self.assertLessEqual(temp, 1.1)
            self.assertIn("utility_weight", level) if level["id"] in ("d1", "d3") else None
            if "utility_weight" in level:
                self.assertGreaterEqual(level["utility_weight"], 0.0)
                self.assertLessEqual(level["utility_weight"], 1.0)

    def test_pro_levels(self):
        pro = [level for level in LEVELS if not level["human"]]
        self.assertEqual([level["id"] for level in pro], list(PRO_VISITS))
        visits = [level["visits"] for level in pro]
        self.assertEqual(visits, [400, 800, 1600])  # 必须递增
        for level in pro:
            self.assertIn(level["group"], PRO_GROUPS)
            self.assertNotIn("profile", level)
            self.assertNotIn("temperature", level)

    def test_default_level(self):
        self.assertEqual(DEFAULT_LEVEL, "pro2")
        self.assertIn(DEFAULT_LEVEL, LEVEL_BY_ID)
        default = LEVEL_BY_ID[DEFAULT_LEVEL]
        self.assertEqual(default["visits"], 400)
        self.assertTrue(default.get("guarantee"))


class LevelsLookupTests(unittest.TestCase):
    def test_level_by_id_covers_all(self):
        self.assertEqual(set(LEVEL_BY_ID), {level["id"] for level in LEVELS})
        for level in LEVELS:
            self.assertIs(LEVEL_BY_ID[level["id"]], level)

    def test_public_levels_hides_engine_profile(self):
        public = public_levels()
        self.assertEqual(len(public), len(LEVELS))
        for level in public:
            self.assertNotIn("profile", level)
            self.assertIn("visits", level)
        self.assertIn("profile", LEVEL_BY_ID["k15"])

    def test_public_levels_returns_copies(self):
        public = public_levels()
        public[0]["name"] = "被篡改"
        public[0]["visits"] = 9999
        self.assertNotEqual(LEVELS[0]["name"], "被篡改")
        self.assertNotEqual(LEVELS[0]["visits"], 9999)


if __name__ == "__main__":
    unittest.main()
