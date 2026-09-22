<#
================================================================
 C 盘清理脚本  (A 档 + B 档)
================================================================
 生成于 2026-09-19，基于对你这台机器的实测数据。

 用法
 -----
 1. 按 Win 键，输入 powershell，右键选「以管理员身份运行」
 2. 执行（先干跑，只看不删）：
      powershell -ExecutionPolicy Bypass -File "本文件完整路径" -DryRun
 3. 确认无误后正式执行：
      powershell -ExecutionPolicy Bypass -File "本文件完整路径"

 说明
 -----
 A 档：缓存、日志、临时文件 —— 永久删除，全部会自动重建
 B 档：旧版本与升级包残留 —— 移动到 E 盘备份目录，可随时还原
 系统级项目（Windows\Panther 等）仅在管理员身份下才会处理

 不使用 robocopy，改用 .NET 原生 API，可正确处理长路径。
================================================================
#>
[CmdletBinding()]
param(
    [switch]$DryRun
)

$ErrorActionPreference = 'Continue'

# 让中文在控制台正确显示
try {
    chcp 65001 | Out-Null
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    $OutputEncoding = [System.Text.Encoding]::UTF8
} catch { }

$BackupRoot = 'E:\_CDriveCleanupBackup_20260919'
$script:TotalFreed = 0
$script:OkCount = 0
$script:FailCount = 0

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

function Get-Size {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return -1 }
    $s = (Get-ChildItem -LiteralPath $Path -Recurse -Force -File -ErrorAction SilentlyContinue |
          Measure-Object -Property Length -Sum).Sum
    if ($null -eq $s) { return 0 }
    return $s
}

function Remove-Tree {
    param([string]$Path)
    try {
        [System.IO.Directory]::Delete($Path, $true)
        return $true
    } catch {
        Write-Host ('      直接删除失败，改用递归方式：' + $_.Exception.Message) -ForegroundColor DarkYellow
    }
    try {
        Get-ChildItem -LiteralPath $Path -Recurse -Force -ErrorAction SilentlyContinue |
            Sort-Object { $_.FullName.Length } -Descending |
            ForEach-Object { try { $_.Delete() } catch {} }
        [System.IO.Directory]::Delete($Path, $true)
        return $true
    } catch {
        Write-Host ('      递归删除仍然失败：' + $_.Exception.Message) -ForegroundColor Red
        return $false
    }
}

function Show-Volume {
    Get-Volume -DriveLetter C | ForEach-Object {
        Write-Host ('      C 盘剩余：{0:N2} GB' -f ($_.SizeRemaining/1GB)) -ForegroundColor Cyan
    }
}

# ------------------------------------------------------------------
# A 档：永久删除（可再生成内容）
# ------------------------------------------------------------------
$TierA = @(
    @{P='C:\Users\admin\AppData\Local\npm-cache';                                       N='npm 包缓存'},
    @{P='C:\Users\admin\.cache\codex-runtimes';                                         N='codex 运行时压缩包缓存'},
    @{P='C:\Users\admin\AppData\Local\pip\Cache';                                       N='pip 包缓存'},
    @{P='C:\Users\admin\AppData\Local\pnpm-cache';                                      N='pnpm 包缓存'},
    @{P='C:\Users\admin\AppData\Local\electron';                                        N='electron 下载缓存'},
    @{P='C:\Users\admin\AppData\Roaming\Tencent\QQ\AuTemp';                             N='QQ 临时文件 AuTemp'},
    @{P='C:\Users\admin\AppData\Roaming\Tencent\QQ\Temp';                               N='QQ 临时文件 Temp'},
    @{P='C:\Users\admin\AppData\Roaming\Tencent\QQ\STemp';                              N='QQ 临时文件 STemp'},
    @{P='C:\Users\admin\AppData\Roaming\Tencent\QQ\webkit_cache';                       N='QQ webkit 缓存'},
    @{P='C:\Users\admin\AppData\Roaming\LarkShell\sdk_storage\log';                     N='飞书运行日志'},
    @{P='C:\Users\admin\AppData\Local\Temp';                                            N='用户临时文件'},
    @{P='C:\Users\admin\AppData\Local\CrashDumps';                                      N='崩溃转储'},
    @{P='C:\Users\admin\AppData\Roaming\baidunetdisk\Cache';                            N='百度网盘缓存'},
    @{P='C:\Users\admin\AppData\Roaming\baidunetdisk\Code Cache';                       N='百度网盘代码缓存'},
    @{P='C:\Users\admin\AppData\Roaming\TRAE SOLO CN\CachedData';                       N='TRAE 缓存数据'},
    @{P='C:\Users\admin\AppData\Roaming\TRAE SOLO CN\Cache';                            N='TRAE 缓存'},
    @{P='C:\Users\admin\AppData\Roaming\TRAE SOLO CN\logs';                             N='TRAE 日志'},
    @{P='C:\Users\admin\AppData\Roaming\TRAE SOLO CN\GPUCache';                         N='TRAE GPU 缓存'},
    @{P='C:\Users\admin\AppData\Local\Doubao\User Data\gecko_cache';                    N='豆包网页缓存'},
    @{P='C:\Users\admin\AppData\Roaming\Tencent\Logs';                                  N='腾讯系日志'},
    @{P='C:\Users\admin\AppData\Roaming\Tencent\QQLive\CacheFile';                      N='QQ 视频缓存'},
    @{P='C:\Users\admin\AppData\Local\Microsoft\Edge\User Data\component_crx_cache';    N='Edge 组件缓存'},
    @{P='C:\Users\admin\AppData\Local\Microsoft\Edge\User Data\extensions_crx_cache';   N='Edge 扩展缓存'},
    @{P='C:\Users\admin\AppData\Local\Microsoft\Edge\User Data\Default\Service Worker'; N='Edge 站点缓存(5.61GB)'},
    @{P='C:\Users\admin\AppData\Local\Microsoft\Edge\User Data\Default\Cache';          N='Edge 磁盘缓存'},
    @{P='C:\Users\admin\AppData\Local\Microsoft\Edge\User Data\Default\GPUCache';       N='Edge GPU 缓存'},
    @{P='C:\Users\admin\AppData\Local\Microsoft\Edge\User Data\Default\DawnWebGPUCache';N='Edge WebGPU 缓存'}
)

# ------------------------------------------------------------------
# B 档：移动到 E 盘备份（可还原）
# ------------------------------------------------------------------
$TierB = @(
    @{P='C:\Users\admin\AppData\Roaming\Tencent\WXWork\upgrade\5.0.9.6060';   N='企业微信升级包 5.0.9.6060'},
    @{P='C:\Users\admin\AppData\Local\Kingsoft\WPS Office\12.1.0.28043';      N='WPS 旧版本 12.1.0.28043'},
    @{P='C:\Users\admin\AppData\Roaming\Tencent\WeGame\qbcore91';             N='WeGame 旧内核 qbcore91'},
    @{P='C:\Users\admin\AppData\Local\perfectworldarena-updater';             N='完美世界更新器'},
    @{P='C:\Users\admin\AppData\Local\@genieworkbuddy-desktop-updater';       N='WorkBuddy 更新器'},
    @{P='C:\Users\admin\AppData\Local\obsidian-updater';                      N='Obsidian 更新器'},
    @{P='C:\Users\admin\AppData\Local\@zcodedesktop-updater';                 N='ZCode 更新器'},
    @{P='C:\Users\admin\AppData\Local\coze-updater';                          N='Coze 更新器'}
)

# ------------------------------------------------------------------
# 管理员专属
# ------------------------------------------------------------------
$TierAdmin = @(
    @{P='C:\Windows\Panther';                                                 N='Windows 安装日志'},
    @{P='C:\Program Files\Google\Chrome.old';                                 N='Chrome 旧版本'},
    @{P='C:\Program Files (x86)\Google\GoogleUpdater\crx_cache';              N='Chrome 更新缓存'},
    @{P='C:\$WINDOWS.~BT';                                                    N='系统升级残留'}
)

Write-Host ''
Write-Host '================================================================' -ForegroundColor White
Write-Host ' C 盘清理' -ForegroundColor White
if ($DryRun) { Write-Host ' 模式：干跑（只看不删）' -ForegroundColor Yellow }
else         { Write-Host ' 模式：正式执行' -ForegroundColor Yellow }
Write-Host (' 管理员权限：' + $(if ($isAdmin) { '是' } else { '否（系统级项目将跳过）' })) -ForegroundColor Yellow
Write-Host '================================================================' -ForegroundColor White
Write-Host ''
Show-Volume
Write-Host ''

# ---------- A 档 ----------
Write-Host '[A 档] 缓存与日志 —— 永久删除' -ForegroundColor Green
Write-Host ''
foreach ($t in $TierA) {
    $before = Get-Size $t.P
    if ($before -lt 0) { Write-Host ('  跳过（不存在）  ' + $t.N) -ForegroundColor DarkGray; continue }
    if ($before -lt 1MB) { Write-Host ('  跳过（已空）    ' + $t.N) -ForegroundColor DarkGray; continue }

    Write-Host ('  处理中  {0,-26} {1,7:N2} GB' -f $t.N, ($before/1GB)) -ForegroundColor White
    if ($DryRun) { continue }

    $ok = Remove-Tree $t.P
    $after = Get-Size $t.P
    if ($after -lt 0) { $after = 0 }
    $freed = $before - $after
    $script:TotalFreed += $freed
    if ($freed -gt ($before * 0.9)) {
        $script:OkCount++
        Write-Host ('          已清理，释放 {0:N2} GB' -f ($freed/1GB)) -ForegroundColor Green
    } else {
        $script:FailCount++
        Write-Host ('          仅释放 {0:N2} GB，剩余 {1:N2} GB 未能删除（可能被程序占用）' -f ($freed/1GB), ($after/1GB)) -ForegroundColor Yellow
    }
}
Write-Host ''

# ---------- B 档 ----------
Write-Host '[B 档] 旧版本与升级包 —— 移动到 E 盘备份' -ForegroundColor Green
Write-Host ('  备份目录：' + $BackupRoot) -ForegroundColor DarkGray
Write-Host ''
if (-not $DryRun) { New-Item -ItemType Directory -Path $BackupRoot -Force | Out-Null }
foreach ($t in $TierB) {
    $before = Get-Size $t.P
    if ($before -lt 0) { Write-Host ('  跳过（不存在）  ' + $t.N) -ForegroundColor DarkGray; continue }
    if ($before -lt 1MB) { Write-Host ('  跳过（已空）    ' + $t.N) -ForegroundColor DarkGray; continue }

    Write-Host ('  处理中  {0,-26} {1,7:N2} GB' -f $t.N, ($before/1GB)) -ForegroundColor White
    if ($DryRun) { continue }

    $dest = Join-Path $BackupRoot ($t.N -replace '[\\/:*?"<>|]', '_')
    try {
        Move-Item -LiteralPath $t.P -Destination $dest -Force -ErrorAction Stop
        $after = Get-Size $t.P
        if ($after -lt 0) { $after = 0 }
        $freed = $before - $after
        $script:TotalFreed += $freed
        $script:OkCount++
        Write-Host ('          已移出，释放 {0:N2} GB（备份在 ' + $dest + '）' -f ($freed/1GB)) -ForegroundColor Green
    } catch {
        $script:FailCount++
        Write-Host ('          移动失败：' + $_.Exception.Message) -ForegroundColor Red
    }
}
Write-Host ''

# ---------- 管理员项 ----------
if ($isAdmin) {
    Write-Host '[系统级] 需要管理员权限' -ForegroundColor Green
    Write-Host ''
    foreach ($t in $TierAdmin) {
        $before = Get-Size $t.P
        if ($before -lt 0) { Write-Host ('  跳过（不存在）  ' + $t.N) -ForegroundColor DarkGray; continue }
        if ($before -lt 1MB) { Write-Host ('  跳过（已空）    ' + $t.N) -ForegroundColor DarkGray; continue }
        Write-Host ('  处理中  {0,-26} {1,7:N2} GB' -f $t.N, ($before/1GB)) -ForegroundColor White
        if ($DryRun) { continue }
        $ok = Remove-Tree $t.P
        $after = Get-Size $t.P
        if ($after -lt 0) { $after = 0 }
        $freed = $before - $after
        $script:TotalFreed += $freed
        if ($freed -gt 0) { $script:OkCount++ } else { $script:FailCount++ }
        Write-Host ('          释放 {0:N2} GB' -f ($freed/1GB)) -ForegroundColor Green
    }
    Write-Host ''
} else {
    Write-Host '[系统级] 未以管理员身份运行，跳过 4 项（约 1.8 GB）' -ForegroundColor DarkYellow
    Write-Host '        Windows\Panther / Chrome.old / crx_cache / $WINDOWS.~BT' -ForegroundColor DarkGray
    Write-Host ''
}

# ---------- 汇总 ----------
Write-Host '================================================================' -ForegroundColor White
if ($DryRun) {
    Write-Host ' 干跑结束，未做任何修改。' -ForegroundColor Yellow
} else {
    Write-Host (' 成功 {0} 项，未完全清理 {1} 项' -f $script:OkCount, $script:FailCount) -ForegroundColor White
    Write-Host (' 本次释放：{0:N2} GB' -f ($script:TotalFreed/1GB)) -ForegroundColor Green
}
Show-Volume
Write-Host '================================================================' -ForegroundColor White
Write-Host ''
if (-not $DryRun) {
    Write-Host ' 提示：B 档文件已移到 E 盘备份目录，确认一切正常后可删除该目录。' -ForegroundColor DarkGray
    Write-Host ('       ' + $BackupRoot) -ForegroundColor DarkGray
    Write-Host ''
}
