"""弈心 · 围棋陪练 —— 本地 HTTP 服务。

纯标准库实现，负责棋局状态、KataGo 调度、提示/复盘、SGF 与静态页面。

命令行参数见 `python server.py --help`：可以指定端口、引擎路径、模型目录、
日志级别，也可以禁止自动打开浏览器。
"""
from __future__ import annotations

import argparse
import copy
import json
import logging
import os
import random
import re
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from go_engine import BLACK, WHITE, GoGame, IllegalMove
from katago_engine import AnalysisEngine, EngineError
from levels import DEFAULT_LEVEL, LEVELS, LEVEL_BY_ID, public_levels

LOG = logging.getLogger("yixin")

_PYTHON_EXE_RE = re.compile(r"^python(?:\d+(?:\.\d+)*)?w?(?:\.exe)?$", re.IGNORECASE)


def _is_python_executable(path: Path) -> bool:
    """判断 sys.executable 是解释器本身还是打包后的 Exe。

    Windows 解释器叫 python.exe / pythonw.exe，Linux 与 macOS 上叫 python3.13
    这类名字；之前只认前两种，源码运行时会把 BASE 错算到解释器目录，
    于是 web/、data/、engine/、models/ 全部找不到。
    """
    return bool(_PYTHON_EXE_RE.match(path.name))


_EXE = Path(sys.executable).resolve()
_IS_FROZEN = getattr(sys, "frozen", False) or not _is_python_executable(_EXE)
BASE = _EXE.parent if _IS_FROZEN else Path(__file__).resolve().parent
WEB = BASE / "web"
DATA = BASE / "data"
GAMES_DIR = DATA / "games"


def ensure_data_dirs() -> None:
    """建好 data/games。放在程序安装目录只读时也不至于让服务起不来。"""
    try:
        GAMES_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        LOG.warning("无法创建棋谱目录 %s：%s（自动保存将不可用）", GAMES_DIR, exc)


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


def flat_board(board: list[list[int]]) -> str:
    return "".join(str(v) for row in board for v in row)


def move_quality(loss: float) -> str:
    if loss <= 0.012:
        return "好棋"
    if loss <= 0.035:
        return "正常"
    if loss <= 0.075:
        return "稍缓"
    if loss <= 0.16:
        return "失误"
    return "严重失误"


def quality_tone(q: str) -> str:
    return {"好棋": "good", "正常": "normal", "稍缓": "warn", "失误": "bad", "严重失误": "bad"}.get(q, "normal")


def display_winrate(raw: float, lead_black: float) -> float:
    return AnalysisEngine._calibrate_winrate(raw, lead_black)


# ---------- 请求参数校验 ----------
# 前端传来的 JSON 可能是 null、数组或字符串；直接 int() 会抛 TypeError，
# 之前会被兜底成 500 并带出 Python 内部错误信息。这里统一成 400 + 中文提示。

def _int_field(body: dict, key: str, default, label: str) -> int:
    value = body.get(key, default)
    if value is None:
        value = default
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError(f"{label}必须是整数")
    try:
        return int(str(value).strip())
    except ValueError:
        raise ValueError(f"{label}必须是整数") from None


def _float_field(body: dict, key: str, default, label: str) -> float:
    value = body.get(key, default)
    if value is None:
        value = default
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError(f"{label}必须是数字")
    try:
        number = float(str(value).strip())
    except ValueError:
        raise ValueError(f"{label}必须是数字") from None
    if number != number or number in (float("inf"), float("-inf")):
        raise ValueError(f"{label}必须是有限数字")
    return number


def _str_field(body: dict, key: str, default: str, label: str) -> str:
    value = body.get(key, default)
    if value is None:
        value = default
    if not isinstance(value, str):
        raise ValueError(f"{label}必须是字符串")
    return value


class GameSession:
    def __init__(self, game: GoGame, level: dict, player_color: str, gen_id: str | None = None):
        self.id = gen_id or uuid.uuid4().hex[:10]
        self.game = game
        self.level = level
        self.player_color = "B" if player_color == "B" else "W"
        self.ai_color = "W" if self.player_color == "B" else "B"
        self.created_at = now_iso()
        self.started = time.time()
        self.lock = threading.RLock()
        self.generation = 0
        self.ai_thinking = False
        self.ai_error: str | None = None
        self.ai_elapsed: float | None = None
        self.ai_search_visits: int | None = None
        self.eval: dict | None = None
        self.candidates: list[dict] = []
        self.hint: dict | None = None
        self.ownership: list[float] | None = None
        self.commentary: list[dict] = []
        self.pending_human: dict | None = None
        self.last_feedback: dict | None = None
        self.scoring_estimating = False
        self.scoring_error: str | None = None
        self.review: dict | None = None
        self.saved = False
        self.add_comment(
            f"对局开始。你执{'黑' if self.player_color == 'B' else '白'}，"
            f"对手：弈心 · {level['name']}。",
            "system",
        )
        if game.handicap:
            self.add_comment(f"让 {game.handicap} 子局，白棋先行，贴目 {game.komi:g}。", "system")

    # ---------- 状态 ----------
    @property
    def phase(self) -> str:
        return self.game.phase

    def add_comment(self, text: str, tone: str = "info", move_no: int | None = None) -> None:
        self.commentary.append({
            "id": uuid.uuid4().hex[:8],
            "text": text,
            "tone": tone,
            "move_no": move_no if move_no is not None else len(self.game.history),
            "at": now_iso(),
        })
        if len(self.commentary) > 40:
            self.commentary = self.commentary[-40:]

    def can_undo(self) -> bool:
        if self.game.phase != "playing" or not self.game.history:
            return False
        last = self.game.history[-1]
        if last.color == self.player_color:
            return True
        return len(self.game.history) >= 2 and self.game.history[-2].color == self.player_color

    def serialize(self) -> dict:
        with self.lock:
            g = self.game
            score = g.scoring if g.phase == "scoring" else None
            scoring = None
            if g.phase == "scoring":
                scoring = {
                    "estimating": self.scoring_estimating,
                    "error": self.scoring_error,
                    "dead": sorted(g.dead_stones),
                    "score": score or g.score(),
                }
            review = None
            if self.review:
                review = {
                    "status": self.review.get("status"),
                    "progress": self.review.get("progress", 0),
                    "total": self.review.get("total", len(g.history) + 1),
                    "visits": self.review.get("visits"),
                    "summary": self.review.get("summary"),
                    "error": self.review.get("error"),
                }
            return {
                "id": self.id,
                "created_at": self.created_at,
                "size": g.size,
                "rules": g.rules,
                "komi": g.komi,
                "handicap": g.handicap,
                "player_color": self.player_color,
                "ai_color": self.ai_color,
                "level": {k: v for k, v in self.level.items() if k not in ("profile",)},
                "board": g.board,
                "board_flat": g.board_flat(),
                "moves": g.moves_list(),
                "initial_stones": [
                    {"color": "B" if c == BLACK else "W", "vertex": v} for c, v in g.initial_stones
                ],
                "to_move": "B" if g.to_move == BLACK else "W",
                "turn": len(g.history),
                "captures": {"B": g.captures[BLACK], "W": g.captures[WHITE]},
                "last_move": g.last_move,
                "last_move_color": "B" if g.last_move_color == BLACK else ("W" if g.last_move_color == WHITE else None),
                "phase": g.phase,
                "result": g.result,
                "ai_thinking": self.ai_thinking,
                "ai_error": self.ai_error,
                "ai_elapsed": self.ai_elapsed,
                "ai_search_visits": self.ai_search_visits,
                "eval": self.eval or {"winrate_black": 0.5, "score_lead_black": 0.0, "visits": 0},
                "candidates": self.candidates,
                "hint": self.hint,
                "ownership": self.ownership,
                "commentary": self.commentary,
                "scoring": scoring,
                "review": review,
                "can_undo": self.can_undo(),
                "elapsed": int(time.time() - self.started),
                "saved": self.saved,
            }

    def _eval_after_move(self, result: dict, move: str) -> dict:
        for c in result.get("candidates", []):
            if c.get("move") == move:
                return {
                    "winrate_black": c.get("winrate", 0.5),
                    "score_lead_black": c.get("score_lead", 0.0),
                    "visits": result.get("eval", {}).get("visits", 0),
                    "best_move": move,
                }
        return result.get("eval") or {"winrate_black": 0.5, "score_lead_black": 0.0, "visits": 0}

    # ---------- AI ----------
    def start_ai(self) -> None:
        with self.lock:
            if self.game.phase != "playing" or self.game.to_move == (BLACK if self.player_color == "B" else WHITE):
                return
            if self.ai_thinking:
                return
            self.ai_thinking = True
            self.ai_error = None
            gen = self.generation
            snap = copy.deepcopy(self.game)
            level = dict(self.level)
            pending = self.pending_human
        threading.Thread(
            target=self._ai_worker, args=(gen, snap, level, pending), name=f"ai-{self.id}", daemon=True
        ).start()

    def _ai_worker(self, gen: int, snap: GoGame, level: dict, pending: dict | None) -> None:
        started = time.time()
        try:
            result = APP.engine.choose_move(snap, level)
        except Exception as exc:
            with self.lock:
                if gen == self.generation:
                    self.ai_thinking = False
                    self.ai_error = str(exc)
                    self.add_comment(f"AI 计算中断：{exc}", "bad")
            return
        with self.lock:
            if gen != self.generation or self.game.phase != "playing":
                return
            move = result.get("move", "pass")
            # 防止引擎给出的着法与本地规则冲突
            if move != "pass":
                ok, msg = self.game.is_legal(move)
                if not ok:
                    legal = [c for c in result.get("candidates", []) if c.get("move") == "pass" or self.game.is_legal(c.get("move", ""))[0]]
                    move = legal[0]["move"] if legal else "pass"
                    self.add_comment(f"AI 着法 {result.get('move')} 与本地规则冲突（{msg}），已改选 {move}。", "warn")
            try:
                if move == "pass":
                    self.game.pass_turn()
                else:
                    self.game.play(move)
            except IllegalMove as exc:
                self.ai_thinking = False
                self.ai_error = f"AI 着法无法执行：{exc}"
                self.add_comment(self.ai_error, "bad")
                return
            self.ai_thinking = False
            self.ai_elapsed = round(time.time() - started, 2)
            self.ai_search_visits = (result.get("eval") or {}).get("visits")
            if move == "pass":
                self.eval = result.get("eval", self.eval)
                self.candidates = []
            else:
                self.eval = self._eval_after_move(result, move)
                self.candidates = []
            self._consume_human_feedback(pending, result)
            if move == "pass":
                self.add_comment(f"AI 停一手（{self.ai_search_visits or level.get('visits') or '?'} visits，用时 {self.ai_elapsed}s）。", "ai")
            else:
                wr = (self.eval or {}).get("winrate_black", 0.5)
                lead = (self.eval or {}).get("score_lead_black", 0.0)
                self.add_comment(
                    f"AI 落子 {move}（{self.ai_search_visits or level.get('visits') or '?'} visits，用时 {self.ai_elapsed}s）。当前黑棋胜率 {wr*100:.1f}%，"
                    f"{'领先' if lead >= 0 else '落后'} {abs(lead):.1f} 目。",
                    "ai",
                )
            if self.game.phase == "scoring":
                self._begin_scoring_locked()
                return
        # 思考结束后，后台做一个轻量形势分析，供下一手提示/胜率显示
        self._start_eval_worker(gen)

    def _consume_human_feedback(self, pending: dict | None, result: dict) -> None:
        if not pending:
            return
        self.pending_human = None
        move = pending.get("move")
        before = pending.get("eval_before") or {}
        after = result.get("eval") or {}
        human_is_black = self.player_color == "B"
        before_wr = before.get("winrate_black", 0.5)
        after_wr = after.get("winrate_black", 0.5)
        if human_is_black:
            loss = max(0.0, before_wr - after_wr)
        else:
            loss = max(0.0, after_wr - before_wr)
        cands = pending.get("candidates_before") or []
        rank = next((i for i, c in enumerate(cands) if c.get("move") == move), None)
        best = cands[0]["move"] if cands else None
        self.last_feedback = {
            "move": move,
            "loss": round(loss, 4),
            "best": best,
            "rank": rank,
            "move_no": len(self.game.history),
        }
        if move == "pass":
            self.add_comment("你选择停一手。", "info")
            return
        if rank == 0:
            self.add_comment(f"{move} 与 AI 首选一致，好手。", "good")
        elif rank is not None and rank <= 2:
            self.add_comment(f"{move} 方向正确，AI 首选是 {best}。", "good")
        elif loss >= 0.12 and best:
            self.add_comment(
                f"{move} 这手损失约 {loss*100:.1f}% 胜率，AI 更看好 {best}。",
                "bad",
            )
        elif loss >= 0.05 and best:
            self.add_comment(f"{move} 稍缓，AI 建议 {best}（约 {loss*100:.1f}% 胜率差）。", "warn")
        elif best:
            self.add_comment(f"{move} 可行，AI 首选为 {best}。", "info")

    def _start_eval_worker(self, gen: int) -> None:
        threading.Thread(target=self._eval_worker, args=(gen,), name=f"eval-{self.id}", daemon=True).start()

    def _eval_worker(self, gen: int) -> None:
        with self.lock:
            if gen != self.generation or self.game.phase != "playing":
                return
            snap = copy.deepcopy(self.game)
        try:
            result = APP.engine.hint(snap, visits=90)
        except Exception:
            return
        with self.lock:
            if gen != self.generation or self.game.phase != "playing":
                return
            if result.get("candidates"):
                self.candidates = result["candidates"][:5]
            if result.get("eval"):
                self.eval = result["eval"]

    # ---------- 人工操作 ----------
    def human_move(self, vertex: str) -> dict:
        with self.lock:
            if self.game.phase != "playing":
                raise ValueError("当前不在对局阶段")
            if self.ai_thinking:
                raise ValueError("AI 正在思考，请稍候")
            if self.game.to_move != (BLACK if self.player_color == "B" else WHITE):
                raise ValueError("还没有轮到你")
            prev_eval = copy.deepcopy(self.eval)
            cands = copy.deepcopy(self.candidates)
            self.game.play(vertex)
            self.generation += 1
            self.pending_human = {
                "move": vertex,
                "eval_before": prev_eval,
                "candidates_before": cands,
            }
            self.hint = None
            self.ownership = None
            self.ai_error = None
            self.start_ai()
            return self.serialize()

    def human_pass(self) -> dict:
        with self.lock:
            if self.game.phase != "playing":
                raise ValueError("当前不在对局阶段")
            if self.ai_thinking:
                raise ValueError("AI 正在思考，请稍候")
            if self.game.to_move != (BLACK if self.player_color == "B" else WHITE):
                raise ValueError("还没有轮到你")
            prev_eval = copy.deepcopy(self.eval)
            cands = copy.deepcopy(self.candidates)
            self.game.pass_turn()
            self.generation += 1
            self.pending_human = {
                "move": "pass",
                "eval_before": prev_eval,
                "candidates_before": cands,
            }
            self.hint = None
            self.ownership = None
            if self.game.phase == "scoring":
                self._begin_scoring_locked()
            else:
                self.start_ai()
            return self.serialize()

    def human_resign(self) -> dict:
        with self.lock:
            if self.game.phase == "ended":
                raise ValueError("对局已经结束")
            winner = "W" if self.player_color == "B" else "B"
            self.game.phase = "ended"
            self.game.result = {
                "winner": winner,
                "reason": "resign",
                "text": f"{'白' if winner == 'W' else '黑'}棋中盘胜（你认输）",
                "margin": None,
            }
            self.generation += 1
            self.ai_thinking = False
            self.add_comment("你选择认输。胜负已定，可以开始复盘。", "system")
            self._save_locked()
            return self.serialize()

    def undo(self) -> dict:
        with self.lock:
            if self.game.phase != "playing":
                raise ValueError("数子或终局阶段不能悔棋")
            if not self.game.history:
                raise ValueError("还没有落子")
            if self.ai_thinking:
                self.generation += 1
                self.ai_thinking = False
            last = self.game.history[-1]
            if last.color == (BLACK if self.player_color == "B" else WHITE):
                plies = 1
            elif len(self.game.history) >= 2 and self.game.history[-2].color == (BLACK if self.player_color == "B" else WHITE):
                plies = 2
            else:
                raise ValueError("这手棋是 AI 先手，暂时无法悔棋")
            self.game.undo(plies)
            self.generation += 1
            self.game.phase = "playing"
            self.game.result = None
            self.game.passes = 0
            self.game.dead_stones.clear()
            self.game.scoring = None
            self.eval = None
            self.candidates = []
            self.hint = None
            self.ownership = None
            self.pending_human = None
            self.last_feedback = None
            self.scoring_estimating = False
            self.scoring_error = None
            self.add_comment(f"悔棋 {plies} 手，轮到你重新思考。", "system")
            return self.serialize()

    def retry_ai(self) -> dict:
        with self.lock:
            if self.game.phase != "playing":
                raise ValueError("当前不在对局阶段")
            if self.ai_thinking:
                return self.serialize()
            if self.game.to_move == (BLACK if self.player_color == "B" else WHITE):
                raise ValueError("现在轮到你落子")
            self.ai_error = None
            self.start_ai()
            return self.serialize()

    def get_hint(self) -> dict:
        with self.lock:
            if self.game.phase != "playing":
                raise ValueError("当前不在对局阶段")
            if self.ai_thinking:
                raise ValueError("AI 正在思考，请稍候")
            snap = copy.deepcopy(self.game)
        hint = APP.engine.hint(snap, visits=500)
        with self.lock:
            self.hint = hint
            if hint.get("eval"):
                self.eval = hint["eval"]
            return hint

    def estimate(self) -> dict:
        with self.lock:
            if self.game.phase not in ("playing", "scoring"):
                raise ValueError("当前无法进行形势判断")
            snap = copy.deepcopy(self.game)
        est = APP.engine.estimate(snap, visits=220)
        with self.lock:
            self.ownership = est.get("ownership") or []
            if est.get("eval"):
                self.eval = est["eval"]
            return est

    # ---------- 数子 ----------
    def _begin_scoring_locked(self) -> None:
        if self.game.phase != "scoring" or self.scoring_estimating:
            return
        self.scoring_estimating = True
        self.scoring_error = None
        gen = self.generation
        snap = copy.deepcopy(self.game)
        self.add_comment("双方停一手，进入数子。AI 正在判断死活……", "system")
        threading.Thread(target=self._scoring_worker, args=(gen, snap), name=f"score-{self.id}", daemon=True).start()

    def _scoring_worker(self, gen: int, snap: GoGame) -> None:
        try:
            est = APP.engine.estimate(snap, visits=300)
            dead = snap.auto_dead_from_ownership(est.get("ownership") or [])
            with self.lock:
                if gen != self.generation or self.game.phase != "scoring":
                    return
                self.game.dead_stones = set(dead)
                self.game.scoring = self.game.score()
                self.ownership = est.get("ownership") or []
                self.scoring_estimating = False
                self.add_comment("AI 已标出死子，点击棋子可以增删，确认后结束对局。", "system")
        except Exception as exc:
            with self.lock:
                if gen != self.generation:
                    return
                self.scoring_estimating = False
                self.scoring_error = str(exc)
                self.game.scoring = self.game.score()
                self.add_comment(f"数子时 AI 判断失败，使用盘面计算：{exc}", "warn")

    def reestimate_scoring(self) -> dict:
        with self.lock:
            if self.game.phase != "scoring":
                raise ValueError("当前不在数子阶段")
            if self.scoring_estimating:
                raise ValueError("AI 正在数子，请稍候")
            self.scoring_estimating = False
            self.add_comment("重新估算死活。", "system")
            self._begin_scoring_locked()
            return self.serialize()

    def toggle_dead(self, vertex: str) -> dict:
        with self.lock:
            if self.game.phase != "scoring":
                raise ValueError("当前不在数子阶段")
            if self.scoring_estimating:
                raise ValueError("AI 正在数子，请稍候")
            self.game.toggle_dead(vertex)
            self.game.scoring = self.game.score()
            return self.serialize()

    def confirm_score(self) -> dict:
        with self.lock:
            if self.game.phase != "scoring":
                raise ValueError("当前不在数子阶段")
            if self.scoring_estimating:
                raise ValueError("AI 正在数子，请稍候")
            self.game.result_from_score()
            self.generation += 1
            self.add_comment(f"数子结果：{self.game.result['text']}。", "result")
            self._save_locked()
            return self.serialize()

    def resume_scoring(self) -> dict:
        with self.lock:
            if self.game.phase != "scoring":
                raise ValueError("当前不在数子阶段")
            self.game.phase = "playing"
            self.game.passes = 0
            self.game.dead_stones.clear()
            self.game.scoring = None
            self.scoring_estimating = False
            self.scoring_error = None
            self.ownership = None
            last = self.game.history[-1] if self.game.history else None
            if last:
                self.game.to_move = WHITE if last.color == BLACK else BLACK
            self.generation += 1
            self.add_comment("继续对局。", "system")
            self.start_ai()
            return self.serialize()

    # ---------- 复盘 ----------
    def start_review(self) -> dict:
        with self.lock:
            if self.game.phase != "ended":
                raise ValueError("请先结束当前对局再复盘")
            if len(self.game.history) == 0:
                raise ValueError("还没有落子，无法复盘")
            if self.review and self.review.get("status") == "running":
                return self.serialize()
            total = len(self.game.history) + 1
            visits = max(35, min(120, 12000 // max(1, len(self.game.history))))
            self.review = {
                "status": "running",
                "progress": 0,
                "total": total,
                "visits": visits,
                "items": [],
                "summary": None,
                "error": None,
                "started": time.time(),
            }
            snap = copy.deepcopy(self.game)
            boards = self._review_boards()
            self.add_comment(f"开始复盘：共 {len(self.game.history)} 手，AI 每手搜索 {visits} 次。", "system")
            threading.Thread(
                target=self._review_worker, args=(snap, boards, visits), name=f"review-{self.id}", daemon=True
            ).start()
            return self.serialize()

    def _review_boards(self) -> list[str]:
        g = self.game
        boards = []
        for i in range(len(g.history)):
            if i < len(g._snapshots):
                boards.append(flat_board(g._snapshots[i]["board"]))
            else:
                boards.append(flat_board(g.board))
        boards.append(flat_board(g.board))
        return boards

    def _review_worker(self, snap: GoGame, boards: list[str], visits: int) -> None:
        def progress(data, done, total):
            with self.lock:
                if self.review:
                    self.review["progress"] = done
                    self.review["total"] = total

        try:
            results = APP.engine.review(snap, visits=visits, progress=progress)
            by_turn = {int(r.get("turnNumber", 0)): r for r in results}
            items = self._build_review_items(snap, boards, by_turn)
            summary = self._review_summary(items, snap)
            with self.lock:
                if not self.review:
                    return
                self.review.update({
                    "status": "done",
                    "progress": len(snap.history) + 1,
                    "total": len(snap.history) + 1,
                    "items": items,
                    "summary": summary,
                    "elapsed": round(time.time() - self.review.get("started", time.time()), 1),
                })
                self.add_comment(
                    f"复盘完成：平均胜率损失 {summary['avg_loss']*100:.1f}%，"
                    f"前三选吻合率 {summary['top3_rate']*100:.0f}%。",
                    "system",
                )
                self._save_locked()
        except Exception as exc:
            with self.lock:
                if self.review:
                    self.review["status"] = "error"
                    self.review["error"] = str(exc)
                self.add_comment(f"复盘中断：{exc}", "bad")

    def _build_review_items(self, snap: GoGame, boards: list[str], by_turn: dict[int, dict]) -> list[dict]:
        items = []
        for t, rec in enumerate(snap.history):
            result = by_turn.get(t, {})
            nxt = by_turn.get(t + 1, {})
            infos = result.get("moveInfos", [])
            if not infos:
                continue
            current = result.get("rootInfo", {}).get("currentPlayer", "B")
            best = min(infos, key=lambda x: x.get("order", 99))
            actual = next((x for x in infos if x.get("move") == rec.vertex), None)
            ordered = sorted(infos, key=lambda x: x.get("order", 99))
            rank = next((idx for idx, item in enumerate(ordered) if item.get("move") == rec.vertex), None)
            best_lead = float(best.get("scoreLead", 0.0))
            best_wr_raw = float(best.get("winrate", 0.5))
            if actual:
                actual_wr_raw = float(actual.get("winrate", best_wr_raw))
                actual_lead = float(actual.get("scoreLead", 0.0))
            else:
                nr = nxt.get("rootInfo", {})
                actual_wr_raw = float(nr.get("winrate", best_wr_raw))
                actual_lead = float(nr.get("scoreLead", 0.0))
            best_wr_black = display_winrate(best_wr_raw, best_lead)
            actual_wr_black = display_winrate(actual_wr_raw, actual_lead)
            mover = rec.color
            best_wr = best_wr_black if mover == BLACK else 1 - best_wr_black
            actual_wr = actual_wr_black if mover == BLACK else 1 - actual_wr_black
            loss = max(0.0, best_wr - actual_wr)
            quality = move_quality(loss)
            items.append({
                "turn": t + 1,
                "move_no": rec.move_no,
                "color": "B" if rec.color == BLACK else "W",
                "move": rec.vertex,
                "captures": rec.captures,
                "winrate_black": round(actual_wr_black, 4),
                "winrate_raw": round(actual_wr_raw, 4),
                "score_lead_black": round(actual_lead, 2),
                "best_move": best.get("move", "pass"),
                "best_winrate_black": round(best_wr_black, 4),
                "loss": round(loss, 4),
                "quality": quality,
                "tone": quality_tone(quality),
                "rank": (rank + 1) if rank is not None else None,
                "pv": best.get("pv", [])[:8],
                "board": boards[t + 1] if t + 1 < len(boards) else boards[-1],
            })
        return items

    @staticmethod
    def _review_summary(items: list[dict], snap: GoGame) -> dict:
        if not items:
            return {"avg_loss": 0, "top3_rate": 0, "counts": {}, "key_mistakes": []}
        avg_loss = sum(i["loss"] for i in items) / len(items)
        top3 = sum(1 for i in items if i.get("rank") and i["rank"] <= 3)
        counts = {}
        for i in items:
            counts[i["quality"]] = counts.get(i["quality"], 0) + 1
        mistakes = sorted(
            [i for i in items if i["loss"] >= 0.075], key=lambda x: x["loss"], reverse=True
        )[:6]
        return {
            "avg_loss": round(avg_loss, 4),
            "top3_rate": round(top3 / len(items), 4),
            "counts": counts,
            "key_mistakes": mistakes,
            "total_moves": len(items),
            "result": snap.result.get("text") if snap.result else None,
        }

    def review_payload(self) -> dict:
        with self.lock:
            if not self.review:
                return {"status": "none"}
            data = dict(self.review)
            if data.get("status") != "done":
                data.pop("items", None)
            return data

    # ---------- 保存 ----------
    def _save_locked(self) -> None:
        try:
            g = self.game
            payload = {
                "id": self.id,
                "created_at": self.created_at,
                "level": self.level.get("name"),
                "level_id": self.level.get("id"),
                "size": g.size,
                "rules": g.rules,
                "komi": g.komi,
                "handicap": g.handicap,
                "player_color": self.player_color,
                "moves": g.moves_list(),
                "initial_stones": [{"color": "B" if c == BLACK else "W", "vertex": v} for c, v in g.initial_stones],
                "result": g.result,
                "sgf": g.sgf(),
                "review_summary": self.review.get("summary") if self.review else None,
            }
            stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(self.started))
            path = GAMES_DIR / f"{stamp}-{self.id}.json"
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(path)
            self.saved = True
        except Exception as exc:
            # 保存失败不应该影响对局，但要在日志里留下原因
            LOG.warning("保存棋谱失败：%s", exc)


class Application:
    def __init__(
        self,
        start_engine: bool = True,
        engine_path: str | None = None,
        models_dir: str | None = None,
    ) -> None:
        self.engine = AnalysisEngine(human_model=True, engine_path=engine_path, models_dir=models_dir)
        self.games: dict[str, GameSession] = {}
        self.lock = threading.RLock()
        self.started = time.time()
        ensure_data_dirs()
        if start_engine:
            self.engine.start_async()

    def new_game(self, body: dict) -> GameSession:
        size = _int_field(body, "size", 19, "棋盘大小")
        if size not in (9, 13, 19):
            raise ValueError("棋盘只支持 9、13、19 路")
        rules = _str_field(body, "rules", "chinese", "规则")
        if rules not in ("chinese", "japanese"):
            raise ValueError("规则只支持中国规则或日本规则")
        level_id = _str_field(body, "level_id", DEFAULT_LEVEL, "难度档位")
        level = LEVEL_BY_ID.get(level_id)
        if not level:
            raise ValueError("未知的难度档位")
        handicap = _int_field(body, "handicap", 0, "让子数")
        if not 0 <= handicap <= 9:
            raise ValueError("让子数应为 0–9")
        player_color = _str_field(body, "player_color", "B", "执子颜色")
        if player_color not in ("B", "W"):
            raise ValueError("请选择执黑或执白")
        default_komi = 0.5 if handicap else (7.5 if rules == "chinese" else 6.5)
        komi = _float_field(body, "komi", default_komi, "贴目")
        if not -10 <= komi <= 50:
            raise ValueError("贴目超出范围")
        game = GoGame(size=size, komi=komi, rules=rules, handicap=handicap, player_color=player_color, level_id=level_id)
        session = GameSession(game, level, player_color)
        with self.lock:
            if len(self.games) > 80:
                oldest = sorted(self.games.values(), key=lambda s: s.started)[:20]
                for s in oldest:
                    self.games.pop(s.id, None)
            self.games[session.id] = session
        if game.to_move != (BLACK if player_color == "B" else WHITE):
            session.start_ai()
        return session

    def get(self, game_id: str) -> GameSession:
        with self.lock:
            session = self.games.get(game_id)
        if not session:
            raise KeyError("对局不存在或已过期")
        return session

    def recent_games(self) -> list[dict]:
        out = []
        try:
            paths = sorted(GAMES_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:12]
        except OSError as exc:
            LOG.warning("读取棋谱目录失败：%s", exc)
            return out
        for path in paths:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                # 单个文件损坏不能让整个“最近棋谱”列表变空
                LOG.warning("跳过损坏的棋谱 %s：%s", path.name, exc)
                continue
            if not isinstance(data, dict):
                LOG.warning("跳过格式不正确的棋谱 %s", path.name)
                continue
            result = data.get("result") or {}
            if not isinstance(result, dict):
                result = {}
            try:
                move_count = len(data.get("moves") or [])
            except TypeError:
                move_count = 0
            out.append({
                "id": data.get("id"),
                "created_at": data.get("created_at"),
                "level": data.get("level"),
                "size": data.get("size"),
                "moves": move_count,
                "result": result.get("text"),
                "winner": result.get("winner"),
                "file": path.name,
            })
        return out


# 模块导入时不启动引擎：--help、单元测试、`import server` 都不应该拉起 KataGo。
# 真正的启动在 main() 里，按命令行参数创建引擎。
APP = Application(start_engine=False)


class Handler(BaseHTTPRequestHandler):
    server_version = "YixinGoCoach/1.0"
    # 客户端连上却迟迟不发完整请求时，别让处理线程一直挂在 read 上
    timeout = 30

    def log_message(self, fmt, *args):
        # 访问日志用 INFO：默认 --log-level warning 时终端保持清爽，调试时可打开
        LOG.info("%s - %s", self.address_string(), fmt % args)

    def log_error(self, fmt, *args):
        LOG.warning("%s - %s", self.address_string(), fmt % args)

    def _send(self, body: bytes, status: int = 200, content_type: str = "application/json; charset=utf-8",
              extra: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if extra:
            for k, v in extra.items():
                self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, data, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self._send(body, status)

    def _error(self, message: str, status: int = 400) -> None:
        if status >= 500:
            LOG.error("%s %s -> %s：%s", getattr(self, "command", "?"), self.path, status, message)
        elif status == 404:
            LOG.debug("%s %s -> 404", getattr(self, "command", "?"), self.path)
        self._json({"error": message}, status)

    def _read_json(self) -> dict:
        raw_length = self.headers.get("Content-Length")
        if raw_length is None or not raw_length.strip():
            return {}
        try:
            length = int(raw_length)
        except ValueError:
            raise ValueError("请求头 Content-Length 不正确") from None
        if length < 0:
            raise ValueError("请求头 Content-Length 不正确")
        if length == 0:
            return {}
        if length > 1_000_000:
            raise ValueError("请求体过大")
        try:
            raw = self.rfile.read(length)
        except OSError as exc:
            raise ValueError("读取请求体失败或超时") from exc
        if len(raw) < length:
            raise ValueError("请求体不完整")
        try:
            data = json.loads(raw.decode("utf-8"))
        except Exception as exc:
            raise ValueError("请求格式不正确") from exc
        if not isinstance(data, dict):
            raise ValueError("请求格式不正确")
        return data

    # ---------- 路由 ----------
    def do_GET(self):
        try:
            parsed = urlparse(self.path)
            path = parsed.path
            if path.startswith("/api/"):
                self.handle_api_get(path)
            else:
                self.serve_static(path)
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as exc:
            LOG.exception("处理 GET %s 时出错", self.path)
            self._error(f"服务器错误：{exc}", 500)

    def do_POST(self):
        try:
            parsed = urlparse(self.path)
            self.handle_api_post(parsed.path)
        except KeyError as exc:
            self._error(str(exc), 404)
        except ValueError as exc:
            self._error(str(exc), 400)
        except EngineError as exc:
            self._error(str(exc), 503)
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as exc:
            LOG.exception("处理 POST %s 时出错", self.path)
            self._error(f"服务器错误：{exc}", 500)

    def handle_api_get(self, path: str):
        if path == "/api/bootstrap":
            with APP.lock:
                games = APP.recent_games()
            self._json({
                "levels": public_levels(),
                "default_level": DEFAULT_LEVEL,
                "engine": APP.engine.status(),
                "defaults": {"size": 19, "rules": "chinese", "komi": 7.5, "handicap": 0, "player_color": "B"},
                "recent_games": games,
                "version": "1.0.0",
            })
            return
        if path == "/api/status":
            self._json({"engine": APP.engine.status(), "uptime": int(time.time() - APP.started)})
            return
        if path == "/api/games":
            self._json({"games": APP.recent_games()})
            return
        m = re.fullmatch(r"/api/records/([^/]+\.json)/sgf", path)
        if m:
            filename = unquote(m.group(1))
            if Path(filename).name != filename:
                self._error("棋谱不存在", 404)
                return
            target = GAMES_DIR / filename
            if not target.exists() or not target.is_file():
                self._error("棋谱不存在", 404)
                return
            try:
                data = json.loads(target.read_text(encoding="utf-8"))
            except Exception:
                self._error("棋谱读取失败", 500)
                return
            sgf = data.get("sgf") or ""
            self._send(
                sgf.encode("utf-8"),
                content_type="application/x-go-sgf; charset=utf-8",
                extra={"Content-Disposition": f'attachment; filename="{filename.replace(".json", ".sgf")}"'},
            )
            return
        m = re.fullmatch(r"/api/game/([a-f0-9]+)", path)
        if m:
            self._json(APP.get(m.group(1)).serialize())
            return
        m = re.fullmatch(r"/api/game/([a-f0-9]+)/sgf", path)
        if m:
            session = APP.get(m.group(1))
            # 导出 SGF 要和 AI 落子互斥，否则可能读到只更新了一半的棋局
            with session.lock:
                sgf = session.game.sgf()
            filename = f"yixin-{session.id}.sgf"
            self._send(
                sgf.encode("utf-8"),
                content_type="application/x-go-sgf; charset=utf-8",
                extra={"Content-Disposition": f'attachment; filename="{filename}"'},
            )
            return
        m = re.fullmatch(r"/api/game/([a-f0-9]+)/review", path)
        if m:
            self._json(APP.get(m.group(1)).review_payload())
            return
        self._error("接口不存在", 404)

    def handle_api_post(self, path: str):
        if path == "/api/new":
            session = APP.new_game(self._read_json())
            self._json(session.serialize())
            return
        if path == "/api/engine/restart":
            APP.engine.restart_async()
            self._json({"ok": True, "engine": APP.engine.status()})
            return
        if path == "/api/quit":
            self._json({"ok": True})
            def shutdown():
                time.sleep(0.35)
                APP.engine.stop()
                # os._exit 不会刷新缓冲区，重定向日志时先手动 flush
                try:
                    sys.stdout.flush()
                    sys.stderr.flush()
                except Exception:
                    pass
                os._exit(0)
            threading.Thread(target=shutdown, daemon=True).start()
            return
        m = re.fullmatch(r"/api/game/([a-f0-9]+)/([a-z/-]+)", path)
        if not m:
            self._error("接口不存在", 404)
            return
        session = APP.get(m.group(1))
        action = m.group(2)
        body = self._read_json()
        if action == "move":
            self._json(session.human_move(str(body.get("vertex", ""))))
        elif action == "pass":
            self._json(session.human_pass())
        elif action == "resign":
            self._json(session.human_resign())
        elif action == "undo":
            self._json(session.undo())
        elif action == "retry-ai":
            self._json(session.retry_ai())
        elif action == "hint":
            self._json(session.get_hint())
        elif action == "estimate":
            self._json(session.estimate())
        elif action == "review":
            self._json(session.start_review())
        elif action == "score/toggle":
            self._json(session.toggle_dead(str(body.get("vertex", ""))))
        elif action == "score/confirm":
            self._json(session.confirm_score())
        elif action == "score/resume":
            self._json(session.resume_scoring())
        elif action == "score/reestimate":
            self._json(session.reestimate_scoring())
        else:
            self._error("接口不存在", 404)

    def serve_static(self, path: str):
        if path in ("/", "/index.html"):
            target = WEB / "index.html"
        elif path == "/favicon.ico":
            target = WEB / "favicon.svg"
        elif path.startswith("/static/"):
            rel = unquote(path[len("/static/"):])
            target = (WEB / rel).resolve()
            if WEB.resolve() not in target.parents and target != WEB.resolve():
                self._error("禁止访问", 403)
                return
        else:
            self._error("页面不存在", 404)
            return
        if not target.exists() or not target.is_file():
            self._error("文件不存在", 404)
            return
        mime = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".svg": "image/svg+xml",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".webp": "image/webp",
            ".woff2": "font/woff2",
        }.get(target.suffix.lower(), "application/octet-stream")
        self._send(target.read_bytes(), content_type=mime, extra={"Cache-Control": "no-cache"})


class LocalHTTPServer(ThreadingHTTPServer):
    """只监听本机回环地址的 HTTP 服务。

    allow_reuse_address 默认是 True：在 Windows 上，第二个进程对已被占用的
    端口也能“绑定成功”，结果是端口回退逻辑形同虚设、两个实例抢同一端口。
    关掉它之后 bind 会真正报错，find_port 才能按 8765→8766–8784 依次回退。
    """

    daemon_threads = True
    allow_reuse_address = False


def find_port(preferred: int) -> tuple[LocalHTTPServer, int]:
    last_error: OSError | None = None
    for port in range(preferred, preferred + 20):
        try:
            return LocalHTTPServer(("127.0.0.1", port), Handler), port
        except OSError as exc:
            last_error = exc
            continue
    raise RuntimeError(f"端口 {preferred}–{preferred + 19} 都被占用，无法启动服务（{last_error}）")


def open_browser(url: str) -> None:
    """优先用 Edge 应用窗口打开；失败或非 Windows 时退回到系统默认浏览器。"""
    if os.name == "nt":
        candidates = [
            Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
            Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
        ]
        for edge in candidates:
            if not edge.exists():
                continue
            try:
                subprocess.Popen(
                    [str(edge), f"--app={url}", "--window-size=1440,980", "--disable-features=Translate"],
                    close_fds=True,
                )
                return
            except Exception as exc:
                LOG.warning("用 Edge 打开界面失败：%s", exc)
    try:
        webbrowser.open(url)
    except Exception as exc:
        LOG.warning("自动打开浏览器失败，请手动访问 %s：%s", url, exc)


_LOG_LEVELS = ("debug", "info", "warning", "error", "critical", "none")


def _log_level(value: str) -> int:
    level = value.lower()
    if level not in _LOG_LEVELS:
        raise argparse.ArgumentTypeError("日志级别只能是 debug/info/warning/error/critical/none")
    if level == "none":
        return logging.CRITICAL + 10
    return getattr(logging, level.upper())


def _port(value: str) -> int:
    try:
        port = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("端口必须是整数") from None
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("端口范围是 1–65535")
    return port


def setup_logging(level: int) -> None:
    """只配一次根日志；--log-level 控制访问日志与引擎日志的输出量。"""
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="弈心 · 围棋陪练：在本机运行 HTTP 服务，用浏览器与 KataGo 对弈。",
        epilog="示例：python server.py --port 9000 --no-browser --log-level info",
    )
    parser.add_argument(
        "--port", type=_port, default=8765,
        help="首选端口，默认 8765；被占用时自动尝试后面 19 个端口（最大到 8784）",
    )
    parser.add_argument(
        "--no-browser", "--no-open", dest="no_browser", action="store_true",
        help="启动后不自动打开浏览器",
    )
    parser.add_argument(
        "--engine-path", default=None,
        help="KataGo 可执行文件或它所在的目录；默认自动查找 engine/ 下的引擎",
    )
    parser.add_argument(
        "--models-dir", default=None,
        help="模型目录，默认程序目录下的 models/",
    )
    parser.add_argument(
        "--log-level", type=_log_level, default="warning",
        help="日志级别：debug/info/warning/error/critical/none（默认 warning）",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    setup_logging(args.log_level)
    if args.models_dir and not Path(args.models_dir).expanduser().is_dir():
        LOG.error("模型目录不存在：%s", args.models_dir)
    # 命令行可能覆盖引擎/模型路径，所以按参数重建引擎；模块级 APP 不自动启动引擎
    APP.engine = AnalysisEngine(
        human_model=True, engine_path=args.engine_path, models_dir=args.models_dir
    )
    ensure_data_dirs()
    try:
        server, port = find_port(args.port)
    except RuntimeError as exc:
        LOG.error("%s", exc)
        return 1
    url = f"http://127.0.0.1:{port}"
    # flush=True：日志/启动器把 stdout 重定向到文件时也能马上看到实际地址
    print("=" * 56, flush=True)
    print("  弈心 · 围棋陪练", flush=True)
    print(f"  本地地址：{url}", flush=True)
    if port != args.port:
        print(f"  端口 {args.port} 被占用，已自动改用 {port}", flush=True)
    print("  AI 引擎正在预热，首次启动约需 10–30 秒；页面可以先打开。", flush=True)
    print("  关闭此窗口或按 Ctrl+C 即可退出。", flush=True)
    print("=" * 56, flush=True)
    LOG.info("引擎：%s；模型目录：%s",
             APP.engine.engine_path or "自动查找", APP.engine.models_dir)
    APP.engine.start_async()
    if not args.no_browser:
        timer = threading.Timer(1.0, open_browser, args=(url,))
        timer.daemon = True
        timer.start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n正在退出……")
    finally:
        APP.engine.stop()
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
