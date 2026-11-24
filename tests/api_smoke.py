"""端到端冒烟测试：启动服务后运行 `python tests/api_smoke.py`。"""
import json
import time
import urllib.request

BASE = "http://127.0.0.1:8765"


def post(path, data=None):
    body = json.dumps(data or {}).encode("utf-8")
    req = urllib.request.Request(BASE + path, data=body, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.load(r)


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=30) as r:
        return json.load(r)


def main():
    status = get("/api/status")["engine"]
    print(f"engine ready={status['ready']} backend={status['backend']} device={status['device']}")
    game = post("/api/new", {
        "size": 19, "player_color": "B", "handicap": 0, "komi": 7.5,
        "rules": "chinese", "level_id": "pro2",
    })
    gid = game["id"]
    print(f"game {gid} level={game['level']['name']}")
    started = time.time()
    game = post(f"/api/game/{gid}/move", {"vertex": "Q16"})
    while time.time() - started < 30:
        time.sleep(0.5)
        game = get(f"/api/game/{gid}")
        if not game["ai_thinking"] and game["turn"] >= 2:
            break
    elapsed = time.time() - started
    assert game["turn"] == 2, f"AI did not move in time: {game['turn']}"
    print(f"AI replied {game['moves'][-1]['vertex']} in {elapsed:.1f}s")
    print(f"eval black={game['eval']['winrate_black']:.3f} lead={game['eval']['score_lead_black']}")
    print(f"candidates={len(game['candidates'])}")
    hint = post(f"/api/game/{gid}/hint")
    print(f"hint={hint['recommended']} candidates={len(hint['candidates'])}")
    assert hint["candidates"], "hint returned no candidates"
    print("SMOKE OK")


if __name__ == "__main__":
    main()
