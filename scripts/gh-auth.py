#!/usr/bin/env python3
r"""GitHub 设备授权流程（OAuth Device Flow），纯标准库。

为什么需要它：本机到 github.com 可以连通但被严重限速，下载官方 gh CLI（15MB）
需要十几分钟。这个脚本用与 `gh auth login` 完全相同的官方设备授权流程，
直接拿到一个 OAuth token，不需要下载任何二进制。

用法：
  python scripts/gh-auth.py --start            # 只申请设备码并打印（不轮询）
  python scripts/gh-auth.py --poll 600         # 打印设备码后轮询最多 600 秒
  python scripts/gh-auth.py --poll 600 --scope repo

拿到 token 后：
  - 写入 %USERPROFILE%\.dsh\gh-token.txt（权限仅当前用户），或
  - 只打印到标准输出（--print-token），由调用方决定怎么用

安全说明：
  - token 只做最小权限（默认 scope=repo），不申请 org/workflow 等额外权限；
  - 授权页面会显示应用名 "GitHub CLI"（这是 GitHub 官方 CLI 的 OAuth 应用，
    设备授权流程本身由 GitHub 提供，令牌可以在 GitHub 设置里随时撤销）；
  - 本脚本不保存密码，也不会打印 token 到日志文件。
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# GitHub CLI 的公开 OAuth client_id（在 cli/cli 源码中公开），
# 用它是为了走与 `gh auth login` 完全一致的官方流程。
CLIENT_ID = "178c6fc778ccc68e1d6a"
DEVICE_CODE_URL = "https://github.com/login/device/code"
TOKEN_URL = "https://github.com/login/oauth/access_token"
GRANT_TYPE = "urn:ietf:params:oauth:grant-type:device_code"
TOKEN_FILE = pathlib.Path(os.environ.get("USERPROFILE", pathlib.Path.home())) / ".dsh" / "gh-token.txt"


def _post(url: str, data: dict, timeout: int = 45) -> dict:
    body = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Accept", "application/json")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    req.add_header("User-Agent", "yixin-publish-auth")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def request_code(scope: str) -> dict:
    return _post(DEVICE_CODE_URL, {"client_id": CLIENT_ID, "scope": scope})


def poll_token(device_code: str, interval: int, expires_in: int) -> str | None:
    deadline = time.time() + expires_in
    while time.time() < deadline:
        time.sleep(interval)
        try:
            payload = _post(TOKEN_URL, {
                "client_id": CLIENT_ID,
                "device_code": device_code,
                "grant_type": GRANT_TYPE,
            })
        except Exception as exc:
            print(f"[warn] 轮询失败，稍后重试：{exc}", flush=True)
            continue
        error = payload.get("error")
        if not error:
            return payload.get("access_token")
        if error == "authorization_pending":
            continue
        if error == "slow_down":
            interval += 5
            continue
        print(f"[error] 授权失败：{error} {payload.get('error_description', '')}", flush=True)
        return None
    print("[error] 设备码已过期，需要重新申请。", flush=True)
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="GitHub 设备授权（纯标准库）")
    parser.add_argument("--start", action="store_true", help="只申请设备码")
    parser.add_argument("--poll", type=int, default=0, help="打印设备码后轮询的秒数")
    parser.add_argument("--scope", default="repo", help="OAuth scope，默认 repo（最小可用）")
    parser.add_argument("--print-token", action="store_true", help="成功后把 token 打到标准输出（默认写文件）")
    args = parser.parse_args()

    code = request_code(args.scope)
    if "user_code" not in code:
        print(f"[error] 申请设备码失败：{code}")
        return 1
    verify_url = code.get("verification_uri", "https://github.com/login/device")
    print("=" * 62, flush=True)
    print("  打开这个网址： " + verify_url, flush=True)
    print("  输入一次性代码： " + code["user_code"], flush=True)
    print(f"  （{code.get('expires_in', 900) // 60} 分钟内有效，输完不用管，这边会自动继续）", flush=True)
    print("=" * 62, flush=True)

    if args.start or args.poll <= 0:
        return 0

    token = poll_token(code["device_code"], int(code.get("interval", 5)), min(args.poll, int(code.get("expires_in", 900))))
    if not token:
        return 2
    if args.print_token:
        print("TOKEN=" + token, flush=True)
    else:
        TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        TOKEN_FILE.write_text(token, encoding="utf-8")
        try:  # 尽量收紧文件权限
            os.chmod(TOKEN_FILE, 0o600)
        except Exception:
            pass
        print(f"[ok] 已保存 token 到 {TOKEN_FILE}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
