"""SGF 导入器的回归测试（不依赖引擎/GPU）。"""
import unittest

from sgf_import import SgfError, parse_sgf


class SgfImportTests(unittest.TestCase):
    def test_basic_game(self):
        sgf = "(;GM[1]FF[4]SZ[19]KM[7.5]RU[Chinese]RE[B+R];B[pd];W[dp];B[qp])"
        parsed = parse_sgf(sgf)
        game = parsed["game"]
        self.assertEqual(parsed["size"], 19)
        self.assertEqual(parsed["komi"], 7.5)
        self.assertEqual(parsed["moves"], 3)
        self.assertEqual(game.phase, "ended")
        self.assertEqual(game.result["winner"], "B")
        self.assertIn("中盘胜", game.result["text"])
        self.assertEqual([m.vertex for m in game.history], ["Q16", "D4", "R4"])

    def test_handicap_and_setup_stones(self):
        sgf = "(;GM[1]SZ[9]KM[0.5]HA[2]AB[cc][gg];W[ee];B[ff])"
        parsed = parse_sgf(sgf)
        game = parsed["game"]
        self.assertEqual(parsed["handicap"], 2)
        self.assertEqual(len(game.initial_stones), 2)
        self.assertEqual(game.history[0].color, 2)  # 白棋先行
        self.assertEqual(parsed["moves"], 2)

    def test_variation_is_skipped_but_main_line_kept(self):
        sgf = "(;SZ[19];B[pd];W[dp](;B[qp];W[dd])(;B[dd];W[qp]))"
        parsed = parse_sgf(sgf)
        self.assertEqual(parsed["moves"], 2)
        self.assertEqual([m.vertex for m in parsed["game"].history], ["Q16", "D4"])

    def test_pass_and_result(self):
        sgf = "(;SZ[9]KM[6.5]RU[Japanese]RE[W+3.5];B[ee];W[];B[])"
        parsed = parse_sgf(sgf)
        self.assertEqual(parsed["rules"], "japanese")
        self.assertEqual(parsed["game"].result["winner"], "W")
        self.assertAlmostEqual(parsed["game"].result["margin"], 3.5)
        self.assertEqual(parsed["moves"], 3)

    def test_invalid_color_order(self):
        with self.assertRaises(SgfError):
            parse_sgf("(;SZ[9];B[ee];B[ff])")

    def test_out_of_board(self):
        with self.assertRaises(SgfError):
            parse_sgf("(;SZ[9];B[zz])")

    def test_bad_input(self):
        for text in ("", "not sgf", "(;SZ[19]", "(;SZ[10];B[ee])", "(;SZ[19];B[ee];W[ff]"):
            with self.assertRaises(SgfError, msg=text):
                parse_sgf(text)


if __name__ == "__main__":
    unittest.main()
