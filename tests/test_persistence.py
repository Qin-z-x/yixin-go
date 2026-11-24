"""未完成对局快照 / 恢复 / 棋谱载入的回归测试。"""
import json
import tempfile
import unittest
from pathlib import Path

import server
from go_engine import GoGame
from levels import LEVEL_BY_ID


class _TempData:
    def __enter__(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.old_active, self.old_games, self.old_data = server.ACTIVE_DIR, server.GAMES_DIR, server.DATA
        server.DATA = root
        server.ACTIVE_DIR = root / "active"
        server.GAMES_DIR = root / "games"
        server.ACTIVE_DIR.mkdir(parents=True)
        server.GAMES_DIR.mkdir(parents=True)
        return root

    def __exit__(self, exc_type, exc, tb):
        server.ACTIVE_DIR, server.GAMES_DIR, server.DATA = self.old_active, self.old_games, self.old_data
        self.tmp.cleanup()


class SnapshotTests(unittest.TestCase):
    def test_roundtrip_playing_game(self):
        with _TempData() as root:
            g = GoGame(size=9, komi=6.5, rules="chinese", player_color="B")
            g.play("E5")
            g.play("D5")
            s = server.GameSession(g, LEVEL_BY_ID["pro2"], "B", gen_id="abc123")
            s.active_enabled = True
            s.add_comment("测试评论", "info")
            s._save_active_locked()
            path = server.ACTIVE_DIR / "abc123.json"
            self.assertTrue(path.exists())
            restored = server.GameSession.from_snapshot(json.loads(path.read_text(encoding="utf-8")))
            self.assertEqual(restored.id, "abc123")
            self.assertEqual(restored.game.board_flat(), g.board_flat())
            self.assertEqual([m.vertex for m in restored.game.history], ["E5", "D5"])
            self.assertEqual(restored.commentary[-1]["text"], "测试评论")
            self.assertEqual(restored.game.phase, "playing")
            restored.active_enabled = False
            self.assertTrue(restored.can_undo())

    def test_can_undo_after_ai_move(self):
        g = GoGame(size=9, komi=6.5, player_color="B")
        g.play("E5")   # 人类
        g.play("D5")   # AI
        s = server.GameSession(g, LEVEL_BY_ID["pro2"], "B")
        self.assertTrue(s.can_undo())

    def test_redo_restores_position(self):
        g = GoGame(size=9, komi=6.5, player_color="B")
        g.play("E5")
        g.play("D5")
        s = server.GameSession(g, LEVEL_BY_ID["pro2"], "B")
        before = g.board_flat()
        s.undo()
        self.assertTrue(s.redo_stack)
        self.assertTrue(s.serialize()["can_redo"])
        self.assertNotEqual(s.game.board_flat(), before)
        s.redo()
        self.assertEqual(s.game.board_flat(), before)
        self.assertEqual(len(s.game.history), 2)
        self.assertFalse(s.redo_stack)

    def test_roundtrip_scoring_game(self):
        with _TempData():
            g = GoGame(size=9, komi=0.5)
            g.play("E5")
            g.pass_turn()
            g.pass_turn()
            self.assertEqual(g.phase, "scoring")
            g.dead_stones = {"A9"}
            s = server.GameSession(g, LEVEL_BY_ID["k5"], "B", gen_id="scoring1")
            s.active_enabled = True
            s._save_active_locked()
            restored = server.GameSession.from_snapshot(
                json.loads((server.ACTIVE_DIR / "scoring1.json").read_text(encoding="utf-8"))
            )
            self.assertEqual(restored.game.phase, "scoring")
            self.assertEqual(restored.game.dead_stones, {"A9"})
            self.assertEqual(restored.game.scoring["dead"], ["A9"])


class RestoreApplicationTests(unittest.TestCase):
    def test_restore_active_game_on_restart(self):
        with _TempData():
            app = server.Application(start_engine=False, persist_active=True)
            session = app.new_game({"size": 9, "player_color": "B", "level_id": "pro2"})
            session.game.play("E5")
            session._save_active_locked()
            self.assertTrue((server.ACTIVE_DIR / f"{session.id}.json").exists())

            app2 = server.Application(start_engine=False, persist_active=True)
            count = app2.restore_active_games()
            self.assertEqual(count, 1)
            loaded = app2.get(session.id)
            self.assertEqual(loaded.game.board_flat(), session.game.board_flat())
            self.assertEqual(loaded.game.phase, "playing")
            self.assertEqual(app2.active_games()[0]["id"], session.id)

    def test_load_saved_record(self):
        with _TempData():
            g = GoGame(size=9, komi=6.5, player_color="B")
            g.play("E5")
            g.pass_turn()
            g.pass_turn()
            g.result_from_score()
            payload = {
                "id": "record1",
                "created_at": "2026-01-01T00:00:00",
                "level": "职业2段",
                "level_id": "pro2",
                "size": 9,
                "rules": "chinese",
                "komi": 6.5,
                "handicap": 0,
                "player_color": "B",
                "moves": g.moves_list(),
                "result": g.result,
                "sgf": g.sgf(),
            }
            (server.GAMES_DIR / "record1.json").write_text(json.dumps(payload), encoding="utf-8")
            app = server.Application(start_engine=False, persist_active=False)
            loaded = app.load_record("record1.json")
            self.assertEqual(loaded.id, "record1")
            self.assertEqual(loaded.game.phase, "ended")
            self.assertEqual(loaded.game.result["text"], g.result["text"])
            self.assertEqual(len(loaded.game.history), 3)
            loaded._save_locked()
            self.assertEqual(len(list(server.GAMES_DIR.glob("*.json"))), 1)

    def test_load_record_rejects_traversal(self):
        with _TempData():
            app = server.Application(start_engine=False, persist_active=False)
            with self.assertRaises(ValueError):
                app.load_record("../server.py")
            with self.assertRaises(KeyError):
                app.load_record("missing.json")

    def test_review_markdown(self):
        with _TempData():
            g = GoGame(size=9, komi=6.5, player_color="B")
            g.play("E5")
            s = server.GameSession(g, LEVEL_BY_ID["pro2"], "B", gen_id="review1")
            s.review = {
                "status": "done",
                "summary": {
                    "avg_loss": 0.02, "top3_rate": 0.75,
                    "key_mistakes": [{"move_no": 1, "color": "B", "move": "E5", "loss": 0.1, "best_move": "D4"}],
                },
                "items": [{
                    "move_no": 1, "color": "B", "move": "E5", "winrate_black": 0.52,
                    "loss": 0.1, "quality": "失误", "best_move": "D4",
                }],
            }
            report = s.review_markdown()
            self.assertIn("弈心 · 围棋复盘报告", report)
            self.assertIn("第 1 手", report)
            self.assertIn("D4", report)
            self.assertIn("失误", report)

    def test_delete_record_and_abandon_game(self):
        with _TempData():
            g = GoGame(size=9, komi=6.5, player_color="B")
            g.play("E5")
            g.pass_turn()
            g.pass_turn()
            g.result_from_score()
            session = server.GameSession(g, LEVEL_BY_ID["pro2"], "B", gen_id="del1")
            session._save_locked()
            filename = session.record_path.name
            app = server.Application(start_engine=False, persist_active=False)
            self.assertEqual(app.game_stats()["total"], 1)
            stats = app.delete_record(filename)
            self.assertEqual(stats["total"], 0)
            self.assertFalse(session.record_path.exists())
            with self.assertRaises(KeyError):
                app.delete_record(filename)
            with self.assertRaises(ValueError):
                app.delete_record("../secret.json")

            g2 = GoGame(size=9, komi=6.5, player_color="B")
            s2 = server.GameSession(g2, LEVEL_BY_ID["pro2"], "B", gen_id="abandon1")
            app.games[s2.id] = s2
            result = app.abandon_game(s2.id)
            self.assertTrue(result["ok"])
            with self.assertRaises(KeyError):
                app.get(s2.id)

    def test_corrupt_record_and_snapshot_are_skipped(self):
        with _TempData():
            (server.GAMES_DIR / "broken.json").write_text("{not json", encoding="utf-8")
            (server.ACTIVE_DIR / "broken.json").write_text("{not json", encoding="utf-8")
            app = server.Application(start_engine=False, persist_active=True)
            self.assertEqual(app.recent_games(), [])
            self.assertEqual(app.game_stats()["total"], 0)
            self.assertEqual(app.restore_active_games(), 0)

    def test_batch_delete_records(self):
        with _TempData():
            names = []
            for i in range(2):
                g = GoGame(size=9, komi=6.5, player_color="B")
                g.play("E5")
                g.pass_turn()
                g.pass_turn()
                g.result_from_score()
                s = server.GameSession(g, LEVEL_BY_ID["pro2"], "B", gen_id=f"batch{i}")
                s._save_locked()
                names.append(s.record_path.name)
            app = server.Application(start_engine=False, persist_active=False)
            result = app.delete_records([names[0], "missing.json", "../bad.json"])
            self.assertEqual(result["deleted"], [names[0]])
            self.assertEqual(result["missing"], ["missing.json"])
            self.assertEqual(len(result["invalid"]), 1)
            self.assertEqual(app.game_stats()["total"], 1)

    def test_clock_timeout_ends_game(self):
        with _TempData():
            g = GoGame(size=9, komi=6.5, player_color="B")
            s = server.GameSession(g, LEVEL_BY_ID["pro2"], "B", gen_id="clock1", time_limit=10)
            s.active_enabled = True
            s.clock_last -= 11
            data = s.serialize()
            self.assertEqual(data["phase"], "ended")
            self.assertEqual(data["result"]["reason"], "timeout")
            self.assertEqual(data["result"]["winner"], "W")
            self.assertEqual(data["clock"]["B"], 0)
            self.assertFalse((server.ACTIVE_DIR / "clock1.json").exists())

    def test_ai_clock_timeout_gives_human_win(self):
        with _TempData():
            g = GoGame(size=9, komi=6.5, player_color="B")
            g.play("E5")  # 轮到白（AI）
            s = server.GameSession(g, LEVEL_BY_ID["pro2"], "B", gen_id="clock3", time_limit=10)
            s.active_enabled = True
            s.clock["W"]["main"] = 0.1
            s.clock_last -= 1
            data = s.serialize()
            self.assertEqual(data["phase"], "ended")
            self.assertEqual(data["result"]["reason"], "timeout")
            self.assertEqual(data["result"]["winner"], "B")
            self.assertEqual(data["clock"]["W"], 0)

    def test_byo_yomi_periods_reset_and_timeout(self):
        with _TempData():
            g = GoGame(size=9, komi=6.5, player_color="B")
            s = server.GameSession(g, LEVEL_BY_ID["pro2"], "B", gen_id="byo1", time_limit=10, byo_yomi=30)
            s.active_enabled = True
            s.clock["B"]["main"] = 0.5
            s.clock_last -= 1
            data = s.serialize()
            self.assertEqual(data["clock"]["B_main"], 0)
            self.assertEqual(data["clock"]["B_periods"], 3)
            self.assertLessEqual(data["clock"]["B_byo"], 30)
            self.assertGreater(data["clock"]["B_byo"], 29)
            s.clock_last -= 30
            data = s.serialize()
            self.assertEqual(data["clock"]["B_periods"], 2)
            self.assertGreater(data["clock"]["B_byo"], 29)
            s._reset_byo_period_locked("B")
            self.assertEqual(s.clock["B"]["byo"], 30.0)
            s.clock["B"]["periods"] = 1
            s.clock["B"]["byo"] = 0.1
            s.clock_last -= 1
            data = s.serialize()
            self.assertEqual(data["phase"], "ended")
            self.assertEqual(data["result"]["winner"], "W")
            self.assertEqual(data["result"]["reason"], "timeout")

    def test_clock_snapshot_roundtrip(self):
        with _TempData():
            g = GoGame(size=9, komi=6.5, player_color="B")
            g.play("E5")
            s = server.GameSession(g, LEVEL_BY_ID["pro2"], "B", gen_id="clock2", time_limit=600, byo_yomi=30)
            s.active_enabled = True
            s.clock["B"]["main"] = 540.0
            s.clock["W"]["main"] = 580.0
            s._save_active_locked()
            restored = server.GameSession.from_snapshot(json.loads((server.ACTIVE_DIR / "clock2.json").read_text(encoding="utf-8")))
            self.assertEqual(restored.time_limit, 600)
            self.assertEqual(restored.byo_yomi, 30)
            self.assertEqual(restored.clock["B"]["periods"], 3)
            self.assertLessEqual(restored.clock["B"]["main"], 540.0)
            self.assertLessEqual(restored.clock["W"]["main"], 580.0)
            self.assertGreater(restored.clock["B"]["main"], 530.0)

    def test_diagnostics_and_stats(self):
        with _TempData():
            g = GoGame(size=9, komi=6.5, player_color="B")
            g.play("E5")
            g.pass_turn()
            g.pass_turn()
            g.result_from_score()
            session = server.GameSession(g, LEVEL_BY_ID["pro2"], "B", gen_id="stats1")
            session._save_locked()
            app = server.Application(start_engine=False, persist_active=False)
            stats = app.game_stats()
            self.assertEqual(stats["total"], 1)
            self.assertEqual(stats["avg_moves"], 3)
            diag = app.diagnostics()
            self.assertIn("checks", diag)
            self.assertIn(diag["status"], ("ok", "warn", "fail"))
            self.assertTrue(any(c["id"] == "data_writable" for c in diag["checks"]))


if __name__ == "__main__":
    unittest.main()
