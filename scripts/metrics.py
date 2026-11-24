#!/usr/bin/env python3
r"""采集公开指标，追加到 docs/community/METRICS-LOG.md。

只读取公开数据（star / fork / release 下载 / topic 排名）。clone 数不公开，
只能在网页 Insights → Traffic 手动抄，本脚本不碰。

用法：
  python scripts/metrics.py --repo owner/name
  python scripts/metrics.py --repo owner/name --append       # 追加一行到指标日志
  set GITHUB_TOKEN=...   # 可选：带上后限额更高
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import pathlib
import re
import sys
import time
import urllib.error
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

API = "https://api.github.com"
LOG = pathlib.Path(__file__).resolve().parent.parent / "docs" / "community" / "METRICS-LOG.md"


def api(path: str, token: str | None, retries: int = 3):
    req = urllib.request.Request(API + path)
    req.add_header("User-Agent", "yixin-metrics")
    req.add_header("Accept", "application/vnd.github+json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    last = ""
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=45) as resp:
                raw = resp.read()
            return json.loads(raw)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")[:160]
            last = f"HTTP {exc.code}: {body}"
            if exc.code in (403, 429):  # 限速，等一下再试
                time.sleep(3 * attempt)
                continue
            return {"_error": last}
        except Exception as exc:  # IncompleteRead / 连接中断 / 超时
            last = f"{type(exc).__name__}: {exc}"
            time.sleep(2 * attempt)
    return {"_error": last}


def topic_rank(full_name: str, token: str | None) -> tuple[int | None, int | None]:
    data = api("/search/repositories?q=topic%3Aweiqi&sort=stars&order=desc&per_page=100", token)
    if "_error" in data:
        return None, None
    names = [item["full_name"].lower() for item in data.get("items", [])]
    if full_name.lower() in names:
        return names.index(full_name.lower()) + 1, data.get("total_count")
    return None, data.get("total_count")


def main() -> int:
    parser = argparse.ArgumentParser(description="采集仓库公开指标")
    parser.add_argument("--repo", required=True, help="owner/name")
    parser.add_argument("--append", action="store_true", help="追加到 docs/community/METRICS-LOG.md")
    parser.add_argument("--note", default="", help="本次记录备注")
    args = parser.parse_args()

    token = os.environ.get("GITHUB_TOKEN") or None
    owner, _, name = args.repo.partition("/")
    if not name:
        return print("--repo 需要 owner/name 形式") or 2

    repo = api(f"/repos/{owner}/{name}", token)
    if "_error" in repo:
        print(f"取仓库信息失败：{repo['_error']}")
        return 1

    releases = api(f"/repos/{owner}/{name}/releases", token)
    downloads = 0
    asset_lines = []
    if isinstance(releases, list):
        for rel in releases:
            for asset in rel.get("assets", []):
                downloads += asset.get("download_count", 0)
                asset_lines.append(f"{asset['name']}={asset['download_count']}")

    rank, total = topic_rank(args.repo, token)
    today = datetime.date.today().isoformat()

    summary = {
        "date": today,
        "repo": args.repo,
        "stars": repo.get("stargazers_count"),
        "forks": repo.get("forks_count"),
        "watchers": repo.get("subscribers_count"),
        "open_issues": repo.get("open_issues_count"),
        "downloads": downloads,
        "assets": asset_lines,
        "topic_weiqi_rank": rank,
        "topic_weiqi_total": total,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    if args.append:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        row = (f"| {today} | {summary['stars']} | {summary['forks']} | {summary['watchers']} | "
               f"{downloads} | {rank or '—'} / {total or '—'} | {args.note} |")
        text = LOG.read_text(encoding="utf-8") if LOG.exists() else ""
        marker = "| 2026-11-24 | 0（未发布）"
        if marker in text:
            # 首次真实数据时，把"未发布"基线行前面插入
            text = text.replace(marker, row + "\n" + marker, 1)
        else:
            if not text.endswith("\n"):
                text += "\n"
            text += row + "\n"
        LOG.write_text(text, encoding="utf-8")
        print(f"已追加到 {LOG}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
