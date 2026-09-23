# 后台启动 AI 智能助手，并脱离当前会话（关掉这个终端也继续跑）
#
# 与 start.ps1 的区别：
#   start.ps1  前台运行，Ctrl+C 即停，适合"我现在就要用一会儿"
#   serve.ps1  后台脱离运行，关掉终端仍然存活，适合"让它一直开着"
#
# 用法：
#   .\serve.ps1              启动（已在跑则先停掉再启动）
#   .\serve.ps1 -LAN         允许局域网访问（需已设 ACCESS_TOKEN，或用 -AllowNoAuth）
#   .\serve.ps1 -Status      查看状态与日志尾部
#   .\serve.ps1 -Stop        停止服务
#
# 日志：data\server.log    PID 文件：data\server.pid

param(
    [switch]$LAN,
    [switch]$AllowNoAuth,
    [switch]$Stop,
    [switch]$Status,
    [int]$Port = 8802
)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here

$dataDir = Join-Path $here "data"
New-Item -ItemType Directory -Force -Path $dataDir | Out-Null
$pidPath = Join-Path $dataDir "server.pid"

function Get-RunningPid {
    # **端口才是权威**，PID 文件只是辅助。
    # 实测踩过：服务是用别的方式起的（没有 PID 文件）时，-Status 报“未运行”，
    # 但服务其实活得好好的——只信 PID 文件会给出错误结论。
    $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if ($conn) { return $conn[0].OwningProcess }

    if (-not (Test-Path $pidPath)) { return $null }
    $saved = (Get-Content $pidPath -ErrorAction SilentlyContinue | Select-Object -First 1)
    if (-not $saved) { return $null }
    $proc = Get-Process -Id ([int]$saved) -ErrorAction SilentlyContinue
    if ($proc) { return $proc.Id }
    return $null
}

function Stop-Server {
    $running = Get-RunningPid
    if ($running) {
        Stop-Process -Id $running -Force -ErrorAction SilentlyContinue
        Write-Host "已停止服务 (PID $running)" -ForegroundColor Yellow
    } else {
        Write-Host "服务未在运行" -ForegroundColor DarkGray
    }
    Remove-Item $pidPath -Force -ErrorAction SilentlyContinue
    # 兜底：按端口清理残留
    $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if ($conn) {
        Stop-Process -Id $conn.OwningProcess -Force -ErrorAction SilentlyContinue
        Write-Host "已清理占用 $Port 的残留进程" -ForegroundColor Yellow
    }
}

if ($Stop) { Stop-Server; exit 0 }

if ($Status) {
    $running = Get-RunningPid
    if ($running) {
        Write-Host "运行中：PID $running，端口 $Port" -ForegroundColor Green
        try {
            $h = Invoke-RestMethod "http://127.0.0.1:$Port/health" -TimeoutSec 8
            Write-Host "健康检查：ok=$($h.ok) engine=$($h.engine) auth=$($h.auth_enabled)"
        } catch {
            Write-Host "健康检查失败：$($_.Exception.Message)" -ForegroundColor Yellow
        }
    } else {
        Write-Host "未运行" -ForegroundColor Yellow
        Write-Host "启动：.\serve.ps1" -ForegroundColor DarkGray
    }
    exit 0
}

# ---------------------------------------------------------------- 口令检查
$accessToken = ""
if (Test-Path ".env") {
    $line = Select-String -Path ".env" -Pattern '^\s*ACCESS_TOKEN=(.*)$' -ErrorAction SilentlyContinue |
            Select-Object -First 1
    if ($line) { $accessToken = $line.Matches[0].Groups[1].Value.Trim() }
}

$bindHost = "127.0.0.1"
if ($LAN) { $bindHost = "0.0.0.0" }

if ($LAN -and -not $accessToken -and -not $AllowNoAuth) {
    Write-Host "[已阻止] -LAN 会把服务暴露给同一网络，但 .env 里没有 ACCESS_TOKEN。" -ForegroundColor Red
    Write-Host "  本机用：.\serve.ps1" -ForegroundColor Yellow
    Write-Host "  局域网免口令：.\serve.ps1 -LAN -AllowNoAuth" -ForegroundColor Yellow
    exit 1
}

# ---------------------------------------------------------------- 重启
Stop-Server | Out-Null
Start-Sleep -Milliseconds 400

# 先确认 python 可用，否则 Start-Process 会甩一个难懂的 Win32 异常
$pyCheck = & python -c "import langgraph, flask, openai; print('ok')" 2>$null
if ($LASTEXITCODE -ne 0 -or "$pyCheck" -notmatch 'ok') {
    Write-Host "[错误] python 不可用或缺少依赖。" -ForegroundColor Red
    Write-Host "  确认：python run.py check" -ForegroundColor Yellow
    Write-Host "  装依赖：python -m pip install -r requirements.txt" -ForegroundColor Yellow
    exit 1
}

# 用 Start-Process 让子进程独立于本会话；关掉终端也继续跑。
#
# 注意：**不要用 -RedirectStandardOutput/-RedirectStandardError**。
# 实测加了那两个参数后本脚本会阻塞不返回（等 300 秒被强杀），进程也起不来。
# 服务日志不在这里看——需要时用 .\serve.ps1 -Status。
$proc = Start-Process -FilePath "python" `
                      -ArgumentList @("run.py", "web", "--host", $bindHost, "--port", "$Port") `
                      -WorkingDirectory $here `
                      -WindowStyle Hidden `
                      -PassThru

$proc.Id | Set-Content $pidPath -Encoding ascii

# 等它起来并确认真的在监听，而不是"启动了但立刻挂了"
$ok = $false
for ($i = 0; $i -lt 20; $i++) {
    Start-Sleep -Milliseconds 700
    $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if ($conn) { $ok = $true; break }
    if ($proc.HasExited) { break }
}

Write-Host ""
if ($ok) {
    $lanIp = (Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
              Where-Object { $_.IPAddress -notmatch '^(127\.|169\.254\.)' -and
                             $_.InterfaceAlias -notmatch 'VPN|WSL|Loopback' } |
              Select-Object -First 1 -ExpandProperty IPAddress)
    Write-Host "服务已启动 (PID $($proc.Id))" -ForegroundColor Green
    Write-Host "  本机访问 : http://127.0.0.1:$Port"
    if ($LAN) {
        if ($lanIp) { Write-Host "  局域网访问: http://${lanIp}:$Port" }
        Write-Host "  访问口令 : $(if ($accessToken) { '已启用' } else { '未启用（-AllowNoAuth）' })"
    }
    Write-Host "  停止     : .\serve.ps1 -Stop" -ForegroundColor DarkGray
    Write-Host ""
    Write-Host "关掉这个终端不影响服务。" -ForegroundColor DarkGray
} else {
    Write-Host "服务启动失败（端口 $Port 未被监听）。" -ForegroundColor Red
    Write-Host "排查：" -ForegroundColor Yellow
    Write-Host "  1) 端口是否被别的程序占用：Get-NetTCPConnection -LocalPort $Port"
    Write-Host "  2) 手动前台启动看报错   ：python run.py web --port $Port"
    exit 1
}
