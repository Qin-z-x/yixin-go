#!/usr/bin/env python3
r"""把当前工作区发布/更新到 GitHub 仓库。

为什么不用 git push：本机到 github.com:443 不通，但 api.github.com 与
uploads.github.com 可用。这个脚本用 GitHub Git Data API 直接建提交，
再用 REST API 建 Release 并上传资产，全流程只依赖 Python 标准库。

用法：
  # 先干跑，确认要提交的文件和占位符替换
  python scripts/publish.py --owner <owner> --repo <repo> --dry-run

  # 正式发布（token 从环境变量或文件读，别写进命令行）
  set GITHUB_TOKEN=github_pat_xxx
  python scripts/publish.py --owner <owner> --repo <repo> --tag v0.1.0 ^
      --asset dist\Yixin-Go-Sparring-0.1.0-portable.zip ^
      --notes-file docs\RELEASE-NOTES-0.1.0.md

token 建议用 fine-grained PAT，只授权目标仓库：
  Contents: Read and write / Administration: Read and write / Metadata: Read
"""

from __future__ import annotations

import argparse
import base64
import concurrent.futures
import json
import os
import pathlib
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

try:  # 控制台是 GBK 时也能正常打印中文
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

API = "https://api.github.com"
UPLOADS = "https://uploads.github.com"
PLACEHOLDER_OWNER = "Qin-z-x"
PLACEHOLDER_REPO = "yixin-go"
TEXT_SUFFIXES = {".md", ".markdown", ".yml", ".yaml", ".json", ".txt", ".html", ".css", ".js", ".py", ".cfg", ".bat", ".spec", ".toml"}


# ---------------------------------------------------------------- http helpers
def _request(method: str, url: str, token: str | None, payload=None, *, raw: bytes | None = None,
             content_type: str = "application/json", timeout: int = 60, retries: int = 4):
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    elif raw is not None:
        data = raw
    last_exc: Exception | None = None
    for attempt in range(1, retries + 1):
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("User-Agent", "yixin-publish-script")
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
        if data is not None:
            req.add_header("Content-Type", content_type)
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read()
                return resp.status, (json.loads(body) if body and body[:1] in (b"{", b"[") else body)
        except urllib.error.HTTPError as exc:
            body = exc.read()
            try:
                parsed = json.loads(body)
            except Exception:
                parsed = body.decode("utf-8", "replace")
            # 5xx / 限速 / 网关错误可以重试；4xx 除了 429 直接返回
            if exc.code in (429, 500, 502, 503, 504) and attempt < retries:
                print(f"  [retry {attempt}/{retries}] HTTP {exc.code} {method} {url}", flush=True)
                time.sleep(2 ** attempt)
                continue
            return exc.code, parsed
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
            last_exc = exc
            if attempt < retries:
                print(f"  [retry {attempt}/{retries}] 网络错误 {exc}，{2 ** attempt}s 后重试", flush=True)
                time.sleep(2 ** attempt)
                continue
            raise SystemExit(f"网络错误：{exc}\n如果连 api.github.com 都不通，需要先解决代理/VPN。") from exc
    raise SystemExit(f"网络错误（重试 {retries} 次后放弃）：{last_exc}")


def api(method: str, path: str, token: str, payload=None):
    status, body = _request(method, API + path, token, payload)
    if status >= 400:
        detail = body.get("message") if isinstance(body, dict) else body
        raise SystemExit(f"API {method} {path} -> {status}: {detail}")
    return body


# ---------------------------------------------------------------- file collection
def git_files(repo_root: pathlib.Path, git_exe: str | None) -> list[str]:
    if git_exe:
        try:
            out = subprocess.run(
                [git_exe, "-c", "core.quotepath=false", "ls-files", "-z",
                 "--cached", "--others", "--exclude-standard"],
                cwd=repo_root, capture_output=True, check=True,
            ).stdout
            names = [n for n in out.decode("utf-8").split("\0") if n]
            if names:
                return names
        except (OSError, subprocess.CalledProcessError):
            pass
    # 退路：按 .gitignore 里的目录名字硬排除（够用，不解析 gitignore 语法）
    skip_dirs = {".git", "engine", "models", "data", "dist", "logs", "build", "assets/..", "弈心围棋陪练-便携版", "__pycache__", ".tools", "tmp"}
    names = []
    for path in repo_root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(repo_root)
        if any(part in skip_dirs for part in rel.parts):
            continue
        if path.suffix in {".pyc", ".pyo", ".zip", ".exe", ".bin", ".gz"} and rel.parts[0] in {"engine", "models"}:
            continue
        names.append(rel.as_posix())
    return sorted(names)


def read_blob(repo_root: pathlib.Path, rel: str, owner: str, repo: str) -> tuple[str, str]:
    raw = (repo_root / rel).read_bytes()
    suffix = pathlib.Path(rel).suffix.lower()
    if suffix in TEXT_SUFFIXES or rel.endswith(".bat"):
        text = raw.decode("utf-8", "surrogateescape")
        if PLACEHOLDER_OWNER in text or PLACEHOLDER_REPO in text:
            text = text.replace(PLACEHOLDER_OWNER, owner).replace(PLACEHOLDER_REPO, repo)
            raw = text.encode("utf-8", "surrogateescape")
    return rel, base64.b64encode(raw).decode("ascii")


# ---------------------------------------------------------------- main flow
def main() -> int:
    parser = argparse.ArgumentParser(description="通过 GitHub REST API 发布仓库（无需 git push）")
    parser.add_argument("--owner", help="GitHub 用户名/组织名，默认取 token 对应账号")
    parser.add_argument("--repo", default="yixin-go", help="仓库名，默认 yixin-go")
    parser.add_argument("--description", default=(
        "Local-first Go (weiqi/baduk) sparring partner for Windows. KataGo + Human SL, "
        "no setup: open the browser and play. Hints, territory, review, SGF — all offline."
    ))
    parser.add_argument("--topics", default="weiqi,baduk,go-game,katago,go-board,sgf,desktop-app,python,offline,human-sl,board-game,local-first")
    parser.add_argument("--tag", default="", help="同时创建 Release，例如 v0.1.0")
    parser.add_argument("--asset", action="append", default=[], help="Release 资产文件路径，可重复")
    parser.add_argument("--notes-file", default="", help="Release 说明 Markdown 文件")
    parser.add_argument("--message", default="release: publish Yixin Go Sparring", help="提交信息")
    parser.add_argument("--token-file", default="", help="存放 token 的文件（避免环境变量泄漏）")
    parser.add_argument("--git", default="", help="git.exe 完整路径（可选，用于精确列出文件）")
    parser.add_argument("--dry-run", action="store_true", help="只打印计划，不发请求")
    args = parser.parse_args()

    repo_root = pathlib.Path(__file__).resolve().parent.parent
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token and args.token_file:
        token = pathlib.Path(args.token_file).read_text(encoding="utf-8").strip()
    if not token:  # device-flow 授权结果默认落在这里
        default_token = pathlib.Path(os.environ.get("USERPROFILE", pathlib.Path.home())) / ".dsh" / "gh-token.txt"
        if default_token.exists():
            token = default_token.read_text(encoding="utf-8").strip()
            print(f"使用已授权 token：{default_token}")
    if not token and not args.dry_run:
        return print("缺少 token：先运行 python scripts/gh-auth.py --poll 600 完成设备授权，"
                     "或设置 GITHUB_TOKEN 环境变量，或用 --token-file 指定文件。") or 2

    files = git_files(repo_root, args.git or None)
    if not files:
        return print("没有找到要发布的文件。") or 2

    owner = args.owner
    if not args.dry_run and not owner:
        user = api("GET", "/user", token)
        owner = user["login"]
    owner = owner or "<token-owner>"

    print(f"目标仓库：{owner}/{args.repo}")
    print(f"待发布文件：{len(files)} 个")
    replaced = [f for f in files if pathlib.Path(f).suffix.lower() in TEXT_SUFFIXES]
    print(f"其中会做占位符替换（Qin-z-x/yixin-go）的文本文件：{len(replaced)} 个")

    if args.dry_run:
        print("\n[dry-run] 计划调用：")
        print(f"  GET    /user")
        print(f"  POST   /user/repos                (不存在时创建 {args.repo})")
        print(f"  POST   /repos/{owner}/{args.repo}/git/blobs        × {len(files)}")
        print(f"  POST   /repos/{owner}/{args.repo}/git/trees")
        print(f"  POST   /repos/{owner}/{args.repo}/git/commits")
        print(f"  PATCH  /repos/{owner}/{args.repo}/git/refs/heads/main")
        print(f"  PUT    /repos/{owner}/{args.repo}/topics")
        if args.tag:
            print(f"  POST   /repos/{owner}/{args.repo}/releases         (tag {args.tag})")
            for asset in args.asset:
                print(f"  UPLOAD {asset} -> {pathlib.Path(asset).name}")
        print("\n[dry-run] 未发现占位符残留问题就绪，可去掉 --dry-run 正式发布。")
        return 0

    # 1. 创建仓库（已存在则跳过）
    status, _ = _request("GET", f"{API}/repos/{owner}/{args.repo}", token)
    if status == 404:
        api("POST", "/user/repos", token, {
            "name": args.repo,
            "description": args.description,
            "private": False,
            "has_issues": True,
            "has_discussions": True,
            "has_wiki": False,
            "has_projects": False,
        })
        print("已创建仓库")
    else:
        print("仓库已存在，走更新流程")

    # 1.5 空仓库无法使用 Git Data API（blobs 会返回 409），先初始化一个提交
    status, _ = _request("GET", f"{API}/repos/{owner}/{args.repo}/git/ref/heads/main", token)
    if status != 200:
        import base64 as _b64
        api("PUT", f"/repos/{owner}/{args.repo}/contents/.gitkeep", token, {
            "message": "chore: initialize repository",
            "content": _b64.b64encode(b"init\n").decode("ascii"),
            "branch": "main",
        })
        print("空仓库已初始化（首次提交）")

    # 2. 上传 blob
    def upload(rel: str):
        path, b64 = read_blob(repo_root, rel, owner, args.repo)
        body = api("POST", f"/repos/{owner}/{args.repo}/git/blobs", token, {"content": b64, "encoding": "base64"})
        return path, body["sha"]

    entries: list[dict] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        for rel, sha in pool.map(upload, files):
            entries.append({"path": rel, "mode": "100644", "type": "blob", "sha": sha})
    print(f"blob 上传完成：{len(entries)} 个")

    # 3. 建 tree / commit / 移动 ref
    tree = api("POST", f"/repos/{owner}/{args.repo}/git/trees", token, {"tree": entries})
    status, ref = _request("GET", f"{API}/repos/{owner}/{args.repo}/git/ref/heads/main", token)
    parents = []
    if status == 200:
        parents = [ref["object"]["sha"]]
        status_head, head_commit = _request("GET", f"{API}/repos/{owner}/{args.repo}/commits/main", token)
        if status_head == 200 and head_commit.get("commit", {}).get("tree", {}).get("sha") == tree["sha"]:
            print("内容无变化，跳过提交")
            commit_sha = parents[0]
        else:
            commit = api("POST", f"/repos/{owner}/{args.repo}/git/commits", token, {
                "message": args.message, "tree": tree["sha"], "parents": parents,
            })
            commit_sha = commit["sha"]
            api("PATCH", f"/repos/{owner}/{args.repo}/git/refs/heads/main", token, {"sha": commit_sha, "force": True})
            print(f"已提交：{commit_sha[:12]}")
    else:
        commit = api("POST", f"/repos/{owner}/{args.repo}/git/commits", token, {
            "message": args.message, "tree": tree["sha"], "parents": [],
        })
        commit_sha = commit["sha"]
        api("POST", f"/repos/{owner}/{args.repo}/git/refs", token, {"ref": "refs/heads/main", "sha": commit_sha})
        print(f"已建立 main 分支：{commit_sha[:12]}")

    # 4. 元数据
    api("PUT", f"/repos/{owner}/{args.repo}/topics", token, {"names": [t.strip() for t in args.topics.split(",") if t.strip()]})
    api("PATCH", f"/repos/{owner}/{args.repo}", token, {"description": args.description, "default_branch": "main"})
    print("仓库元数据已更新（description / topics）")

    # 5. Release
    if args.tag:
        notes = ""
        if args.notes_file and pathlib.Path(args.notes_file).exists():
            notes = pathlib.Path(args.notes_file).read_text(encoding="utf-8")
        status, release = _request("GET", f"{API}/repos/{owner}/{args.repo}/releases/tags/{args.tag}", token)
        if status == 200:
            print(f"Release {args.tag} 已存在，跳过创建")
            release_id = release["id"]
        else:
            release = api("POST", f"/repos/{owner}/{args.repo}/releases", token, {
                "tag_name": args.tag,
                "target_commitish": commit_sha,
                "name": f"Yixin Go Sparring {args.tag}",
                "body": notes or f"See CHANGELOG.md for details.",
                "draft": False,
                "prerelease": False,
            })
            release_id = release["id"]
            print(f"Release 已创建：{release['html_url']}")
        for asset in args.asset:
            path = pathlib.Path(asset)
            if not path.exists():
                print(f"跳过不存在的资产：{asset}")
                continue
            raw = path.read_bytes()
            url = f"{UPLOADS}/repos/{owner}/{args.repo}/releases/{release_id}/assets?name={urllib.parse.quote(path.name)}"
            status, body = _request("POST", url, token, raw=raw, content_type="application/octet-stream", timeout=900)
            print(f"资产上传 {path.name}: HTTP {status}")

    print(f"\n完成：https://github.com/{owner}/{args.repo}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
