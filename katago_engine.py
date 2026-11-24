"""KataGo 并行分析引擎的 Python 封装。

使用 stdio JSON 协议，避免 GTP 流式解析的复杂度；一个引擎进程同时支持
对局、提示、形势判断和复盘。
"""
from __future__ import annotations

import json
import logging
import math
import os
import random
import re
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Callable

from go_engine import BLACK, WHITE, GoGame

LOG = logging.getLogger("yixin.engine")

_PYTHON_EXE_RE = re.compile(r"^python(?:\d+(?:\.\d+)*)?w?(?:\.exe)?$", re.IGNORECASE)


def _is_python_executable(path: Path) -> bool:
    """判断 sys.executable 是解释器本身还是打包后的 Exe。

    Windows 解释器叫 python.exe / pythonw.exe，Linux 与 macOS 上则叫
    python3.13 这类名字；只认前两种会把源码运行时的 BASE 错算到解释器目录，
    导致找不到 web/、models/、engine/。
    """
    return bool(_PYTHON_EXE_RE.match(path.name))


_EXE_PATH = Path(sys.executable).resolve()
BASE = (
    _EXE_PATH.parent
    if (getattr(sys, "frozen", False) or not _is_python_executable(_EXE_PATH))
    else Path(__file__).resolve().parent
)


class EngineError(RuntimeError):
    pass


class Pending:
    def __init__(self, expected: int, callback: Callable[[dict], None] | None = None) -> None:
        self.expected = max(1, expected)
        self.results: list[dict] = []
        self.error: str | None = None
        self.event = threading.Event()
        self.callback = callback

    def push(self, data: dict) -> None:
        if data.get("error"):
            self.error = str(data["error"])
            self.event.set()
            return
        if data.get("warning") and not data.get("moveInfos") and not data.get("rootInfo"):
            # 警告本身不是分析结果，不能触发一次“空结果”完成
            return
        if data.get("isDuringSearch"):
            return
        self.results.append(data)
        if self.callback:
            try:
                self.callback(data)
            except Exception:
                pass
        if len(self.results) >= self.expected:
            self.event.set()


class AnalysisEngine:
    # 引擎连续异常退出后最多自动重启的次数，避免配置错误时无限重启
    MAX_AUTO_RESTARTS = 3

    def __init__(
        self,
        human_model: bool = True,
        engine_path: str | os.PathLike | None = None,
        models_dir: str | os.PathLike | None = None,
    ) -> None:
        self.proc: subprocess.Popen | None = None
        self.ready = threading.Event()
        self.lock = threading.RLock()
        self.write_lock = threading.Lock()
        self.pending: dict[str, Pending] = {}
        self.logs = deque(maxlen=80)
        self.backend = "未启动"
        self.device = ""
        self.version = ""
        self.error: str | None = None
        self.started_at = 0.0
        self.ready_at = 0.0
        self.models_dir = Path(models_dir).expanduser().resolve() if models_dir else BASE / "models"
        self.engine_path = Path(engine_path).expanduser().resolve() if engine_path else None
        self.exe: Path | None = None
        self.config: Path | None = None
        self._selected_backend = "未启动"
        self.model = self.models_dir / "b10c512h8nbt3tflrs-fson-silu-rsnh.bin.gz"
        self.human_model = self.models_dir / "b18c384nbt-humanv0.bin.gz"
        self.human_enabled = bool(human_model and self.human_model.exists())
        self._seq = 0
        self._stopping = False
        self._starting = False
        self._start_lock = threading.Lock()
        self._auto_restarts = 0
        self._restart_inflight = False

    def _select_binary(self) -> tuple[Path, Path]:
        """定位 KataGo 可执行文件与对应的分析配置。

        构造函数不再因为找不到引擎而抛异常：那样会让整个 HTTP 服务在启动时
        直接崩掉，界面上连“引擎异常”都看不到。这里只在真正启动引擎时失败，
        并把原因写进 status() 的 error 字段。
        """
        cfg_dir = BASE / "configs"
        env_cfg = os.environ.get("KATAGO_CONFIG")
        candidates: list[tuple[Path, Path, str]] = []
        if self.engine_path is not None:
            exe = self.engine_path
            if exe.is_dir():
                exe = exe / ("katago.exe" if os.name == "nt" else "katago")
            hint = str(exe).lower()
            if "directml" in hint:
                cfg_name = "analysis_directml.cfg"
            elif "cuda" in hint:
                cfg_name = "analysis_cuda.cfg"
            else:
                cfg_name = "analysis_opencl.cfg"
            candidates.append((exe, cfg_dir / cfg_name, "自定义"))
        else:
            env = os.environ.get("KATAGO_EXE")
            if env:
                candidates.append((Path(env), cfg_dir / "analysis_opencl.cfg", "自定义"))
            candidates += [
                (BASE / "engine" / "katago-opencl" / "katago.exe", cfg_dir / "analysis_opencl.cfg", "OpenCL"),
                (BASE / "engine" / "katago-directml" / "katago.exe", cfg_dir / "analysis_directml.cfg", "DirectML"),
                (BASE / "engine" / "katago" / "katago.exe", cfg_dir / "analysis_cuda.cfg", "CUDA"),
            ]
        for exe, cfg, backend in candidates:
            if not exe.exists():
                continue
            if env_cfg and Path(env_cfg).exists():
                cfg = Path(env_cfg)
            elif not cfg.exists():
                # 自定义引擎没带同名配置时，退而使用 configs/ 里现有的后备配置
                for name in ("analysis_opencl.cfg", "analysis_directml.cfg", "analysis_cuda.cfg"):
                    if (cfg_dir / name).exists():
                        cfg = cfg_dir / name
                        break
            if not cfg.exists():
                raise EngineError(f"缺少 KataGo 分析配置文件：{cfg}")
            self._selected_backend = backend
            self.exe, self.config = exe, cfg
            return exe, cfg
        if self.engine_path is not None:
            raise EngineError(f"指定的引擎路径不存在：{self.engine_path}")
        searched = "、".join(str(item[0]) for item in candidates) or "（无）"
        raise EngineError(f"未找到 KataGo 引擎，请确认 engine 目录完整或使用 --engine-path 指定。已查找：{searched}")

    # ---------- 生命周期 ----------
    def start_async(self) -> None:
        with self._start_lock:
            if self._starting:
                return
            if self.proc is not None and self.proc.poll() is None:
                return
            self._starting = True
            self._stopping = False
        threading.Thread(target=self._start_thread, name="katago-start", daemon=True).start()

    def _start_thread(self) -> None:
        try:
            self._start_blocking()
        finally:
            with self._start_lock:
                self._starting = False

    def _start_blocking(self) -> None:
        if self.ready.is_set():
            return
        self.ready.clear()
        self.logs.clear()
        self.error = None
        try:
            self._select_binary()
            if not self.model.exists():
                raise EngineError(f"神经网络模型缺失：{self.model}")
        except EngineError as exc:
            self.error = str(exc)
            LOG.error("%s", self.error)
            return
        self.human_enabled = bool(self.human_model.exists())
        cmd = [
            str(self.exe),
            "analysis",
            "-config",
            str(self.config),
            "-model",
            str(self.model),
        ]
        if self.human_enabled:
            cmd += ["-human-model", str(self.human_model)]
        env = os.environ.copy()
        env["PATH"] = str(self.exe.parent) + os.pathsep + env.get("PATH", "")
        creationflags = 0
        if os.name == "nt":
            creationflags = 0x08000000  # CREATE_NO_WINDOW
        try:
            self.proc = subprocess.Popen(
                cmd,
                cwd=str(BASE),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=env,
                creationflags=creationflags,
            )
        except Exception as exc:
            self.proc = None
            self.error = f"引擎启动失败：{exc}"
            LOG.error("%s", self.error)
            return
        self.started_at = time.time()
        self.ready_at = 0.0
        proc = self.proc
        threading.Thread(target=self._read_stderr, args=(proc,), name="katago-err", daemon=True).start()
        threading.Thread(target=self._read_stdout, args=(proc,), name="katago-out", daemon=True).start()
        # 首次加载 + 可能的一次性 GPU 调优，给足时间；进程中途退出时立刻失败，
        # 不要傻等 7 分钟才告诉用户“引擎异常”（例如 --engine-path 指错了 exe）。
        deadline = time.monotonic() + 420
        while not self.ready.is_set():
            if self._stopping:
                return
            if proc.poll() is not None:
                # 给 stderr 读线程一点时间把具体原因写进 error
                time.sleep(0.2)
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self.error = "引擎初始化超时。首次运行可能需要进行数分钟 GPU 调优，请查看 KataGo 日志。"
                LOG.error("%s", self.error)
                self.stop()
                # 启动失败不是“关闭中”，让后续请求直接看到超时原因
                self._stopping = False
                return
            self.ready.wait(min(0.5, remaining))
        if not self.ready.is_set():
            if not self.error:
                self.error = self._describe_exit(proc)
                LOG.error("%s", self.error)
            return
        self._auto_restarts = 0
        self.version = self._detect_version()
        LOG.info("引擎已就绪：backend=%s version=%s", self._selected_backend, self.version or "未知")
        try:
            self.request({"id": "version-probe", "action": "query_version"}, timeout=15)
        except Exception:
            pass

    def _detect_version(self) -> str:
        for line in list(self.logs):
            m = re.search(r"KataGo v([\d.]+)", line)
            if m:
                return m.group(1)
        return ""

    def _describe_exit(self, proc: subprocess.Popen | None) -> str:
        """生成比“最后一行日志”更有用的退出原因。"""
        code = proc.poll() if proc is not None else None
        meaningful = None
        for line in reversed(self.logs):
            low = line.lower()
            if "started, ready" in low or "using opencl" in low or "using cuda" in low:
                continue
            if "error" in low or "exception" in low or "failed" in low or "uncaught" in low:
                meaningful = line
                break
        if meaningful is None:
            for line in reversed(self.logs):
                low = line.lower()
                if "started, ready" in low or low.startswith("katago v") or "warning:" in low:
                    continue
                meaningful = line
                break
        reason = f"（退出码 {code}）" if code is not None else ""
        detail = f"：{meaningful}" if meaningful else ""
        return f"引擎进程已退出{reason}{detail}"

    def _fail_pending(self, message: str) -> None:
        """让所有等结果的线程立刻醒来，而不是干等到请求超时。"""
        with self.lock:
            for pending in self.pending.values():
                if not pending.event.is_set():
                    pending.error = message
                    pending.event.set()

    def _schedule_auto_restart(self) -> None:
        """引擎意外退出后做有限的自动重启，让下一手还能下。"""
        with self.lock:
            if self._restart_inflight or self._stopping:
                return
            if self._auto_restarts >= self.MAX_AUTO_RESTARTS:
                self.error = (self.error or "引擎进程已退出") + (
                    f"；已连续异常退出 {self.MAX_AUTO_RESTARTS} 次，停止自动重启，请检查日志后手动重启引擎。"
                )
                return
            self._auto_restarts += 1
            self._restart_inflight = True
            attempt = self._auto_restarts
        LOG.warning("引擎意外退出，%d 秒后自动重启（第 %d/%d 次）", 3, attempt, self.MAX_AUTO_RESTARTS)

        def worker() -> None:
            time.sleep(3.0)
            with self.lock:
                self._restart_inflight = False
            if not self._stopping:
                self.start_async()

        threading.Thread(target=worker, name="katago-autorestart", daemon=True).start()

    def stop(self) -> None:
        self._stopping = True
        self.ready.clear()
        self._fail_pending("AI 引擎已停止")
        proc, self.proc = self.proc, None
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        LOG.info("引擎已停止")

    def restart_async(self) -> None:
        self.stop()
        self._stopping = False
        self._auto_restarts = 0
        self.start_async()

    def _wait_ready(self, timeout: float = 420) -> None:
        """等到引擎就绪；进程已死或压根没启动时快速失败，不再空等 7 分钟。"""
        deadline = time.monotonic() + timeout
        while not self.ready.is_set():
            if self._stopping:
                raise EngineError("AI 引擎正在关闭")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise EngineError(self.error or "AI 引擎初始化超时")
            self.ready.wait(min(0.25, remaining))
            if self.ready.is_set():
                return
            proc = self.proc
            if proc is not None:
                if proc.poll() is not None:
                    raise EngineError(self.error or "AI 引擎进程已退出")
            elif not self._starting and not self._restart_inflight:
                raise EngineError(self.error or "AI 引擎没有运行，请检查引擎与模型文件")

    def status(self) -> dict:
        return {
            "ready": self.ready.is_set(),
            "starting": self._starting,
            "backend": self.backend if self.backend != "未启动" else self._selected_backend,
            "device": self.device,
            "version": self.version,
            "error": self.error,
            "human_model": self.human_enabled,
            "model": self.model.name,
            "engine_path": str(self.exe) if self.exe else None,
            "startup_seconds": round(self.ready_at - self.started_at, 1) if self.ready_at else None,
            "pid": self.proc.pid if self.proc and self.proc.poll() is None else None,
        }

    # ---------- IO ----------
    def _read_stderr(self, proc: subprocess.Popen) -> None:
        if not proc.stderr:
            return
        for line in proc.stderr:
            if self.proc is not proc:
                # 引擎已重启，旧进程的残留输出不能再影响新进程的状态
                return
            clean = line.rstrip()
            if not clean:
                continue
            self.logs.append(clean)
            if "Started, ready" in clean:
                self.ready_at = time.time()
                self._auto_restarts = 0
                self.ready.set()
            if "Using OpenCL Device" in clean:
                self.backend = "OpenCL"
                m = re.search(r"Using OpenCL Device \d+: ([^(]+)", clean)
                if m:
                    self.device = m.group(1).strip()
            elif "Using CUDA device" in clean:
                self.backend = "CUDA"
                m = re.search(r"Using CUDA device[^:]*: ([^(]+)", clean)
                if m:
                    self.device = m.group(1).strip()
            elif "Using ONNX" in clean or "DirectML" in clean:
                self.backend = "DirectML"
            elif "Device 0:" in clean and not self.device:
                m = re.search(r"Device 0: ([^(]+)", clean)
                if m:
                    self.device = m.group(1).strip()
        if self.proc is not proc:
            return
        # stderr 关闭意味着进程退出：清 ready、通知所有等待者，并尝试有限自动重启
        self.ready.clear()
        if self._stopping:
            self._fail_pending("AI 引擎正在重启")
            return
        self.error = self._describe_exit(proc)
        LOG.warning("%s", self.error)
        self._fail_pending(self.error)
        self._schedule_auto_restart()

    def _read_stdout(self, proc: subprocess.Popen) -> None:
        if not proc.stdout:
            return
        for line in proc.stdout:
            if self.proc is not proc:
                return
            line = line.strip()
            if not line.startswith("{"):
                if line:
                    self.logs.append(line)
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            qid = data.get("id")
            if not qid:
                continue
            with self.lock:
                p = self.pending.get(qid)
            if p:
                p.push(data)

    def request(self, query: dict, timeout: float = 30, callback: Callable[[dict], None] | None = None,
                expected: int = 1) -> list[dict]:
        if not self.ready.is_set():
            self._wait_ready(420)
        with self.lock:
            self._seq += 1
            qid = query.get("id") or f"q{self._seq}"
            query = dict(query)
            query["id"] = qid
            pending = Pending(expected, callback)
            self.pending[qid] = pending
        line = json.dumps(query, ensure_ascii=False, separators=(",", ":"))
        try:
            with self.write_lock:
                if not self.proc or not self.proc.stdin:
                    raise EngineError("AI 引擎没有运行")
                self.proc.stdin.write(line + "\n")
                self.proc.stdin.flush()
        except Exception as exc:
            with self.lock:
                self.pending.pop(qid, None)
            raise EngineError(f"无法发送请求到 AI 引擎：{exc}") from exc
        if not pending.event.wait(timeout=timeout):
            with self.lock:
                self.pending.pop(qid, None)
            # 请求终止，避免继续占用计算资源
            try:
                with self.write_lock:
                    if self.proc and self.proc.stdin:
                        self.proc.stdin.write(json.dumps({"id": qid + "-term", "action": "terminate", "targetIds": [qid]}) + "\n")
                        self.proc.stdin.flush()
            except Exception:
                pass
            raise EngineError("AI 计算超时，请稍后重试")
        with self.lock:
            self.pending.pop(qid, None)
        if pending.error:
            raise EngineError(pending.error)
        return pending.results

    # ---------- 请求构造 ----------
    def _query(self, game: GoGame, visits: int, *, include_ownership: bool = False,
               include_policy: bool = False, human_profile: str | None = None,
               analyze_turns: list[int] | None = None, priority: int = 10,
               pv_len: int = 10, extra_overrides: dict | None = None) -> dict:
        moves = [[m.color == BLACK and "B" or "W", m.vertex] for m in game.history]
        q = {
            "moves": moves,
            "initialStones": [[c == BLACK and "B" or "W", v] for c, v in game.initial_stones],
            "initialPlayer": "W" if game.handicap else "B",
            "rules": game.rules,
            "komi": game.komi,
            "boardXSize": game.size,
            "boardYSize": game.size,
            "maxVisits": int(visits),
            "analysisPVLen": pv_len,
            "includeOwnership": bool(include_ownership),
            "includePolicy": bool(include_policy),
            "priority": priority,
        }
        if game.handicap:
            # 与本软件的数子方式一致：让子棋白棋不另加让子补偿
            q["whiteHandicapBonus"] = "0"
        overrides = dict(extra_overrides or {})
        if human_profile:
            overrides.setdefault("humanSLProfile", human_profile)
            overrides.setdefault("ignorePreRootHistory", False)
            overrides.setdefault("rootNumSymmetriesToSample", 2)
        if overrides:
            q["overrideSettings"] = overrides
        if analyze_turns is not None:
            q["analyzeTurns"] = analyze_turns
            if len(analyze_turns) > 1:
                # KataGo 不允许同时提供 priority 和 priorities
                q.pop("priority", None)
                q["priorities"] = [priority] * len(analyze_turns)
        return q

    @staticmethod
    def _side_sign(current_player: str) -> int:
        return 1 if current_player == "B" else -1

    @staticmethod
    def _calibrate_winrate(raw: float, score_lead_black: float) -> float:
        """把不同网络的胜率校准到更符合目差的显示尺度。

        b10 变压器网络在开局对 7.5 贴目的胜率偏悲观（约 36%），但目差只有
        -1 目。这里以目差为主做一次温和校准，保证胜率条与“领先/落后”一致。
        原始胜率仍保留在 winrate_raw 字段中，供调试与进阶分析。
        """
        raw = max(0.001, min(0.999, float(raw)))
        lead = max(-80.0, min(80.0, float(score_lead_black)))
        logistic = 1.0 / (1.0 + math.exp(-lead / 6.0))
        return round(max(0.001, min(0.999, 0.15 * raw + 0.85 * logistic)), 4)

    def _format_candidates(self, result: dict, limit: int = 5) -> list[dict]:
        out = []
        for mi in sorted(result.get("moveInfos", []), key=lambda x: x.get("order", 99))[:limit]:
            # 配置 reportAnalysisWinratesAs = BLACK 时，scoreLead 也按黑棋视角报告
            lead_black = float(mi.get("scoreLead", 0.0))
            out.append({
                "move": mi.get("move", "pass"),
                "winrate": self._calibrate_winrate(float(mi.get("winrate", 0.5)), lead_black),
                "winrate_raw": round(float(mi.get("winrate", 0.5)), 4),
                "score_lead": round(lead_black, 2),
                "visits": int(mi.get("visits", 0)),
                "order": int(mi.get("order", 0)),
                "prior": round(float(mi.get("prior", 0.0)), 4),
                "pv": mi.get("pv", [])[:10],
            })
        return out

    @staticmethod
    def _format_eval(result: dict, fallback_move: str | None = None) -> dict:
        root = result.get("rootInfo", {})
        lead = float(root.get("scoreLead", 0.0))
        raw = float(root.get("winrate", 0.5))
        return {
            "winrate_black": AnalysisEngine._calibrate_winrate(raw, lead),
            "winrate_raw": round(raw, 4),
            "score_lead_black": round(lead, 2),
            "visits": int(root.get("visits", 0)),
            "current_player": root.get("currentPlayer", "B"),
            "best_move": fallback_move,
        }

    # ---------- 对外能力 ----------
    def choose_move(self, game: GoGame, level: dict) -> dict:
        started = time.time()
        human_profile = level.get("profile") if level.get("human") else None
        visits = int(level.get("visits", 400))
        if human_profile and not self.human_enabled:
            # 以前会静默退回职业档强度，界面仍显示“15级”，棋力与所选档位不符
            raise EngineError(
                f"缺少人类风格模型 {self.human_model}，无法使用人类档位；请改选职业档位或补全模型文件。"
            )
        if human_profile:
            result = self.request(
                self._query(game, max(8, visits), include_policy=True, human_profile=human_profile),
                timeout=120,
            )[0]
            move, candidates, policy_move = self._pick_human_move(game, result, level)
            data = {
                "move": move,
                "candidates": candidates,
                "eval": self._format_eval(result, candidates[0]["move"] if candidates else None),
                "elapsed": round(time.time() - started, 2),
                "style": "human",
                "profile": human_profile,
                "policy_move": policy_move,
            }
            return data
        # 职业强度：首选着
        result = self.request(self._query(game, visits), timeout=180)[0]
        candidates = self._format_candidates(result, 5)
        move = candidates[0]["move"] if candidates else "pass"
        return {
            "move": move,
            "candidates": candidates,
            "eval": self._format_eval(result, move),
            "elapsed": round(time.time() - started, 2),
            "style": "pro",
        }

    def _pick_human_move(self, game: GoGame, result: dict, level: dict) -> tuple[str, list[dict], str]:
        candidates = self._format_candidates(result, 8)
        policy = result.get("humanPolicy") or result.get("rootInfo", {}).get("humanPolicy") or []
        top = candidates[0]["move"] if candidates else "pass"
        # 官子阶段允许停一手；布局阶段不要因为人类模型的偏差而过早 pass
        if top == "pass" and len(game.history) > max(24, game.size * game.size // 10):
            return "pass", candidates, "pass"
        if not policy or len(policy) < game.size * game.size + 1:
            return top, candidates, top
        weights = []
        info_by_move = {c["move"]: c for c in candidates}
        utility_weight = float(level.get("utility_weight", 0.0))
        temperature = float(level.get("temperature", 1.0))
        for idx, p in enumerate(policy[: game.size * game.size + 1]):
            if p is None or p < 0:
                continue
            if idx == game.size * game.size:
                move = "pass"
                if len(game.history) <= max(24, game.size * game.size // 10):
                    continue
            else:
                move = self._index_to_vertex(idx, game.size)
                if game.stone_at(move) != 0:
                    continue
                ok, _ = game.is_legal(move)
                if not ok:
                    continue
            w = max(1e-12, float(p)) ** (1.0 / max(0.35, temperature))
            if utility_weight and move in info_by_move:
                # 让段位棋手在人类风格与正确性之间取得平衡
                utility = info_by_move[move].get("winrate", 0.5)
                w *= math.exp((utility - 0.5) * utility_weight * 6.0)
            weights.append((move, w))
        if not weights:
            return top, candidates, top
        total = sum(w for _, w in weights)
        r = random.random() * total
        acc = 0.0
        chosen = weights[-1][0]
        for move, w in weights:
            acc += w
            if r <= acc:
                chosen = move
                break
        return chosen, candidates, top

    @staticmethod
    def _index_to_vertex(idx: int, size: int) -> str:
        from go_engine import xy_to_vertex

        return xy_to_vertex(idx % size, idx // size, size)

    def hint(self, game: GoGame, visits: int = 500) -> dict:
        result = self.request(self._query(game, visits, priority=15, pv_len=12), timeout=180)[0]
        candidates = self._format_candidates(result, 5)
        return {
            "candidates": candidates,
            "eval": self._format_eval(result, candidates[0]["move"] if candidates else None),
            "recommended": candidates[0]["move"] if candidates else "pass",
        }

    def estimate(self, game: GoGame, visits: int = 220) -> dict:
        result = self.request(
            self._query(game, visits, include_ownership=True, priority=8, pv_len=3), timeout=180
        )[0]
        root = result.get("rootInfo", {})
        return {
            "ownership": result.get("ownership", []),
            "eval": self._format_eval(result, None),
            "score_lead_black": round(float(root.get("scoreLead", 0.0)), 2),
            "winrate_black": self._calibrate_winrate(float(root.get("winrate", 0.5)), float(root.get("scoreLead", 0.0))),
            "visits": int(root.get("visits", 0)),
        }

    def review(self, game: GoGame, visits: int = 80,
               progress: Callable[[dict, int, int], None] | None = None) -> list[dict]:
        turns = list(range(len(game.history) + 1))
        if not turns:
            return []
        total = len(turns)
        counter = {"n": 0}

        def on_result(data: dict) -> None:
            counter["n"] += 1
            if progress:
                progress(data, counter["n"], total)

        q = self._query(
            game,
            visits,
            analyze_turns=turns,
            priority=-5,
            pv_len=8,
        )
        results = self.request(q, timeout=max(300, total * 3), callback=on_result, expected=total)
        results.sort(key=lambda r: r.get("turnNumber", 0))
        return results
