"""最小但实用的 SGF 导入器。

只读取第一棵 game tree 的主线，跳过分支变化；支持常见的 SZ/KM/RU/HA/AB/AW/PL/RE
以及 B/W 落子。目标是把棋友从野狐、弈城、OGS 导出的棋谱带进来复盘，而不是
实现完整的 SGF 编辑器。遇到不合法或无法解释的棋谱会抛出 SgfError，并带中文原因。
"""
from __future__ import annotations

import re

from go_engine import (
    BLACK,
    WHITE,
    GoGame,
    IllegalMove,
    from_sgf_vertex,
)

MAX_MOVES = 1000
PROP_RE = re.compile(r"^[A-Za-z]+$")


class SgfError(ValueError):
    pass


def _tokenize(text: str) -> list:
    tokens: list = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c.isspace():
            i += 1
            continue
        if c in "();":
            tokens.append(c)
            i += 1
            continue
        if c.isalpha():
            j = i
            while j < n and text[j].isalpha():
                j += 1
            ident = text[i:j].upper()
            if not PROP_RE.match(ident):
                raise SgfError(f"无法识别的属性：{ident}")
            i = j
            values = []
            while True:
                while i < n and text[i].isspace():
                    i += 1
                if i >= n or text[i] != "[":
                    break
                i += 1
                buf = []
                closed = False
                while i < n:
                    ch = text[i]
                    if ch == "\\":
                        i += 1
                        if i < n:
                            buf.append(text[i])
                            i += 1
                        continue
                    if ch == "]":
                        i += 1
                        closed = True
                        break
                    buf.append(ch)
                    i += 1
                if not closed:
                    raise SgfError("属性值没有闭合")
                values.append("".join(buf))
            if not values:
                raise SgfError(f"属性 {ident} 没有值")
            tokens.append(("prop", ident, values))
            continue
        raise SgfError(f"无法识别的字符：{c!r}")
    return tokens


def _skip_subtree(tokens: list, idx: int) -> int:
    if idx >= len(tokens) or tokens[idx] != "(":
        raise SgfError("分支格式不正确")
    depth = 0
    while idx < len(tokens):
        token = tokens[idx]
        if token == "(":
            depth += 1
        elif token == ")":
            depth -= 1
            if depth == 0:
                return idx + 1
        idx += 1
    raise SgfError("SGF 分支没有闭合")


def _parse_first_tree(tokens: list) -> list[dict]:
    idx = 0
    while idx < len(tokens) and tokens[idx] != "(":
        idx += 1
    if idx >= len(tokens):
        raise SgfError("不是有效的 SGF：找不到左括号")
    idx += 1
    nodes: list[dict] = []
    while idx < len(tokens):
        token = tokens[idx]
        if token == "(":
            # 变化图：跳过，保留主线。
            idx = _skip_subtree(tokens, idx)
            continue
        if token == ")":
            if not nodes:
                raise SgfError("SGF 中没有节点")
            return nodes
        if token != ";":
            raise SgfError("SGF 节点格式不正确")
        idx += 1
        props: dict[str, list[str]] = {}
        while idx < len(tokens) and isinstance(tokens[idx], tuple):
            _, ident, values = tokens[idx]
            props.setdefault(ident, []).extend(values)
            idx += 1
        nodes.append(props)
    raise SgfError("SGF 没有闭合")


def _first(props: dict, key: str, default: str | None = None) -> str | None:
    values = props.get(key)
    if not values:
        return default
    return values[-1]


def _parse_size(root: dict) -> int:
    raw = _first(root, "SZ", "19") or "19"
    if ":" in raw:
        parts = raw.split(":", 1)
        if not all(p.isdigit() for p in parts) or parts[0] != parts[1]:
            raise SgfError("只支持正方形棋盘")
        raw = parts[0]
    try:
        size = int(raw)
    except ValueError:
        raise SgfError("棋盘大小不正确") from None
    if size not in (9, 13, 19):
        raise SgfError("只支持 9、13、19 路")
    return size


def _parse_komi(root: dict, rules: str) -> float:
    raw = _first(root, "KM")
    if raw is None:
        return 7.5 if rules == "chinese" else 6.5
    try:
        komi = float(raw)
    except ValueError:
        raise SgfError("贴目格式不正确") from None
    if not -10 <= komi <= 50:
        raise SgfError("贴目超出范围")
    return komi


def _parse_result(game: GoGame, raw: str | None) -> dict:
    if not raw:
        return {"winner": None, "reason": "import", "text": "导入棋谱（未记录结果）", "margin": None}
    text = raw.strip()
    if text in ("0", "Draw", "DRAW", "draw", "Jigo"):
        return {"winner": "D", "reason": "score", "text": "和棋（导入棋谱）", "margin": 0.0}
    m = re.match(r"^([BW])\+(.+)$", text, re.IGNORECASE)
    if not m:
        return {"winner": None, "reason": "import", "text": f"导入棋谱（结果：{text}）", "margin": None}
    winner = m.group(1).upper()
    suffix = m.group(2).strip()
    margin = None
    reason = "score"
    if suffix.upper().startswith(("R", "RESIGN")):
        reason = "resign"
        label = "中盘胜"
    elif suffix.upper().startswith(("T", "TIME")):
        reason = "time"
        label = "超时胜"
    elif suffix.upper().startswith(("F", "FORFEIT")):
        reason = "forfeit"
        label = "判负"
    else:
        label = None
        try:
            margin = float(suffix)
        except ValueError:
            margin = None
    color = "黑" if winner == "B" else "白"
    if label:
        result_text = f"{color}棋{label}（导入棋谱）"
    elif margin is not None:
        result_text = f"{color}胜 {margin:g} 目（导入棋谱）"
    else:
        result_text = f"{color}胜（导入棋谱）"
    return {"winner": winner, "reason": reason, "text": result_text, "margin": margin}


def _setup_game(size: int, komi: float, rules: str, root: dict) -> tuple[GoGame, int]:
    ha_raw = _first(root, "HA")
    handicap = 0
    if ha_raw is not None:
        try:
            handicap = int(ha_raw)
        except ValueError:
            raise SgfError("让子数格式不正确") from None
        if not 0 <= handicap <= 9:
            raise SgfError("让子数应在 0–9")
    ab = [_vertex(v, size, "AB") for v in root.get("AB", [])]
    aw = [_vertex(v, size, "AW") for v in root.get("AW", [])]
    player = (_first(root, "PL") or "").upper()
    if player and player not in ("B", "W"):
        player = ""
    if ab or aw:
        game = GoGame(size=size, komi=komi, rules=rules, handicap=0, player_color=player or "B")
        for vertex in ab:
            x, y = _xy(vertex, size)
            if game.board[y][x] != 0:
                raise SgfError("初始棋子有重复位置")
            game.board[y][x] = BLACK
            game.initial_stones.append((BLACK, vertex))
        for vertex in aw:
            x, y = _xy(vertex, size)
            if game.board[y][x] != 0:
                raise SgfError("初始棋子有重复位置")
            game.board[y][x] = WHITE
            game.initial_stones.append((WHITE, vertex))
        if 2 <= len(ab) <= 9:
            game.handicap = len(ab)
        if player in ("B", "W"):
            game.to_move = BLACK if player == "B" else WHITE
        else:
            game.to_move = WHITE if ab else BLACK
    else:
        game = GoGame(size=size, komi=komi, rules=rules, handicap=handicap, player_color=player or "B")
        if player == "W":
            game.to_move = WHITE
        elif player == "B":
            game.to_move = BLACK
    return game, len(ab) if ab else handicap


def _xy(vertex: str, size: int):
    from go_engine import vertex_to_xy

    return vertex_to_xy(vertex, size)


def _vertex(raw: str, size: int, label: str) -> str:
    if not isinstance(raw, str) or len(raw.strip()) < 2:
        raise SgfError(f"{label} 坐标不正确")
    try:
        vertex = from_sgf_vertex(raw, size)
    except IllegalMove as exc:
        raise SgfError(f"{label} 坐标不正确：{exc}") from None
    if vertex == "pass":
        raise SgfError(f"{label} 不能是停一手")
    return vertex


def parse_sgf(text: str) -> dict:
    if not isinstance(text, str) or not text.strip():
        raise SgfError("SGF 内容为空")
    if len(text) > 2_000_000:
        raise SgfError("SGF 文件过大")
    text = text.lstrip("\ufeff")
    nodes = _parse_first_tree(_tokenize(text))
    root = nodes[0]
    size = _parse_size(root)
    ru = (_first(root, "RU") or "chinese").lower()
    rules = "japanese" if ("japanese" in ru or "korean" in ru) else "chinese"
    komi = _parse_komi(root, rules)
    game, handicap = _setup_game(size, komi, rules, root)
    moves = 0
    for node in nodes[1:]:
        has_b, has_w = "B" in node, "W" in node
        if not has_b and not has_w:
            continue
        if has_b and has_w:
            raise SgfError("同一节点不能同时有黑白落子")
        color = "B" if has_b else "W"
        expected = "B" if game.to_move == BLACK else "W"
        if color != expected:
            raise SgfError(f"第 {moves + 1} 手颜色与轮次不符")
        raw = _first(node, color, "")
        moves += 1
        if moves > MAX_MOVES:
            raise SgfError("棋谱手数过多")
        try:
            vertex = from_sgf_vertex(raw or "", size)
            if vertex == "pass":
                game.pass_turn()
            else:
                game.play(vertex)
        except IllegalMove as exc:
            raise SgfError(f"第 {moves} 手无法落子：{exc}") from None
    raw_result = _first(root, "RE")
    result = _parse_result(game, raw_result)
    game.phase = "ended"
    game.result = result
    return {
        "game": game,
        "size": size,
        "rules": rules,
        "komi": komi,
        "handicap": handicap,
        "moves": moves,
        "comment": _first(root, "C") or "",
    }
