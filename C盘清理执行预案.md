# C 盘清理执行预案

> 生成时间：2026-09-19 · 状态：**待你确认，尚未执行任何删除动作**

---

## 一、干跑结果（16 项，合计 9.33 GB）

| # | 大小 | 文件数 | 子目录 | 最近修改 | 权限 | 项目 | 路径 |
|---|---|---|---|---|---|---|---|
| 1 | 2.20 GB | 45,465 | 10,424 | 2026-09-19 | 用户 | npm 包缓存 | `C:\Users\admin\AppData\Local\npm-cache` |
| 2 | 1.86 GB | 21,741 | 2,045 | 2026-09-10 | 用户 | codex 运行时压缩包 | `C:\Users\admin\.cache\codex-runtimes` |
| 3 | 0.61 GB | 708 | 2,233 | 2026-09-16 | 用户 | pip 缓存 | `C:\Users\admin\AppData\Local\pip\Cache` |
| 4 | 0.56 GB | 191 | 21 | 2026-09-18 | **管理员** | Windows 安装日志 | `C:\Windows\Panther` |
| 5 | 0.52 GB | 3 | 1 | 2026-08-19 | 用户 | 完美世界更新器残留 | `...\AppData\Local\perfectworldarena-updater` |
| 6 | 0.49 GB | 272 | 14 | 2026-08-28 | **管理员** | Chrome 旧版本 | `C:\Program Files\Google\Chrome.old` |
| 7 | 0.49 GB | 3 | 0 | 2026-09-19 | **管理员** | Chrome 更新缓存 | `...\GoogleUpdater\crx_cache` |
| 8 | 0.45 GB | 1 | 0 | 2026-09-17 | 用户 | WorkBuddy 更新器安装包 | `...\Local\@genieworkbuddy-desktop-updater` |
| 9 | 0.39 GB | 1,042 | 547 | 2026-09-19 | 用户 | pnpm 缓存 | `C:\Users\admin\AppData\Local\pnpm-cache` |
| 10 | 0.35 GB | 130 | 33 | 2026-09-19 | 用户 | 用户临时文件 | `C:\Users\admin\AppData\Local\Temp` |
| 11 | 0.31 GB | 1 | 0 | 2026-08-23 | 用户 | Obsidian 更新器安装包 | `...\Local\obsidian-updater` |
| 12 | 0.30 GB | 5 | 1 | 2026-09-12 | 用户 | ZCode 更新器 | `...\Local\@zcodedesktop-updater` |
| 13 | 0.25 GB | 2 | 3 | 2026-09-12 | 用户 | electron 下载缓存 | `...\AppData\Local\electron` |
| 14 | 0.25 GB | 579 | 154 | 2026-09-15 | **管理员** | 系统升级残留 | `C:\$WINDOWS.~BT` |
| 15 | 0.18 GB | 4 | 1 | 2026-09-10 | 用户 | Coze 更新器 | `...\AppData\Local\coze-updater` |
| 16 | 0.13 GB | 10 | 0 | 2026-09-18 | 用户 | 崩溃转储 | `...\AppData\Local\CrashDumps` |

---

## 二、三个必须先说清楚的问题

### 1. 回收站不会释放空间

这是最容易被忽略的一点：把文件移进回收站，**磁盘占用一点没变**，只是从"某目录"变成了"回收站里的 45,000 个文件"。要真正腾出空间，还得再清空一次回收站——那才是真正的永久删除。

所以这 9.33 GB 必须分两种方式处理：

| 类型 | 处理方式 | 原因 |
|---|---|---|
| 纯缓存（npm / pnpm / pip / Temp / CrashDumps） | **用官方清理命令永久删除** | 本身就是可再生成的缓存，官方命令是标准做法，且立即释放空间 |
| 安装包与旧版本（更新器残留 / Chrome.old / $WINDOWS.~BT） | **走回收站**，观察几天再清空 | 保留一次后悔的机会 |

### 2. 有进程正在占用这些文件

实测当前运行的进程：

```
Doubao ×15    chrome ×11    node ×10    msedge ×8
python ×7     Docker Desktop ×5    wps ×2
com.docker.backend ×2    codex ×1
```

据此判断：

- **需要先关闭再清理**：Chrome（影响 #6 #7）、codex（影响 #2）、node 进程（影响 #1 #9）
- **未运行，可放心清理**：Obsidian、ZCode、Coze、飞书、微信 —— 所以 #8 #11 #12 #15 随时可以动

### 3. 长路径风险

`npm-cache` 内最长路径达 **223 字符**，`codex-runtimes` 208 字符，已接近 Windows 传统的 260 字符上限。手工在资源管理器里删容易中途报错失败。这也是**建议用官方命令而不是手动删**的实际理由之一。

---

## 三、建议的执行顺序

### 批次 A｜官方缓存清理（无需管理员，立即释放约 5.06 GB）

> 执行前先关闭 codex 与 node 相关进程。

```powershell
# npm 缓存 2.20 GB
npm cache clean --force

# pnpm 缓存 0.39 GB
pnpm store prune

# pip 缓存 0.61 GB
pip cache purge
```

`codex-runtimes`（1.86 GB）属于下载的运行时压缩包，关闭 codex 后可直接删除目录，下次运行会自动重新下载。

### 批次 B｜更新器残留（无需管理员，约 2.01 GB）

这些目录里只是"上次更新用过的安装包"，软件已经装完了：

```
...\AppData\Local\@genieworkbuddy-desktop-updater     0.45 GB
...\AppData\Local\perfectworldarena-updater            0.52 GB
...\AppData\Local\obsidian-updater                     0.31 GB
...\AppData\Local\@zcodedesktop-updater                0.30 GB
...\AppData\Local\electron                             0.25 GB
...\AppData\Local\coze-updater                         0.18 GB
```

### 批次 C｜临时文件（无需管理员，约 0.48 GB）

```
...\AppData\Local\Temp        0.35 GB   （建议只清 7 天前的文件）
...\AppData\Local\CrashDumps  0.13 GB
```

### 批次 D｜需要管理员权限（约 1.79 GB）

> 执行前先完全退出 Chrome。

```
C:\Windows\Panther                                        0.56 GB
C:\Program Files\Google\Chrome.old                        0.49 GB
C:\Program Files (x86)\Google\GoogleUpdater\crx_cache     0.49 GB
C:\$WINDOWS.~BT                                           0.25 GB
```

---

## 四、备份与回滚

**Tier 1 全部是可再生成的内容，不需要做数据备份**——这也是它风险为零的原因。回滚方式就是"重新触发一次下载"：

| 项目 | 回滚方式 |
|---|---|
| npm / pnpm / pip 缓存 | 下次安装依赖时自动重新下载 |
| codex 运行时 | 下次启动 codex 自动重新下载 |
| 各更新器安装包 | 软件下次更新时自动重新下载 |
| Chrome.old / Temp / CrashDumps / $WINDOWS.~BT | 无需回滚 |

我会在动手前额外生成一份**文件清单快照**（路径 + 大小 + 修改时间），存到 `E:\AI智能助手\.workbuddy-ai\` 下，方便事后核对到底动过什么。

---

## 五、执行纪律

1. 每批最多 10 项，做完一批核对一次再继续。
2. 走回收站的项目，逐项确认后才执行。
3. 任何一项失败立即停止，不继续下一项。
4. 全程不触碰 `C:\Windows\Installer`（存着软件卸载信息，手删会导致程序无法卸载）。
5. 第二档（虚拟内存、WinSxS）和第四档（卸载）单独确认，不与本预案混在一起执行。

---

## 六、预期效果

| 阶段 | 释放 | C 盘剩余 |
|---|---|---|
| 现在 | — | 19.4 GB |
| 完成第一档（本预案） | +9.3 GB | **约 28.7 GB** |
| 加第二档（虚拟内存 18.2 + WinSxS 2~4） | +20 GB | **约 49 GB** |
| 加第三档（数据迁到 D 盘） | +45.9 GB | **约 95 GB** |
