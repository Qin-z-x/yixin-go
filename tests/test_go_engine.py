"""go_engine 的规则回归测试（纯标准库，不依赖引擎/GPU）。

覆盖：坐标与 SGF 互转、提子、自杀、劫、让子、中国/日本数子、死子标记、
悔棋、SGF 导出与再导入（往返）。
"""
import re
import unittest

from go_engine import (
    BLACK,
    WHITE,
    GoGame,
    IllegalMove,
    from_sgf_vertex,
    to_sgf_vertex,
    vertex_to_xy,
    xy_to_vertex,
)


def put(game: GoGame, stones: dict[str, int]) -> None:
    """在棋盘上直接摆子（仅测试用，绕过轮次与劫规则）。"""
    for vertex, color in stones.items():
        x, y = vertex_to_xy(vertex, game.size)
        game.board[y][x] = color


class CoordinateTests(unittest.TestCase):
    def test_skip_letter_i(self):
        self.assertEqual(xy_to_vertex(0, 18, 19), "A1")
        self.assertEqual(xy_to_vertex(8, 18, 19), "J1")  # 跳过 I
        self.assertEqual(xy_to_vertex(18, 0, 19), "T19")
        self.assertEqual(vertex_to_xy("J1", 19), (8, 18))
        self.assertEqual(vertex_to_xy("Q16", 19), (15, 3))

    def test_board_sizes(self):
        self.assertEqual(xy_to_vertex(8, 0, 9), "J9")
        self.assertEqual(xy_to_vertex(12, 0, 13), "N13")
        with self.assertRaises(IllegalMove):
            vertex_to_xy("K1", 9)  # K 不在 9 路列里

    def test_invalid_vertices(self):
        for bad in ("", "A", "Z1", "A0", "A20", "T-1", "1A", " pass "[:-1]):
            with self.assertRaises(IllegalMove, msg=bad):
                vertex_to_xy(bad, 19)
        with self.assertRaises(IllegalMove):
            vertex_to_xy("pass", 19)

    def test_sgf_vertex_roundtrip(self):
        for size in (9, 13, 19):
            for x in range(size):
                for y in range(size):
                    v = xy_to_vertex(x, y, size)
                    self.assertEqual(from_sgf_vertex(to_sgf_vertex(v, size), size), v)
        self.assertEqual(to_sgf_vertex("pass", 19), "")
        self.assertEqual(from_sgf_vertex("", 19), "pass")
        self.assertEqual(from_sgf_vertex("tt", 19), "pass")  # FF[4] 旧停一手写法
        self.assertEqual(to_sgf_vertex("PASS", 9), "")

    def test_sgf_vertex_invalid(self):
        with self.assertRaises(IllegalMove):
            from_sgf_vertex("a", 19)
        with self.assertRaises(IllegalMove):
            from_sgf_vertex("zz", 9)  # 越界
        with self.assertRaises(IllegalMove):
            to_sgf_vertex("A0", 19)


class CaptureTests(unittest.TestCase):
    def test_single_stone_capture(self):
        g = GoGame(size=9)
        put(g, {"D5": BLACK, "F5": BLACK, "E4": BLACK, "E5": WHITE})
        g.to_move = BLACK
        rec = g.play("E6")
        self.assertEqual(rec.captures, ["E5"])
        self.assertEqual(g.stone_at("E5"), 0)
        self.assertEqual(g.captures[BLACK], 1)
        self.assertEqual(rec.move_no, 1)

    def test_group_capture_multi_stone(self):
        g = GoGame(size=9)
        # 白 D5/E5 两子，黑三面包围，最后一手 F5 提掉。
        put(g, {"C5": BLACK, "D4": BLACK, "E4": BLACK, "F4": BLACK, "D6": BLACK, "E6": BLACK, "F6": BLACK,
                "D5": WHITE, "E5": WHITE})
        g.to_move = BLACK
        rec = g.play("F5")
        self.assertEqual(sorted(rec.captures), ["D5", "E5"])
        self.assertEqual(g.captures[BLACK], 2)
        self.assertEqual(g.stone_at("D5"), 0)
        self.assertEqual(g.stone_at("E5"), 0)

    def test_corner_capture(self):
        g = GoGame(size=9)
        put(g, {"A2": BLACK, "A1": WHITE})
        g.to_move = BLACK
        rec = g.play("B1")  # 白 A1 最后一气被填
        self.assertEqual(rec.captures, ["A1"])

    def test_recapture_without_ko_shape_is_suicide(self):
        g = GoGame(size=9)
        put(g, {"D5": BLACK, "F5": BLACK, "E4": BLACK, "E5": WHITE})
        g.to_move = BLACK
        rec = g.play("E6")
        self.assertEqual(rec.captures, ["E5"])
        # 缺少外围白子时不是劫形，白棋下 E5 是自杀
        ok, msg = g.is_legal("E5", WHITE)
        self.assertFalse(ok)
        self.assertIn("自杀", msg)


class SuicideAndKoTests(unittest.TestCase):
    def test_suicide_rejected(self):
        g = GoGame(size=9)
        put(g, {"D5": WHITE, "E4": WHITE, "E6": WHITE, "F5": WHITE})
        g.to_move = BLACK
        ok, msg = g.is_legal("E5", BLACK)
        self.assertFalse(ok)
        self.assertIn("自杀", msg)
        with self.assertRaises(IllegalMove):
            g.play("E5")
        self.assertEqual(g.history, [])

    def test_simple_ko_immediate_recapture(self):
        g = GoGame(size=9)
        put(g, {"D5": BLACK, "F5": BLACK, "E4": BLACK, "E5": WHITE, "D6": WHITE, "F6": WHITE, "E7": WHITE})
        g.to_move = BLACK
        rec = g.play("E6")
        self.assertEqual(rec.captures, ["E5"])
        ok, msg = g.is_legal("E5", WHITE)
        self.assertFalse(ok)
        self.assertIn("劫", msg)
        with self.assertRaises(IllegalMove):
            g.play("E5")

    def test_ko_legal_after_elsewhere(self):
        g = GoGame(size=9)
        put(g, {"D5": BLACK, "F5": BLACK, "E4": BLACK, "E5": WHITE, "D6": WHITE, "F6": WHITE, "E7": WHITE})
        g.play("E6")       # 黑提劫
        g.play("A5")       # 白找劫
        g.play("A6")       # 黑应
        ok, msg = g.is_legal("E5", WHITE)
        self.assertTrue(ok, msg)
        g.play("E5")       # 白提回
        self.assertEqual(g.stone_at("E6"), 0)
        self.assertEqual(g.stone_at("E5"), WHITE)

    def test_ko_hash_cleared_after_pass(self):
        g = GoGame(size=9)
        put(g, {"D5": BLACK, "F5": BLACK, "E4": BLACK, "E5": WHITE, "D6": WHITE, "F6": WHITE, "E7": WHITE})
        g.play("E6")
        g.pass_turn()  # 白停一手
        self.assertIsNone(g.ko_hash)


class TurnAndValidationTests(unittest.TestCase):
    def test_play_wrong_color(self):
        g = GoGame(size=9)
        with self.assertRaises(IllegalMove):
            g.play("E5", WHITE)  # 轮到黑

    def test_play_occupied_and_out_of_bounds(self):
        g = GoGame(size=9)
        g.play("E5")
        with self.assertRaises(IllegalMove):
            g.play("E5")
        with self.assertRaises(IllegalMove):
            g.play("Z99")

    def test_invalid_game_config(self):
        with self.assertRaises(ValueError):
            GoGame(size=11)
        with self.assertRaises(ValueError):
            GoGame(rules="aga")
        with self.assertRaises(ValueError):
            GoGame(handicap=10)
        with self.assertRaises(IllegalMove):
            GoGame(size=9).play("")

    def test_two_passes_enter_scoring(self):
        g = GoGame(size=9)
        g.pass_turn()
        g.pass_turn()
        self.assertEqual(g.phase, "scoring")
        with self.assertRaises(IllegalMove):
            g.play("E5", BLACK)


class HandicapTests(unittest.TestCase):
    def test_handicap_counts_all_sizes(self):
        for size in (9, 13, 19):
            for n in range(2, 10):
                g = GoGame(size=size, handicap=n)
                self.assertEqual(len(g.initial_stones), n, (size, n))
                self.assertEqual(sum(row.count(BLACK) for row in g.board), n)
                self.assertEqual(g.to_move, WHITE)
                # 让子点必须唯一且全部是黑子
                vertices = [v for _, v in g.initial_stones]
                self.assertEqual(len(set(vertices)), n)
                for v in vertices:
                    self.assertEqual(g.stone_at(v), BLACK)
                h = 3 if size >= 13 else 2
                self.assertEqual(vertices[0], xy_to_vertex(h, size - 1 - h, size))  # 左下星

    def test_handicap_2_4_placement(self):
        g2 = GoGame(size=19, handicap=2)
        self.assertEqual([v for _, v in g2.initial_stones], ["D4", "Q16"])
        g4 = GoGame(size=19, handicap=4)
        self.assertEqual([v for _, v in g4.initial_stones], ["D4", "Q16", "D16", "Q4"])


class ScoringTests(unittest.TestCase):
    def test_chinese_area_scoring(self):
        g = GoGame(size=9, komi=0.5)
        put(g, {"B1": BLACK, "A2": BLACK, "B2": BLACK, "E5": WHITE})
        score = g.score()
        self.assertEqual(score["breakdown"]["black_territory"], 1)
        self.assertEqual(score["breakdown"]["black_stones"], 3)
        self.assertEqual(score["winner"], "B")
        self.assertEqual(score["margin"], 2.5)  # 4 : 1.5
        self.assertEqual(score["rules"], "chinese")

    def test_japanese_scoring_counts_prisoners(self):
        g = GoGame(size=9, komi=6.5, rules="japanese")
        put(g, {"B1": BLACK, "A2": BLACK, "B2": BLACK, "E5": WHITE})
        g.captures[BLACK] = 2
        score = g.score()
        self.assertEqual(score["breakdown"]["black_territory"], 1)
        self.assertEqual(score["breakdown"]["black_captures"], 2)
        self.assertEqual(score["black_score"], 3)
        # 盘上仅一子白棋，四周空域同时接触黑白，不构成白地；只计 6.5 贴目
        self.assertEqual(score["breakdown"]["black_territory"], 1)
        self.assertEqual(score["breakdown"]["white_territory"], 0)
        self.assertEqual(score["white_score"], 6.5)
        self.assertEqual(score["winner"], "W")

    def test_dead_stone_removed_from_board(self):
        g = GoGame(size=9, komi=0.5)
        put(g, {"B1": BLACK, "A2": BLACK, "B2": BLACK, "E5": WHITE})
        base = g.score()
        dead = g.score(dead=["E5"])
        self.assertEqual(dead["dead_white"], 1)
        self.assertLess(dead["white_score"], base["white_score"])
        self.assertNotIn("E5", base["dead"])

    def test_two_color_border_is_neutral(self):
        g = GoGame(size=9, komi=0.5)
        # A1 空点同时接触黑 A2 与白 B1，不属于任何一方
        put(g, {"A2": BLACK, "B1": WHITE})
        self.assertEqual(g._territory(g.board)[1], 0)
        self.assertEqual(g._territory(g.board)[2], 0)

    def test_toggle_dead_by_group(self):
        g = GoGame(size=9)
        put(g, {"E5": WHITE, "E6": WHITE})
        marked = g.toggle_dead("E5")
        self.assertEqual(marked, ["E5", "E6"])  # 整块一起标记
        self.assertEqual(g.toggle_dead("E5"), [])

    def test_auto_dead_from_ownership(self):
        g = GoGame(size=9)
        put(g, {"E5": BLACK, "A1": WHITE})
        ownership = [0.0] * (9 * 9)
        ownership[4 * 9 + 4] = -0.95  # 黑子位置归属为白，判死
        dead = g.auto_dead_from_ownership(ownership)
        self.assertEqual(dead, ["E5"])
        self.assertEqual(g.auto_dead_from_ownership([0.0] * 3), [])

    def test_result_from_score(self):
        g = GoGame(size=9, komi=0.5)
        put(g, {"B1": BLACK, "A2": BLACK, "B2": BLACK, "E5": WHITE})
        result = g.result_from_score()
        self.assertEqual(g.phase, "ended")
        self.assertEqual(result["winner"], "B")
        self.assertIn("黑胜", result["text"])
        self.assertIn("score", result)


class SgfTests(unittest.TestCase):
    def test_sgf_export_basic(self):
        g = GoGame(size=19, komi=7.5)
        g.play("Q16")
        sgf = g.sgf()
        self.assertTrue(sgf.startswith("(;GM[1]FF[4]"))
        self.assertIn("SZ[19]", sgf)
        self.assertIn("KM[7.5]", sgf)
        self.assertIn("RU[Chinese]", sgf)
        self.assertIn(";B[pd]", sgf)

    def test_sgf_pass_and_result(self):
        g = GoGame(size=9, komi=0.5)
        g.play("E5")
        g.pass_turn()
        g.result_from_score()
        sgf = g.sgf()
        self.assertIn(";W[]", sgf)
        self.assertRegex(sgf, r"RE\[[BW]\+")

    @unittest.expectedFailure
    def test_sgf_resign(self):
        """已知缺陷：GoGame.resign() 用 int 比较字符串 player_color，恒判黑胜。

        server.py 的 human_resign 自己算 winner，线上链路不受影响；该函数目前是
        死代码，但 SGF 的 RE 会写反。见 docs/AUDIT-REPORT.md 已知缺陷表。
        """
        g = GoGame(size=9, player_color="B")
        g.play("E5")
        g.resign()
        self.assertIn("RE[W+R]", g.sgf())

    def test_sgf_roundtrip_replay(self):
        """导出 SGF 后再按 SGF 坐标重新落子，最终棋盘必须一致。"""
        g = GoGame(size=19, komi=7.5)
        moves = ["Q16", "D4", "Q4", "D16", "pass", "R17", "C3", "pass"]
        for v in moves:
            if v == "pass":
                g.pass_turn()
            else:
                g.play(v)
        sgf = g.sgf()
        body = sgf[sgf.index(";") + 1:]
        body = body[body.index(";"):]  # 去掉根节点
        replay = GoGame(size=19, komi=7.5)
        seq = re.findall(r";([BW])\[([a-z]{0,2})\]", body)
        self.assertEqual(len(seq), len(moves))
        for color, coord in seq:
            vertex = from_sgf_vertex(coord, 19)
            if vertex == "pass":
                replay.pass_turn()
            else:
                replay.play(vertex)
        self.assertEqual(replay.board_flat(), g.board_flat())
        self.assertEqual(replay.to_move, g.to_move)

    def test_sgf_handicap_roundtrip(self):
        g = GoGame(size=19, handicap=4, komi=0.5)
        g.play("C3")
        sgf = g.sgf()
        self.assertIn("HA[4]", sgf)
        self.assertIn("AB[dp][pd][dd][pp]", sgf)
        replay = GoGame(size=19, handicap=4, komi=0.5)
        ab = re.search(r"AB((?:\[[a-z]{2}\])+)", sgf).group(1)
        coords = re.findall(r"\[([a-z]{2})\]", ab)
        replay_vertices = [from_sgf_vertex(c, 19) for c in coords]
        self.assertEqual(replay_vertices, [v for _, v in replay.initial_stones])

    def test_sgf_escape(self):
        from go_engine import sgf_escape

        self.assertEqual(sgf_escape("a]b\\c"), "a\\]b\\\\c")


class UndoTests(unittest.TestCase):
    def test_undo_one_and_many(self):
        g = GoGame(size=9)
        g.play("E5")
        g.play("D5")
        g.play("F5")
        self.assertEqual(g.undo(1), 1)
        self.assertEqual(len(g.history), 2)
        self.assertEqual(g.stone_at("F5"), 0)
        self.assertEqual(g.undo(5), 2)  # 只撤销实际存在的
        self.assertEqual(len(g.history), 0)
        self.assertEqual(g.to_move, BLACK)

    def test_undo_restores_captures_and_ko(self):
        g = GoGame(size=9)
        put(g, {"D5": BLACK, "F5": BLACK, "E4": BLACK, "E5": WHITE})
        g.to_move = BLACK
        g.play("E6")
        self.assertEqual(g.captures[BLACK], 1)
        g.undo(1)
        self.assertEqual(g.captures[BLACK], 0)
        self.assertEqual(g.stone_at("E5"), WHITE)
        self.assertIsNone(g.ko_hash)

    def test_undo_after_scoring(self):
        g = GoGame(size=9)
        g.play("E5")
        g.pass_turn()
        g.pass_turn()
        self.assertEqual(g.phase, "scoring")
        g.undo(1)
        self.assertEqual(g.phase, "playing")
        self.assertEqual(g.passes, 1)


class ResignTests(unittest.TestCase):
    @unittest.expectedFailure
    def test_resign_player_black(self):
        """已知缺陷：player_color 是 "B" 字符串，与 BLACK=1 比较永假，恒判黑胜。"""
        g = GoGame(size=9, player_color="B")
        result = g.resign()
        self.assertEqual(result["winner"], "W")
        self.assertEqual(g.phase, "ended")
        self.assertIn("认输", result["text"])

    def test_resign_player_white(self):
        """当前实现恒判黑胜；执白认输时“黑胜”恰好正确，故此用例照常通过。"""
        g = GoGame(size=9, player_color="W")
        self.assertEqual(g.resign()["winner"], "B")


class BoardUtilTests(unittest.TestCase):
    def test_board_flat_and_moves_list(self):
        g = GoGame(size=9)
        g.play("E5")
        g.pass_turn()
        flat = g.board_flat()
        self.assertEqual(len(flat), 81)
        self.assertEqual(flat[4 * 9 + 4], "1")
        moves = g.moves_list()
        self.assertEqual(moves[0]["color"], "B")
        self.assertEqual(moves[0]["vertex"], "E5")
        self.assertEqual(moves[1]["vertex"], "pass")
        self.assertEqual(moves[1]["move_no"], 2)

    def test_group_vertices(self):
        g = GoGame(size=9)
        put(g, {"E5": BLACK, "E6": BLACK, "E4": WHITE})
        self.assertEqual(sorted(g.group_vertices("E5")), ["E5", "E6"])
        self.assertEqual(g.group_vertices("A1"), [])


if __name__ == "__main__":
    unittest.main()
