"""围棋规则引擎：提子、劫争、让子、数子、SGF 坐标。

这个模块不依赖第三方库，供 HTTP 服务与单元测试共用。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

COLS = "ABCDEFGHJKLMNOPQRST"
EMPTY, BLACK, WHITE = 0, 1, 2
COLOR_NAME = {BLACK: "黑", WHITE: "白"}
COLOR_CODE = {BLACK: "B", WHITE: "W"}


class IllegalMove(ValueError):
    """落子不合法。"""


def columns(size: int) -> str:
    return COLS[:size]


def xy_to_vertex(x: int, y: int, size: int) -> str:
    return f"{columns(size)[x]}{size - y}"


def vertex_to_xy(vertex: str, size: int) -> tuple[int, int]:
    v = (vertex or "").strip().upper()
    if v == "PASS":
        raise IllegalMove("pass 不是棋盘坐标")
    if len(v) < 2:
        raise IllegalMove("坐标格式不正确")
    col = v[0]
    if col not in columns(size):
        raise IllegalMove(f"没有 {col} 列")
    try:
        num = int(v[1:])
    except ValueError as exc:
        raise IllegalMove("坐标格式不正确") from exc
    if not 1 <= num <= size:
        raise IllegalMove(f"行号应在 1–{size} 之间")
    return columns(size).index(col), size - num


def to_sgf_vertex(vertex: str, size: int) -> str:
    """GTP 坐标（如 Q16）转 SGF 坐标（如 pd）。"""
    if vertex is None:
        raise IllegalMove("坐标不能为空")
    if str(vertex).strip().lower() == "pass":
        return ""
    x, y = vertex_to_xy(str(vertex), size)
    return chr(ord("a") + x) + chr(ord("a") + y)


def from_sgf_vertex(sgf: str, size: int) -> str:
    """SGF 坐标（如 pd）转 GTP 坐标（如 Q16）。

    FF[4] 里空串和 19 路及以下的 "tt" 都表示停一手；坐标越界或格式不对时
    抛 IllegalMove，而不是把 IndexError 泄露给调用方。
    """
    if sgf is None:
        return "pass"
    v = str(sgf).strip().lower()
    if not v or v == "tt":
        return "pass"
    if len(v) != 2 or not ("a" <= v[0] <= "z") or not ("a" <= v[1] <= "z"):
        raise IllegalMove("SGF 坐标格式不正确")
    x = ord(v[0]) - ord("a")
    y = ord(v[1]) - ord("a")
    if not 0 <= x < size or not 0 <= y < size:
        raise IllegalMove("SGF 坐标超出棋盘范围")
    return xy_to_vertex(x, y, size)


def sgf_escape(text: str) -> str:
    return str(text).replace("\\", "\\\\").replace("]", "\\]")


@dataclass
class MoveRecord:
    color: int
    vertex: str
    captures: list[str]
    move_no: int

    @property
    def is_pass(self) -> bool:
        return self.vertex.lower() == "pass"

    def as_dict(self) -> dict:
        return {
            "color": COLOR_CODE[self.color],
            "vertex": self.vertex,
            "captures": self.captures,
            "move_no": self.move_no,
        }


class GoGame:
    def __init__(
        self,
        size: int = 19,
        komi: float = 7.5,
        rules: str = "chinese",
        handicap: int = 0,
        player_color: str = "B",
        level_id: str = "pro2",
    ) -> None:
        if size not in (9, 13, 19):
            raise ValueError("棋盘只支持 9、13、19 路")
        if rules not in ("chinese", "japanese"):
            raise ValueError("规则只支持中国规则或日本规则")
        if not 0 <= handicap <= 9:
            raise ValueError("让子数应为 0–9")
        self.size = size
        self.komi = float(komi)
        self.rules = rules
        self.handicap = handicap
        self.player_color = "B" if player_color == "B" else "W"
        self.level_id = level_id
        self.board = [[EMPTY for _ in range(size)] for _ in range(size)]
        self.initial_stones: list[tuple[int, str]] = []
        self.history: list[MoveRecord] = []
        self.captures = {BLACK: 0, WHITE: 0}
        self.to_move = BLACK
        self.passes = 0
        self.last_move: str | None = None
        self.last_move_color: int | None = None
        self.ko_hash: tuple | None = None
        self.phase = "playing"  # playing / scoring / ended
        self.result: dict | None = None
        self.dead_stones: set[str] = set()
        self.scoring: dict | None = None
        self._snapshots: list[dict] = []
        self.start_ns = None

        if handicap:
            stones = self._handicap_points(handicap)
            for x, y in stones:
                self.board[y][x] = BLACK
                self.initial_stones.append((BLACK, xy_to_vertex(x, y, size)))
            self.to_move = WHITE

    # ---------- 基础查询 ----------
    def in_bounds(self, x: int, y: int) -> bool:
        return 0 <= x < self.size and 0 <= y < self.size

    def neighbors(self, x: int, y: int) -> Iterable[tuple[int, int]]:
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if self.in_bounds(nx, ny):
                yield nx, ny

    def group(self, x: int, y: int, board: list[list[int]] | None = None) -> tuple[set[tuple[int, int]], int]:
        board = board if board is not None else self.board
        color = board[y][x]
        if color == EMPTY:
            return set(), 0
        seen = {(x, y)}
        stack = [(x, y)]
        liberties = 0
        border = set()
        while stack:
            cx, cy = stack.pop()
            for nx, ny in self.neighbors(cx, cy):
                v = board[ny][nx]
                if v == EMPTY:
                    border.add((nx, ny))
                elif v == color and (nx, ny) not in seen:
                    seen.add((nx, ny))
                    stack.append((nx, ny))
        return seen, len(border)

    def board_hash(self, board: list[list[int]] | None = None) -> tuple:
        b = board if board is not None else self.board
        return tuple(v for row in b for v in row)

    def stone_at(self, vertex: str) -> int:
        x, y = vertex_to_xy(vertex, self.size)
        return self.board[y][x]

    def _handicap_points(self, n: int) -> list[tuple[int, int]]:
        s = self.size
        h = 3 if s >= 13 else 2
        mid = s // 2
        lo, hi = h, s - 1 - h
        # 两个对角先放，再补左上、右下、天元与边星。
        pts = [(lo, hi), (hi, lo), (lo, lo), (hi, hi), (mid, mid)]
        pts += [(lo, mid), (hi, mid), (mid, hi), (mid, lo)]
        # 去重并保持顺序
        out = []
        for p in pts:
            if p not in out:
                out.append(p)
        return out[:n]

    # ---------- 落子 ----------
    def _simulate(self, x: int, y: int, color: int, check_ko: bool = True) -> tuple[list[list[int]], list[str]] | None:
        if not self.in_bounds(x, y) or self.board[y][x] != EMPTY:
            return None
        board = [row[:] for row in self.board]
        board[y][x] = color
        opp = BLACK if color == WHITE else WHITE
        captured: list[tuple[int, int]] = []
        for nx, ny in self.neighbors(x, y):
            if board[ny][nx] != opp:
                continue
            grp, libs = self.group(nx, ny, board)
            if libs == 0:
                for gx, gy in grp:
                    board[gy][gx] = EMPTY
                    captured.append((gx, gy))
        own, libs = self.group(x, y, board)
        if libs == 0:
            return None
        # 简单劫：禁止立即还原上一手之前的全盘局面
        if check_ko and self.ko_hash is not None and self.board_hash(board) == self.ko_hash:
            return None
        return board, [xy_to_vertex(cx, cy, self.size) for cx, cy in captured]

    def is_legal(self, vertex: str, color: int | None = None) -> tuple[bool, str]:
        color = color or self.to_move
        try:
            x, y = vertex_to_xy(vertex, self.size)
        except IllegalMove as exc:
            return False, str(exc)
        if self.phase != "playing":
            return False, "当前不在对局阶段"
        if self.board[y][x] != EMPTY:
            return False, "这里已经有棋子了"
        if self._simulate(x, y, color, check_ko=False) is None:
            return False, "自杀手不合法"
        if self._simulate(x, y, color) is None:
            return False, "劫争：不能立即提回"
        return True, ""

    def play(self, vertex: str, color: int | None = None) -> MoveRecord:
        if color is not None and color != self.to_move:
            raise IllegalMove("还没有轮到这一方落子")
        color = self.to_move
        if self.phase != "playing":
            raise IllegalMove("对局不在进行中")
        x, y = vertex_to_xy(vertex, self.size)
        if self.board[y][x] != EMPTY:
            raise IllegalMove("这里已经有棋子了")
        if self._simulate(x, y, color, check_ko=False) is None:
            raise IllegalMove("自杀手不合法")
        sim = self._simulate(x, y, color)
        if sim is None:
            raise IllegalMove("劫争：不能立即提回")
        new_board, captured = sim
        self._snapshots.append(self._snapshot())
        prev_hash = self.board_hash()
        self.board = new_board
        for v in captured:
            cx, cy = vertex_to_xy(v, self.size)
            self.board[cy][cx] = EMPTY
            self.captures[color] += 1
        record = MoveRecord(color, xy_to_vertex(x, y, self.size), captured, len(self.history) + 1)
        self.history.append(record)
        self.last_move = record.vertex
        self.last_move_color = color
        self.to_move = BLACK if color == WHITE else WHITE
        self.passes = 0
        self.ko_hash = prev_hash
        return record

    def pass_turn(self, color: int | None = None) -> MoveRecord:
        if color is not None and color != self.to_move:
            raise IllegalMove("还没有轮到这一方落子")
        if self.phase != "playing":
            raise IllegalMove("对局不在进行中")
        color = self.to_move
        self._snapshots.append(self._snapshot())
        record = MoveRecord(color, "pass", [], len(self.history) + 1)
        self.history.append(record)
        self.passes += 1
        self.to_move = BLACK if color == WHITE else WHITE
        self.last_move = "pass"
        self.last_move_color = color
        self.ko_hash = None
        if self.passes >= 2:
            self.phase = "scoring"
        return record

    def undo(self, plies: int = 1) -> int:
        """撤销若干手，返回实际撤销数量。"""
        n = 0
        while self._snapshots and n < plies:
            snap = self._snapshots.pop()
            self._restore(snap)
            n += 1
        self.phase = "playing" if self.phase != "ended" else self.phase
        self.result = None if self.phase == "playing" else self.result
        self.dead_stones.clear()
        self.scoring = None
        return n

    def resign(self) -> dict:
        winner = WHITE if self.player_color == BLACK else BLACK
        self.phase = "ended"
        self.result = {
            "winner": COLOR_CODE[winner],
            "reason": "resign",
            "text": f"{COLOR_NAME[winner]}棋中盘胜（对方认输）",
            "margin": None,
        }
        return self.result

    # ---------- 快照 ----------
    def _snapshot(self) -> dict:
        return {
            "board": [row[:] for row in self.board],
            "to_move": self.to_move,
            "captures": dict(self.captures),
            "passes": self.passes,
            "last_move": self.last_move,
            "last_move_color": self.last_move_color,
            "ko_hash": self.ko_hash,
            "phase": self.phase,
            "history_len": len(self.history),
        }

    def _restore(self, snap: dict) -> None:
        self.board = [row[:] for row in snap["board"]]
        self.to_move = snap["to_move"]
        self.captures = dict(snap["captures"])
        self.passes = snap["passes"]
        self.last_move = snap["last_move"]
        self.last_move_color = snap["last_move_color"]
        self.ko_hash = snap["ko_hash"]
        self.phase = snap["phase"]
        del self.history[snap["history_len"] :]

    # ---------- 数子 ----------
    def _territory(self, board: list[list[int]]) -> tuple[list[list[int]], int, int]:
        """返回每点归属（0 无主，1 黑，2 白）与黑白地数量。"""
        own = [[0 for _ in range(self.size)] for _ in range(self.size)]
        black_t, white_t = 0, 0
        visited = set()
        for y in range(self.size):
            for x in range(self.size):
                if board[y][x] != EMPTY or (x, y) in visited:
                    continue
                region = {(x, y)}
                stack = [(x, y)]
                visited.add((x, y))
                border = set()
                while stack:
                    cx, cy = stack.pop()
                    for nx, ny in self.neighbors(cx, cy):
                        v = board[ny][nx]
                        if v == EMPTY and (nx, ny) not in visited:
                            visited.add((nx, ny))
                            region.add((nx, ny))
                            stack.append((nx, ny))
                        elif v != EMPTY:
                            border.add(v)
                if len(border) == 1:
                    color = next(iter(border))
                    for rx, ry in region:
                        own[ry][rx] = color
                    if color == BLACK:
                        black_t += len(region)
                    else:
                        white_t += len(region)
        return own, black_t, white_t

    def score(self, dead: Iterable[str] | None = None) -> dict:
        dead_set = set(dead if dead is not None else self.dead_stones)
        board = [row[:] for row in self.board]
        dead_black = dead_white = 0
        for v in dead_set:
            try:
                x, y = vertex_to_xy(v, self.size)
            except IllegalMove:
                continue
            color = board[y][x]
            if color:
                board[y][x] = EMPTY
                if color == BLACK:
                    dead_black += 1
                else:
                    dead_white += 1
        territory_map, black_t, white_t = self._territory(board)
        black_stones = sum(row.count(BLACK) for row in board)
        white_stones = sum(row.count(WHITE) for row in board)
        if self.rules == "japanese":
            black_score = black_t + self.captures[BLACK] + dead_white
            white_score = white_t + self.captures[WHITE] + dead_black + self.komi
            breakdown = {
                "black_territory": black_t,
                "white_territory": white_t,
                "black_captures": self.captures[BLACK] + dead_white,
                "white_captures": self.captures[WHITE] + dead_black,
            }
        else:
            black_score = black_stones + black_t
            white_score = white_stones + white_t + self.komi
            breakdown = {
                "black_stones": black_stones,
                "white_stones": white_stones,
                "black_territory": black_t,
                "white_territory": white_t,
            }
        diff = round(black_score - white_score, 2)
        if abs(diff) < 1e-9:
            winner = "D"
            text = "和棋"
        elif diff > 0:
            winner = "B"
            text = f"黑胜 {abs(diff):g} 目"
        else:
            winner = "W"
            text = f"白胜 {abs(diff):g} 目"
        return {
            "winner": winner,
            "text": text,
            "margin": abs(diff),
            "black_score": round(black_score, 2),
            "white_score": round(white_score, 2),
            "komi": self.komi,
            "breakdown": breakdown,
            "dead": sorted(dead_set),
            "dead_black": dead_black,
            "dead_white": dead_white,
            "territory": territory_map,
            "rules": self.rules,
        }

    def auto_dead_from_ownership(self, ownership: list[float], threshold: float = 0.56) -> list[str]:
        """依据 KataGo 归属预测标记死子（按整块棋判断）。"""
        dead: set[tuple[int, int]] = set()
        if not ownership or len(ownership) != self.size * self.size:
            return []
        seen = set()
        for y in range(self.size):
            for x in range(self.size):
                color = self.board[y][x]
                if color == EMPTY or (x, y) in seen:
                    continue
                grp, _ = self.group(x, y)
                seen |= grp
                vals = [ownership[gy * self.size + gx] for gx, gy in grp]
                avg = sum(vals) / max(1, len(vals))
                if (color == BLACK and avg < -threshold) or (color == WHITE and avg > threshold):
                    dead |= grp
        return sorted(xy_to_vertex(x, y, self.size) for x, y in dead)

    def group_vertices(self, vertex: str) -> list[str]:
        x, y = vertex_to_xy(vertex, self.size)
        if self.board[y][x] == EMPTY:
            return []
        grp, _ = self.group(x, y)
        return [xy_to_vertex(gx, gy, self.size) for gx, gy in grp]

    def toggle_dead(self, vertex: str) -> list[str]:
        group = set(self.group_vertices(vertex))
        if not group:
            return sorted(self.dead_stones)
        if group <= self.dead_stones:
            self.dead_stones -= group
        else:
            self.dead_stones |= group
        return sorted(self.dead_stones)

    def compute_score(self) -> dict:
        score = self.score()
        self.scoring = score
        return score

    def result_from_score(self, reason: str = "score") -> dict:
        score = self.score()
        self.phase = "ended"
        self.result = {
            "winner": score["winner"],
            "reason": reason,
            "text": score["text"],
            "margin": score["margin"],
            "score": score,
        }
        return self.result

    # ---------- 导出 ----------
    def board_flat(self) -> str:
        return "".join(str(v) for row in self.board for v in row)

    def sgf(self) -> str:
        rules = "Chinese" if self.rules == "chinese" else "Japanese"
        root = [
            "GM[1]",
            "FF[4]",
            "CA[UTF-8]",
            "AP[YixinGoCoach:1.0]",
            f"SZ[{self.size}]",
            f"KM[{self.komi:g}]",
            f"RU[{rules}]",
            f"PB[你]",
            f"PW[弈心 · 职业陪练]",
        ]
        if self.handicap:
            root.append(f"HA[{self.handicap}]")
            ab = "".join(f"[{to_sgf_vertex(v, self.size)}]" for _, v in self.initial_stones)
            root.append(f"AB{ab}")
        if self.result:
            color = "B" if self.result.get("winner") == "B" else "W"
            if self.result.get("winner") == "D":
                root.append("RE[0]")
            elif self.result.get("reason") == "resign":
                root.append(f"RE[{color}+R]")
            else:
                margin = self.result.get("margin") or 0
                root.append(f"RE[{color}+{margin:g}]")
        body = []
        for rec in self.history:
            if rec.is_pass:
                body.append(f";{COLOR_CODE[rec.color]}[]")
            else:
                body.append(f";{COLOR_CODE[rec.color]}[{to_sgf_vertex(rec.vertex, self.size)}]")
        return "(;" + "".join(root) + "".join(body) + ")\n"

    def moves_list(self) -> list[dict]:
        return [
            {
                "color": COLOR_CODE[r.color],
                "vertex": r.vertex,
                "move_no": r.move_no,
                "captures": r.captures,
            }
            for r in self.history
        ]
