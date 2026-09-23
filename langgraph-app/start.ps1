# 启动 AI 智能助手（LangGraph 版）
#
# 用法：
#   .\start.ps1                    起 Web 页（127.0.0.1:8802，本机免口令）
#   .\start.ps1 -LAN               允许局域网访问（需已设 ACCESS_TOKEN）
#   .\start.ps1 -LAN -AllowNoAuth  局域网访问且不要口令（需明确确认风险）
#   .\start.ps1 -Check             只做自检（配置 + 模型连通）
#   .\start.ps1 -Port 9000         换端口
#   .\start.ps1 -NoBrowser         不自动开浏览器
#
# 关于口令：
#   本机监听（127.0.0.1）默认免口令——只有这台机器能连，摩擦最低。
#   局域网监听（0.0.0.0）默认要求口令，因为同网络任何人都能用你的 API Key
#   并读写本机 data/。确实不想要口令时必须显式加 -AllowNoAuth。

param(
    [switch]$Check,
    [switch]$LAN,
    [switch]$AllowNoAuth,
    [int]$Port = 8802,
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here

# ---------------------------------------------------------------- 找解释器
# 逐个试候选解释器：能导入项目依赖才算可用（跳过残缺 shim）。
function Test-PythonCandidate {
    param([string]$Exe)
    try {
        $probe = & $Exe -c "import sys, langgraph, flask, openai; print(sys.version.split()[0])" 2>$null
        if ($LASTEXITCODE -ne 0) { return $false }
        return ($probe -match '^3\.')
    } catch {
        return $false
    }
}

function Find-Python {
    $candidates = @("python.exe", "python3.exe", "F:\python.exe")
    foreach ($c in $candidates) {
        if (Test-PythonCandidate -Exe $c) { return $c }
    }
    return ""
}

$py = Find-Python
if (-not $py) {
    Write-Host "[错误] 找不到可用的 Python 解释器（需要 3.9+ 且已装依赖）。" -ForegroundColor Red
    Write-Host ""
    Write-Host "请先安装依赖："
    Write-Host "  python -m pip install -r requirements.txt"
    exit 1
}
Write-Host "解释器: $py" -ForegroundColor DarkGray

# ---------------------------------------------------------------- .env 检查
$accessToken = ""
if (Test-Path ".env") {
    $line = Select-String -Path ".env" -Pattern '^\s*ACCESS_TOKEN=(.*)$' -ErrorAction SilentlyContinue |
            Select-Object -First 1
    if ($line) { $accessToken = $line.Matches[0].Groups[1].Value.Trim() }
} else {
    Write-Host "[提示] 未找到 .env。复制模板并填入百炼 Key：" -ForegroundColor Yellow
    Write-Host "  Copy-Item .env.example .env"
    Write-Host "  （不填也能跑：全部测试与零 token 算术路径都不需要 Key）"
    Write-Host ""
}

# ---------------------------------------------------------------- 自检模式
if ($Check) {
    & $py run.py check
    exit $LASTEXITCODE
}

# ---------------------------------------------------------------- 安全检查
$bindHost = "127.0.0.1"
if ($LAN) { $bindHost = "0.0.0.0" }

# 本机监听免口令；局域网监听必须有口令，除非显式 -AllowNoAuth。
# 这道保护防的是"手滑把没口令的服务暴露到整个 WiFi"——不是不让你这么做，
# 是要你明确说过。
if ($LAN -and -not $accessToken -and -not $AllowNoAuth) {
    Write-Host ""
    Write-Host "  " + ("!" * 60) -ForegroundColor Red
    Write-Host "  [已阻止] -LAN 会把服务暴露给同一网络，但 .env 里没有 ACCESS_TOKEN。" -ForegroundColor Red
    Write-Host "           任何能连上的人都可用你的 API Key（消耗额度），" -ForegroundColor Red
    Write-Host "           并读写本机 data/ 目录下的资料与上传文件。" -ForegroundColor Red
    Write-Host ""
    Write-Host "  两个选择：" -ForegroundColor Yellow
    Write-Host "    1) 本机用（推荐）：直接 .\start.ps1           —— 免口令，只有这台机器能连" -ForegroundColor Yellow
    Write-Host "    2) 确实要在局域网免口令：.\start.ps1 -LAN -AllowNoAuth" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "  想保留口令但换一个：" -ForegroundColor DarkGray
    Write-Host "    python -c ""import secrets,pathlib;p=pathlib.Path('.env');s=p.read_text(encoding='utf-8');p.write_text(s.replace('ACCESS_TOKEN=','ACCESS_TOKEN='+secrets.token_urlsafe(12)),encoding='utf-8')""" -ForegroundColor DarkGray
    Write-Host "  " + ("!" * 60) -ForegroundColor Red
    Write-Host ""
    exit 1
}

if ($LAN -and -not $accessToken -and $AllowNoAuth) {
    Write-Host ""
    Write-Host "  [警告] 局域网免口令模式：同一网络下任何人都能使用本服务。" -ForegroundColor Yellow
    Write-Host "         会消耗你的 DASHSCOPE 额度，并可读写本机 data/。" -ForegroundColor Yellow
    Write-Host ""
}

# ---------------------------------------------------------------- 起服务
$visit = "http://127.0.0.1:$Port"
if ($LAN) {
    $lanIp = (Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
              Where-Object { $_.IPAddress -notmatch '^(127\.|169\.254\.)' -and $_.PrefixOrigin -ne 'WellKnown' } |
              Select-Object -First 1 -ExpandProperty IPAddress)
}

if (-not $NoBrowser) {
    Start-Job -ScriptBlock {
        param($url)
        Start-Sleep -Seconds 4
        Start-Process $url
    } -ArgumentList $visit | Out-Null
}

Write-Host ""
if ($LAN) {
    Write-Host "本机访问  : $visit" -ForegroundColor Green
    if ($lanIp) {
        Write-Host "局域网访问: http://${lanIp}:$Port   （手机连同一 WiFi 打开）" -ForegroundColor Green
    }
    Write-Host "访问口令  : 已启用（打开页面后输入 .env 里 ACCESS_TOKEN 的值）" -ForegroundColor Green
} else {
    Write-Host "本机访问  : $visit   （Ctrl+C 停止）" -ForegroundColor Green
    if ($accessToken) {
        Write-Host "访问口令  : 已启用" -ForegroundColor DarkGray
    } else {
        Write-Host "访问口令  : 未启用（仅本机监听，风险可控）" -ForegroundColor DarkGray
    }
}
Write-Host ""

if ($LAN) {
    & $py run.py web --host $bindHost --port $Port
} else {
    & $py run.py web --port $Port
}
