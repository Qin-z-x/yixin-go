"""稳定性与向后兼容测试：长时间随机棋局、并发状态读取、旧快照格式。"""
import json
import random
import tempfile
import threading
import unittest
from pathlib import Path

import server
from go_engine import GoGame, xy_to_vertex
from levels import LEVEL_BY_ID
from sgf_import import parse_sgf


class _TempData:
    def __enter__(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.old = server.ACTIVE_DIR, server.GAMES_DIR, server.DATA
        server.DATA = root
        server.ACTIVE_DIR = root / "active"
        server.GAMES_DIR = root / "games"
        server.ACTIVE_DIR.mkdir(parents=True)
        server.GAMES_DIR.mkdir(parents=True)
        return root

    def __exit__(self, exc_type, exc, tb):
        server.ACTIVE_DIR, server.GAMES_DIR, server.DATA = self.old
        self.tmp.cleanup()


def random_game(size: int, max_moves: int, seed: int) -> GoGame:
    rng = random.Random(seed)
    game = GoGame(size=size, komi=6.5, rules="chinese", player_color="B")
    for _ in range(max_moves):
        if game.phase != "playing":
            break
        candidates = []
        for y in range(size):
            for x in range(size):
                if game.board[y][x] == 0:
                    vertex = xy_to_vertex(x, y, size)
                    if game.is_legal(vertex)[0]:
                        candidates.append(vertex)
        if not candidates:
            game.pass_turn()
        else:
            game.play(rng.choice(candidates))
    return game


class StabilityTests(unittest.TestCase):
    def test_legacy_numeric_clock_snapshot(self):
        with _TempData():
            g = GoGame(size=9, komi=6.5, player_color="B")
            g.play("E5")
            s = server.GameSession(g, LEVEL_BY_ID["pro2"], "B", gen_id="legacy1", time_limit=600)
            s.active_enabled = True
            snap = s.to_snapshot()
            # 0.2.5 及以前的 clock 是 {"B": 数字, "W": 数字}
            snap["clock"] = {"B": 500, "W": 520}
            snap.pop("byo_yomi", None)
            restored = server.GameSession.from_snapshot(json.loads(json.dumps(snap)))
            self.assertEqual(restored.time_limit, 600)
            self.assertEqual(restored.clock["B"]["main"], 500)
            self.assertEqual(restored.clock["W"]["main"], 520)
            self.assertEqual(restored.byo_yomi, 0)

    def test_long_random_19x19_game_and_sgf(self):
        game = random_game(19, 120, seed=90210)
        self.assertGreater(len(game.history), 80)
        score = game.score()
        self.assertIn(score["winner"], ("B", "W", "D"))
        again = parse_sgf(game.sgf())["game"]
        self.assertEqual(again.board_flat(), game.board_flat())
        self.assertEqual(len(again.history), len(game.history))

    def test_concurrent_serialize(self):
        g = random_game(13, 40, seed=4242)
        s = server.GameSession(g, LEVEL_BY_ID["pro2"], "B", gen_id="concurrent1")
        errors = []
        barrier = threading.Barrier(8)

        def worker():
            try:
                barrier.wait()
                for _ in range(50):
                    data = s.serialize()
                    assert data["id"] == "concurrent1"
                    assert len(data["board_flat"]) == 13 * 13
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        self.assertEqual(errors, [])
        self.assertEqual(len(s.commentary), 1)


if __name__ == "__main__":
    unittest.main()
