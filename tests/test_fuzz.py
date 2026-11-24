"""随机规则模糊测试：不依赖引擎，专门找棋盘状态/提子/数子/悔棋的边界问题。"""
import random
import unittest

import server
from go_engine import BLACK, WHITE, GoGame, xy_to_vertex
from levels import LEVEL_BY_ID
from sgf_import import parse_sgf


def random_legal_game(seed: int, size: int = 9, max_moves: int = 120) -> GoGame:
    rng = random.Random(seed)
    game = GoGame(size=size, komi=6.5, rules="chinese", player_color="B")
    for _ in range(max_moves):
        if game.phase != "playing":
            break
        if rng.random() < 0.04:
            game.pass_turn()
            continue
        candidates = []
        for y in range(size):
            for x in range(size):
                if game.board[y][x] == 0:
                    vertex = xy_to_vertex(x, y, size)
                    if game.is_legal(vertex)[0]:
                        candidates.append(vertex)
        if not candidates:
            game.pass_turn()
            continue
        game.play(rng.choice(candidates))
    return game


class FuzzTests(unittest.TestCase):
    def test_random_games_keep_board_invariants(self):
        for seed in range(25):
            game = random_legal_game(seed)
            flat = game.board_flat()
            self.assertEqual(len(flat), game.size * game.size, seed)
            self.assertEqual(flat.count("1"), sum(1 for row in game.board for v in row if v == BLACK), seed)
            self.assertEqual(flat.count("2"), sum(1 for row in game.board for v in row if v == WHITE), seed)
            score = game.score()
            for key in ("black_score", "white_score", "margin"):
                self.assertIsInstance(score[key], (int, float))
                self.assertEqual(score[key], score[key], key)  # 不是 NaN
            self.assertIn(score["winner"], ("B", "W", "D"))

    def test_random_game_sgf_roundtrip(self):
        for seed in range(12):
            game = random_legal_game(seed, max_moves=50)
            parsed = parse_sgf(game.sgf())
            replay = parsed["game"]
            self.assertEqual(replay.board_flat(), game.board_flat(), seed)
            self.assertEqual(len(replay.history), len(game.history), seed)
            self.assertEqual(replay.captures, game.captures, seed)

    def test_random_undo_redo_restores_board(self):
        for seed in range(15):
            game = random_legal_game(seed, max_moves=35)
            session = server.GameSession(game, LEVEL_BY_ID["pro2"], "B", gen_id=f"fuzz{seed}")
            if not session.can_undo():
                continue
            before = game.board_flat()
            count = len(game.history)
            session.undo()
            self.assertNotEqual(session.game.board_flat(), before, seed)
            self.assertTrue(session.redo_stack, seed)
            session.redo()
            self.assertEqual(session.game.board_flat(), before, seed)
            self.assertEqual(len(session.game.history), count, seed)


if __name__ == "__main__":
    unittest.main()
