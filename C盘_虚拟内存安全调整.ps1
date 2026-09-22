<#
================================================================
 C 盘虚拟内存（pagefile）安全调整
================================================================
 为什么需要这个脚本：
   提交上限 = 物理内存 + 页面文件。
   如果页面文件调得比"当前提交内存"还小，系统会拒绝内存分配，
   程序会报「内存不足」或直接崩溃。

   这个脚本会先检查你的提交内存，只有在安全的情况下才允许修改，
   否则会拒绝执行并告诉你还差多少。

 用法（必须以管理员身份运行 PowerShell）
 --------------------------------------
 1. 先看当前状态，不做任何修改：
      powershell -ExecutionPolicy Bypass -File "本文件路径" -DryRun

 2. 按建议值修改（默认 16384 MB = 16 GB）：
      powershell -ExecutionPolicy Bypass -File "本文件路径"

 3. 自定义大小：
      powershell -ExecutionPolicy Bypass -File "本文件路径" -SizeMB 24576

 4. 恢复系统自动管理：
      powershell -ExecutionPolicy Bypass -File "本文件路径" -Restore

 修改后需要重启才生效。
================================================================
#>
[CmdletBinding()]
param(
    [int]$SizeMB = 16384,
    [switch]$DryRun,
    [switch]$Restore
)

$ErrorActionPreference = 'Stop'
try {
    chcp 65001 | Out-Null
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    $OutputEncoding = [System.Text.Encoding]::UTF8
} catch { }

function Line($s) { Write-Host $s }
function Head($s) { Write-Host ''; Write-Host ('== ' + $s) -ForegroundColor Cyan }

# ---------- 权限检查 ----------
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host '需要管理员权限。请右键 PowerShell 选择「以管理员身份运行」后重试。' -ForegroundColor Red
    exit 1
}

# ---------- 恢复自动管理 ----------
if ($Restore) {
    Line '正在恢复「系统自动管理页面文件」...'
    $cs = Get-CimInstance Win32_ComputerSystem
    $cs.AutomaticManagedPagefile = $true
    Set-CimInstance -InputObject $cs
    Line '已恢复。重启后生效。' -ForegroundColor Green
    exit 0
}

# ---------- 采集当前数据 ----------
$os  = Get-CimInstance Win32_OperatingSystem
$ramGB = [math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB, 2)

$committed = $null
$limit     = $null
$availMB   = $null
try {
    $c = Get-Counter -Counter @('\Memory\Committed Bytes','\Memory\Commit Limit','\Memory\Available MBytes') -ErrorAction Stop
    foreach ($s in $c.CounterSamples) {
        if ($s.Path -like '*committed bytes*') { $committed = $s.CookedValue }
        elseif ($s.Path -like '*commit limit*') { $limit = $s.CookedValue }
        elseif ($s.Path -like '*available mbytes*') { $availMB = $s.CookedValue }
    }
} catch {
    Write-Host ('读取性能计数器失败：' + $_.Exception.Message) -ForegroundColor Red
    exit 1
}

$pf = Get-CimInstance Win32_PageFileUsage | Where-Object { $_.Name -like 'C:*' }
$pfNowGB = 0
if ($pf) { $pfNowGB = [math]::Round($pf.AllocatedBaseSize / 1024, 2) }

$committedGB = [math]::Round($committed / 1GB, 2)
$limitGB     = [math]::Round($limit / 1GB, 2)

Head '当前状态'
Line ('  物理内存            : {0,8:N2} GB' -f $ramGB)
Line ('  提交内存 (已用)     : {0,8:N2} GB' -f $committedGB)
Line ('  提交上限            : {0,8:N2} GB' -f $limitGB)
Line ('  可用物理内存        : {0,8:N0} MB' -f $availMB)
Line ('  页面文件当前分配    : {0,8:N2} GB' -f $pfNowGB)
if ($pf) {
    Line ('  页面文件实际使用    : {0,8:N0} MB  (峰值 {1:N0} MB)' -f $pf.CurrentUsage, $pf.PeakUsage)
}

# ---------- 安全检查 ----------
Head '安全校验'
$newLimitGB = [math]::Round($ramGB + ($SizeMB / 1024.0), 2)
$headroomGB = [math]::Round($newLimitGB - $committedGB, 2)

Line ('  拟设页面文件        : {0,8:N0} MB ({1:N2} GB)' -f $SizeMB, ($SizeMB/1024.0))
Line ('  修改后的提交上限    : {0,8:N2} GB  (物理内存 {1:N2} + 页面文件 {2:N2})' -f $newLimitGB, $ramGB, ($SizeMB/1024.0))
Line ('  当前已用提交内存    : {0,8:N2} GB' -f $committedGB)
Line ('  修改后剩余余量      : {0,8:N2} GB' -f $headroomGB)

$MIN_HEADROOM = 4.0
if ($headroomGB -lt $MIN_HEADROOM) {
    Write-Host ''
    Write-Host '  ✗ 拒绝执行：余量不足' -ForegroundColor Red
    Write-Host ''
    Write-Host ('  修改后提交上限只有 {0:N2} GB，而当前已用 {1:N2} GB，' -f $newLimitGB, $committedGB) -ForegroundColor Red
    Write-Host ('  余量仅 {0:N2} GB，低于安全线 {1:N2} GB。' -f $headroomGB, $MIN_HEADROOM) -ForegroundColor Red
    Write-Host '  强行修改会导致程序报「内存不足」甚至崩溃。' -ForegroundColor Red
    Write-Host ''
    Write-Host '  请先关闭占内存的进程，把提交内存降下来。当前占用大户：' -ForegroundColor Yellow
    Get-Process | Group-Object ProcessName | ForEach-Object {
        [pscustomobject]@{
            Name = $_.Name
            Cnt  = $_.Count
            Priv = ($_.Group | Measure-Object -Property PrivateMemorySize64 -Sum).Sum / 1GB
        }
    } | Sort-Object Priv -Descending | Select-Object -First 10 | ForEach-Object {
        Write-Host ('    {0,7:N2} GB  x{1,-4} {2}' -f $_.Priv, $_.Cnt, $_.Name) -ForegroundColor Yellow
    }
    Write-Host ''
    Write-Host '  建议优先关闭：Docker Desktop + WSL（然后执行 wsl --shutdown）' -ForegroundColor Yellow
    Write-Host ('  若提交内存降到 {0:N2} GB 以下，本脚本即可安全执行。' -f ($newLimitGB - $MIN_HEADROOM)) -ForegroundColor Yellow
    Write-Host ''
    Write-Host ('  想放宽到更大的页面文件？例如 -SizeMB {0}' -f ([int](($committedGB + $MIN_HEADROOM - $ramGB) * 1024 / 1024) * 1024)) -ForegroundColor DarkGray
    exit 2
}

Write-Host ''
Write-Host ('  ✓ 校验通过，余量 {0:N2} GB 高于安全线 {1:N2} GB' -f $headroomGB, $MIN_HEADROOM) -ForegroundColor Green

if ($DryRun) {
    Write-Host ''
    Write-Host '  干跑模式，未做任何修改。去掉 -DryRun 即可执行。' -ForegroundColor Yellow
    exit 0
}

# ---------- 确认 ----------
Head '确认'
Write-Host ('  即将把 C 盘页面文件设为固定 {0:N0} MB，预计释放约 {1:N2} GB 磁盘空间。' -f $SizeMB, ($pfNowGB - $SizeMB/1024.0)) -ForegroundColor White
Write-Host '  修改后必须重启电脑才会生效。' -ForegroundColor White
Write-Host ''
$ans = Read-Host '  输入 YES 继续，其它任意键取消'
if ($ans -ne 'YES') {
    Write-Host '  已取消，未做任何修改。' -ForegroundColor Yellow
    exit 0
}

# ---------- 应用 ----------
Head '执行'
try {
    $cs = Get-CimInstance Win32_ComputerSystem
    if ($cs.AutomaticManagedPagefile) {
        $cs.AutomaticManagedPagefile = $false
        Set-CimInstance -InputObject $cs
        Write-Host '  已关闭「自动管理所有驱动器的分页文件大小」' -ForegroundColor Green
    }

    $setting = Get-CimInstance Win32_PageFileSetting | Where-Object { $_.Name -like 'C:*' }
    if ($setting) {
        $setting.InitialSize = $SizeMB
        $setting.MaximumSize = $SizeMB
        Set-CimInstance -InputObject $setting
        Write-Host ('  已将 C 盘页面文件设为固定 {0:N0} MB' -f $SizeMB) -ForegroundColor Green
    } else {
        $new = New-CimInstance -ClassName Win32_PageFileSetting -Property @{
            Name = 'C:\pagefile.sys'; InitialSize = $SizeMB; MaximumSize = $SizeMB
        }
        Write-Host ('  已创建 C 盘页面文件设置，固定 {0:N0} MB' -f $SizeMB) -ForegroundColor Green
    }
} catch {
    Write-Host ('  设置失败：' + $_.Exception.Message) -ForegroundColor Red
    exit 1
}

Write-Host ''
Write-Host '  完成。请重启电脑使设置生效。' -ForegroundColor Green
Write-Host '  重启后可用本脚本的 -DryRun 复查，或直接查看 C 盘剩余空间。' -ForegroundColor DarkGray
