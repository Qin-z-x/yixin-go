import unittest

from go_engine import BLACK, WHITE, GoGame, IllegalMove, vertex_to_xy, xy_to_vertex


class RuleTests(unittest.TestCase):
    def test_coordinates_skip_i(self):
        self.assertEqual(xy_to_vertex(8, 18, 19), "J1")
        self.assertEqual(xy_to_vertex(18, 0, 19), "T19")
        self.assertEqual(vertex_to_xy("Q16", 19), (15, 3))

    def test_capture_single_stone(self):
        g = GoGame(size=9)
        for v, color in (("D5", BLACK), ("F5", BLACK), ("E4", BLACK), ("E5", WHITE)):
            x, y = vertex_to_xy(v, 9)
            g.board[y][x] = color
        g.to_move = BLACK
        rec = g.play("E6")
        self.assertEqual(rec.captures, ["E5"])
        self.assertEqual(g.stone_at("E5"), 0)
        self.assertEqual(g.captures[BLACK], 1)

    def test_suicide_illegal(self):
        g = GoGame(size=9)
        for v in ("D5", "E4", "E6", "F5"):
            x, y = vertex_to_xy(v, 9)
            g.board[y][x] = WHITE
        g.to_move = BLACK
        ok, msg = g.is_legal("E5", BLACK)
        self.assertFalse(ok)
        self.assertIn("自杀", msg)

    def test_simple_ko(self):
        g = GoGame(size=9)
        # 构造经典劫：白 E5 被黑三面包围，黑 E6 提子后也只剩 E5 一气。
        setup_b = {"D5", "F5", "E4"}
        setup_w = {"E5", "D6", "F6", "E7"}
        for v in setup_b:
            x, y = vertex_to_xy(v, 9)
            g.board[y][x] = BLACK
        for v in setup_w:
            x, y = vertex_to_xy(v, 9)
            g.board[y][x] = WHITE
        g.to_move = BLACK
        rec = g.play("E6")
        self.assertEqual(rec.captures, ["E5"])
        self.assertEqual(g.stone_at("E5"), 0)
        ok, msg = g.is_legal("E5", WHITE)
        self.assertFalse(ok, msg)
        self.assertIn("劫", msg)

    def test_handicap(self):
        g = GoGame(size=19, handicap=4, komi=0.5)
        self.assertEqual(len(g.initial_stones), 4)
        self.assertEqual(g.to_move, WHITE)
        self.assertEqual(sum(row.count(BLACK) for row in g.board), 4)

    def test_territory_scoring(self):
        g = GoGame(size=9, komi=0.5)
        # 黑棋用 B1、A2、B2 围住 A1；E5 放一颗白子。
        for v, color in (("B1", BLACK), ("A2", BLACK), ("B2", BLACK), ("E5", WHITE)):
            x, y = vertex_to_xy(v, 9)
            g.board[y][x] = color
        score = g.score()
        self.assertEqual(score["breakdown"]["black_territory"], 1)
        self.assertEqual(score["breakdown"]["black_stones"], 3)
        self.assertEqual(score["winner"], "B")  # 4 : 1.5

    def test_dead_stone_scoring(self):
        g = GoGame(size=9, komi=0.5)
        # 黑围住左上，白 E5 为盘上死子
        for v, color in (("B1", BLACK), ("A2", BLACK), ("B2", BLACK), ("E5", WHITE)):
            x, y = vertex_to_xy(v, 9)
            g.board[y][x] = color
        base = g.score()
        dead = g.score(dead=["E5"])
        self.assertEqual(dead["breakdown"]["black_stones"], 3)
        self.assertLess(dead["white_score"], base["white_score"])

    def test_undo(self):
        g = GoGame(size=9)
        g.play("E5")
        g.play("D5")
        self.assertEqual(len(g.history), 2)
        g.undo(1)
        self.assertEqual(len(g.history), 1)
        self.assertEqual(g.to_move, WHITE)
        self.assertEqual(g.stone_at("D5"), 0)

    def test_sgf(self):
        g = GoGame(size=19, handicap=2)
        g.play("C3")  # 白棋先行
        sgf = g.sgf()
        self.assertIn("HA[2]", sgf)
        self.assertIn("AB[dp][pd]", sgf)
        self.assertIn(";W[cq]", sgf)


if __name__ == "__main__":
    unittest.main()
