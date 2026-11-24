# 发版清单

> 目标：一次发版 20 分钟内完成，且发出去的包在别人机器上能跑。

## 0. 前置

- [ ] `main` 分支 CI 全绿
- [ ] `python -m unittest discover -s tests -v` 本地全绿
- [ ] `grep -rnE '__[A-Z]+__' README.md README.en.md SECURITY.md .github` 无输出（发布脚本会替换 README/SECURITY/config.yml 里的仓库占位符）
- [ ] 版本号已更新：`CHANGELOG.md` 加段落；`server.py` 里的 `VERSION`（如有）同步

## 1. 打包

```powershell
# 干净构建（先删旧的 build/dist 产物）
Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue
python -m PyInstaller --noconfirm --clean --name 围棋陪练 --noconsole --icon assets\app.ico server.py

# 把 web/configs/engine/models 等运行时文件复制到 exe 同目录（见 docs/BUILD.md 6.3）
$out = "dist\围棋陪练"
Copy-Item -Recurse -Force web, configs, engine, models $out
New-Item -ItemType Directory -Force -Path "$out\third_party", "$out\data\games" | Out-Null
Copy-Item -Force README.md, README.en.md, LICENSE, THIRD_PARTY_NOTICES.md, CHANGELOG.md, third_party\KataGo-LICENSE.txt $out
Copy-Item -Force 启动围棋陪练.bat, 停止围棋陪练.bat, 调试启动.bat, 源码模式启动.bat, 解除SmartScreen锁定并启动.bat $out

# 组装 + 排除运行期产物 + 隐私检查 + 校验和
powershell -ExecutionPolicy Bypass -File scripts/package.ps1 -Version 0.1.0 -Source $out
```

产物：`dist/Yixin-Go-Sparring-<version>-portable.zip`

- [ ] 解压到**不含中文的路径**，双击 `启动围棋陪练.bat` 能出棋盘
- [ ] 在一台没装 Python 的机器/沙盒里再验一次
- [ ] 断网状态下能对局与复盘（验证真的离线）

## 2. 校验和

```powershell
Get-FileHash dist\Yixin-Go-Sparring-0.1.0-portable.zip -Algorithm SHA256 |
  ForEach-Object { "{0}  {1}" -f $_.Hash.ToLower(), (Split-Path $_.Path -Leaf) } |
  Out-File dist\SHA256SUMS.txt -Encoding ascii
```

## 3. 提交与打 tag

```powershell
git add -A
git commit -m "release: v0.1.0"
git tag -a v0.1.0 -m "v0.1.0"
```

> **本机注意**：`github.com:443` 不可达，`git push` 会超时。两条可行路径：
> 1. **API 发布（推荐）**：`python scripts/publish.py --owner <owner> --repo <repo> --tag v0.1.0 --asset dist\Yixin-Go-Sparring-0.1.0-portable.zip --notes-file docs\RELEASE-NOTES-0.1.0.md`
> 2. **SSH 推送**：`ssh.github.com:443` 可达；把公钥加到账号后，`git remote add origin ssh://git@ssh.github.com:443/<owner>/<repo>.git`，然后 `git push -u origin main --tags`（仍需先用网页/API 建空仓库）。

## 4. Release 说明模板

```markdown
## 弈心 · 围棋陪练 v0.1.0

Windows 本地围棋陪练，开箱即用，全程离线。

### 下载
- `Yixin-Go-Sparring-0.1.0-portable.zip`（约 230 MB，含 KataGo 引擎与两个模型）
- 解压到不含中文的路径，双击 `启动围棋陪练.bat`
- SHA256：`<填入>`

### 运行要求
- Windows 10/11 x64，支持 OpenCL 的显卡（核显可跑但慢）
- 首次启动会做 OpenCL 调优，可能等几分钟

### 本版变更
<从 CHANGELOG 摘录>

### 已知问题
<如实列出>

### 许可
项目代码 MIT。随包分发的 KataGo 引擎同为 MIT（其 `cpp/external/` 下第三方库另有许可），详见 THIRD_PARTY_NOTICES.md。
```

## 5. 发布后 24 小时

- [ ] Release 页确认 asset 可下载、大小正确、SHA256 与本地一致
- [ ] 仓库 About 的 description / topics 已设置
- [ ] **Social preview 已上传**：仓库 → Settings → General → Social preview → 上传 `assets/social-preview.png`（1280×640，GitHub API 不支持上传，只能网页设置；源文件 `assets/social-preview.html` 可重新渲染）
- [ ] Discussions 的「Show your work」帖已发（见 `docs/community/DRAFTS.md`）
- [ ] 记录基线指标到 `docs/community/METRICS-LOG.md`
- [ ] 观察是否有装机失败 issue，24h 内响应

## 6. 回滚

如果发布包有阻断性 bug：

1. 把 Release 标记为 `pre-release` 或删除该 tag 的 Release（不要删 tag，避免已有下载的校验和失效解释不清）；
2. 立即发 `v0.1.1` 修复版，在 Release 说明里写清影响范围；
3. 在 README 顶部临时加提示，指向新版本。
