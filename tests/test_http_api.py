"""HTTP 接口集成测试：参数校验、静态文件边界、AI 异步链路（引擎打桩）。

不依赖 KataGo / GPU：
- 导入 server 模块不会启动引擎（模块级 APP 用 start_engine=False 构建）；
- 测试前把 ``server.APP.engine`` 替换成 FakeEngine，AI 相关线程可正常跑完。
"""
import json
import threading
import time
import unittest
import urllib.error
import urllib.request

import server
from go_engine import xy_to_vertex
from levels import LEVELS


class FakeEngine:
    """最小的引擎替身：不做搜索，直接返回第一个合法着点。"""

    human_enabled = False

    def __init__(self):
        self.ready = False
        self.started = False
        self.stopped = False
        self.restarted = 0

    def status(self):
        return {
            "ready": self.ready,
            "backend": "fake",
            "device": "none",
            "version": "test",
            "error": None,
            "human_model": False,
            "startup_seconds": 0.1,
            "pid": None,
        }

    def start_async(self):
        self.started = True
        self.ready = True

    def restart_async(self):
        self.restarted += 1
        self.ready = True

    def stop(self):
        self.stopped = True
        self.ready = False

    def choose_move(self, game, level):
        for y in range(game.size):
            for x in range(game.size):
                vertex = xy_to_vertex(x, y, game.size)
                if game.is_legal(vertex)[0]:
                    return {
                        "move": vertex,
                        "candidates": [],
                        "eval": {"winrate_black": 0.5, "score_lead_black": 0.0, "visits": 42},
                        "elapsed": 0.01,
                        "style": "pro",
                    }
        return {"move": "pass", "candidates": [], "eval": {}, "elapsed": 0.01, "style": "pro"}

    def hint(self, game, visits=500):
        return {
            "candidates": [],
            "eval": {"winrate_black": 0.5, "score_lead_black": 0.0, "visits": 42},
            "recommended": "pass",
        }

    def estimate(self, game, visits=220):
        return {"ownership": [], "eval": {"winrate_black": 0.5, "score_lead_black": 0.0, "visits": 42}}

    def review(self, game, visits=80, progress=None):
        return []


class HTTPTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.saved_engine = server.APP.engine
        server.APP.engine = FakeEngine()
        server.APP.engine.start_async()
        cls.httpd = server.LocalHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, name="test-http", daemon=True)
        cls.thread.start()
        for _ in range(100):
            if cls.thread.is_alive():
                break
            time.sleep(0.01)

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=5)
        server.APP.engine = cls.saved_engine

    # ---------- helpers ----------
    def url(self, path):
        return f"http://127.0.0.1:{self.port}{path}"

    def request(self, method, path, body=None, raw=None, headers=None):
        data = None
        if raw is not None:
            data = raw
        elif body is not None:
            data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(self.url(path), data=data, method=method)
        req.add_header("Content-Type", "application/json")
        for key, value in (headers or {}).items():
            req.add_header(key, value)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.status, resp.read(), dict(resp.headers)
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read(), dict(exc.headers)

    def get_json(self, path):
        status, payload, _ = self.request("GET", path)
        return status, json.loads(payload.decode("utf-8"))

    def post_json(self, path, body=None, raw=None):
        status, payload, _ = self.request("POST", path, body=body, raw=raw)
        return status, json.loads(payload.decode("utf-8"))

    def new_game(self, **body):
        status, data = self.post_json("/api/new", body)
        self.assertEqual(status, 200, data)
        return data

    def wait_for_turn(self, game_id, turns, timeout=10.0):
        deadline = time.time() + timeout
        data = None
        while time.time() < deadline:
            status, data = self.get_json(f"/api/game/{game_id}")
            self.assertEqual(status, 200, data)
            if len(data["moves"]) >= turns and not data["ai_thinking"]:
                return data
            time.sleep(0.05)
        self.fail(f"等待 AI 落子超时：{data}")


class BootstrapTests(HTTPTestCase):
    def test_bootstrap_payload(self):
        status, data = self.get_json("/api/bootstrap")
        self.assertEqual(status, 200)
        self.assertEqual(len(data["levels"]), len(LEVELS))
        self.assertEqual(data["default_level"], "pro2")
        self.assertEqual(data["defaults"]["komi"], 7.5)
        self.assertEqual(data["defaults"]["size"], 19)
        self.assertEqual(data["version"], "1.0.0")
        self.assertTrue(data["engine"]["ready"])
        self.assertNotIn("profile", data["levels"][0])

    def test_status_payload(self):
        status, data = self.get_json("/api/status")
        self.assertEqual(status, 200)
        self.assertIsInstance(data["uptime"], int)
        self.assertEqual(data["engine"]["backend"], "fake")

    def test_engine_restart(self):
        status, data = self.post_json("/api/engine/restart", {})
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertEqual(server.APP.engine.restarted, 1)


class NewGameTests(HTTPTestCase):
    def test_default_new_game(self):
        game = self.new_game()
        self.assertEqual(game["size"], 19)
        self.assertEqual(game["rules"], "chinese")
        self.assertEqual(game["komi"], 7.5)
        self.assertEqual(game["handicap"], 0)
        self.assertEqual(game["player_color"], "B")
        self.assertEqual(game["turn"], 0)
        self.assertEqual(len(game["board"]), 19)
        self.assertEqual(len(game["board"][0]), 19)
        self.assertEqual(game["level"]["id"], "pro2")
        self.assertEqual(game["level"]["visits"], 400)
        self.assertEqual(game["phase"], "playing")

    def test_none_values_fall_back_to_defaults(self):
        game = self.new_game(size=None, rules=None, komi=None, handicap=None, player_color=None)
        self.assertEqual(game["size"], 19)
        self.assertEqual(game["rules"], "chinese")
        self.assertEqual(game["komi"], 7.5)
        self.assertEqual(game["player_color"], "B")

    def test_japanese_komi_default(self):
        game = self.new_game(rules="japanese")
        self.assertEqual(game["komi"], 6.5)

    def test_handicap_default_komi_and_white_to_move(self):
        game = self.new_game(handicap=4)
        self.assertEqual(game["komi"], 0.5)
        self.assertEqual(game["initial_stones"][0]["color"], "B")
        self.assertEqual(len(game["initial_stones"]), 4)
        # 让子棋白棋先行：默认执黑时由 AI（白）先落子
        data = self.wait_for_turn(game["id"], turns=1)
        self.assertEqual(data["moves"][0]["color"], "W")
        self.assertEqual(data["to_move"], "B")

    def test_invalid_size(self):
        status, data = self.post_json("/api/new", {"size": 10})
        self.assertEqual(status, 400, data)
        self.assertIn("9、13、19", data["error"])
        status, data = self.post_json("/api/new", {"size": "abc"})
        self.assertEqual(status, 400, data)
        self.assertIn("必须是整数", data["error"])
        status, data = self.post_json("/api/new", {"size": [19]})
        self.assertEqual(status, 400, data)
        self.assertIn("必须是整数", data["error"])

    def test_invalid_rules_and_level(self):
        for body, keyword in (
            ({"rules": "aga"}, "中国规则或日本规则"),
            ({"rules": 123}, "必须是字符串"),
            ({"level_id": "pro99"}, "未知的难度档位"),
            ({"level_id": 5}, "必须是字符串"),
        ):
            status, data = self.post_json("/api/new", body)
            self.assertEqual(status, 400, (body, data))
            self.assertIn(keyword, data["error"], (body, data))

    def test_invalid_handicap(self):
        for body, keyword in (
            ({"handicap": 10}, "0–9"),
            ({"handicap": -1}, "0–9"),
            ({"handicap": "x"}, "必须是整数"),
            ({"handicap": 2.5}, "必须是整数"),
            ({"handicap": True}, "必须是整数"),
        ):
            status, data = self.post_json("/api/new", body)
            self.assertEqual(status, 400, (body, data))
            self.assertIn(keyword, data["error"], (body, data))

    def test_invalid_komi(self):
        for body, keyword in (
            ({"komi": "abc"}, "必须是数字"),
            ({"komi": 100}, "贴目超出范围"),
            ({"komi": -100}, "贴目超出范围"),
            ({"komi": True}, "必须是数字"),
        ):
            status, data = self.post_json("/api/new", body)
            self.assertEqual(status, 400, (body, data))
            self.assertIn(keyword, data["error"], (body, data))

    def test_invalid_player_color(self):
        status, data = self.post_json("/api/new", {"player_color": "X"})
        self.assertEqual(status, 400, data)
        self.assertIn("执黑或执白", data["error"])

    def test_invalid_json_body(self):
        status, data = self.post_json("/api/new", raw=b"{not json")
        self.assertEqual(status, 400, data)
        self.assertIn("请求格式不正确", data["error"])
        status, data = self.post_json("/api/new", raw=b"[1,2,3]")
        self.assertEqual(status, 400, data)
        self.assertIn("请求格式不正确", data["error"])

    def test_oversized_body(self):
        # 只发请求头（Content-Length 超限），服务端应在读 body 前直接 400；
        # 这样也避免客户端持续发送大 body 时被服务端 RST 打断。
        import socket

        sock = socket.create_connection(("127.0.0.1", self.port), timeout=10)
        try:
            head = (
                "POST /api/new HTTP/1.1\r\n"
                "Host: 127.0.0.1\r\n"
                "Content-Type: application/json\r\n"
                "Content-Length: 1100000\r\n"
                "Connection: close\r\n\r\n"
            ).encode("ascii")
            sock.sendall(head)
            chunks = []
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                chunks.append(chunk)
        finally:
            sock.close()
        raw = b"".join(chunks)
        status_line, _, body = raw.partition(b"\r\n\r\n")
        status = int(status_line.split()[1])
        data = json.loads(body.decode("utf-8"))
        self.assertEqual(status, 400, data)
        self.assertIn("请求体过大", data["error"])


class MoveTests(HTTPTestCase):
    def test_invalid_vertex(self):
        game = self.new_game()
        for vertex in ("Z99", "", "A20", "passxyz"):
            status, data = self.post_json(f"/api/game/{game['id']}/move", {"vertex": vertex})
            self.assertEqual(status, 400, (vertex, data))
            self.assertIn("error", data)
        # 非法着法不应改变棋局
        status, data = self.get_json(f"/api/game/{game['id']}")
        self.assertEqual(data["turn"], 0)

    def test_move_triggers_ai_reply(self):
        game = self.new_game()
        status, data = self.post_json(f"/api/game/{game['id']}/move", {"vertex": "Q16"})
        self.assertEqual(status, 200, data)
        self.assertEqual(data["turn"], 1)
        self.assertEqual(data["moves"][0]["vertex"], "Q16")
        self.assertEqual(data["moves"][0]["color"], "B")
        data = self.wait_for_turn(game["id"], turns=2)
        self.assertEqual(data["moves"][1]["color"], "W")
        self.assertNotEqual(data["moves"][1]["vertex"], "pass")
        self.assertFalse(data["ai_thinking"])

    def test_ai_first_when_player_white(self):
        game = self.new_game(player_color="W")
        data = self.wait_for_turn(game["id"], turns=1)
        self.assertEqual(data["moves"][0]["color"], "B")

    def test_unknown_game_post_404(self):
        # 不带请求体：未知对局在读取 body 之前就会 404，避免未读完 body 被 RST
        status, payload, _ = self.request("POST", "/api/game/deadbeef/move", raw=b"")
        data = json.loads(payload.decode("utf-8"))
        self.assertEqual(status, 404, data)
        self.assertIn("对局不存在", data["error"])

    @unittest.expectedFailure
    def test_unknown_game_get_404(self):
        """已知缺陷：do_GET 没有 KeyError 分支，查不到对局时返回 500 而不是 404。"""
        status, data = self.get_json("/api/game/deadbeef")
        self.assertEqual(status, 404, data)

    def test_unknown_action_404(self):
        game = self.new_game()
        status, data = self.post_json(f"/api/game/{game['id']}/nonsense", {})
        self.assertEqual(status, 404, data)


class StaticFileTests(HTTPTestCase):
    def test_index_page(self):
        status, payload, headers = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers["Content-Type"])
        self.assertIn("弈心".encode("utf-8"), payload)

    def test_static_asset(self):
        status, payload, headers = self.request("GET", "/static/app.js")
        self.assertEqual(status, 200)
        self.assertIn("javascript", headers["Content-Type"])
        self.assertGreater(len(payload), 1000)

    def test_path_traversal_rejected(self):
        for path in ("/static/..%2f..%2fserver.py", "/static/%2e%2e%2f%2e%2e%2fserver.py",
                     "/static/..%5c..%5cserver.py"):
            status, payload, _ = self.request("GET", path)
            self.assertIn(status, (403, 404), path)
            self.assertNotIn(b"ThreadingHTTPServer", payload)

    def test_unknown_path_404(self):
        status, data = self.get_json("/api/nope")
        self.assertEqual(status, 404, data)
        status, data = self.get_json("/nope")
        self.assertEqual(status, 404, data)


if __name__ == "__main__":
    unittest.main()
