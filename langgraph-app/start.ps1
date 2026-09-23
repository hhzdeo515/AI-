# 启动 AI 智能助手（LangGraph 版）
#
# 为什么需要这个脚本：本机 PATH 上的 `python` 是一个残缺的 shim
# （F:\Scripts\python.exe，报 "No pyvenv.cfg file"），照抄 README 里的
# `python run.py check` 会直接失败。这里按顺序探测可用的解释器。
#
# 用法：
#   .\start.ps1              起 Web 页（默认端口 8802，自动打开浏览器）
#   .\start.ps1 -Check       只做自检（配置 + 模型连通）
#   .\start.ps1 -Port 9000   换端口
#   .\start.ps1 -NoBrowser   不自动开浏览器

param(
    [switch]$Check,
    [int]$Port = 8802,
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here

# ---------------------------------------------------------------- 找解释器
# 逐个试候选解释器：能报出 Python 3 版本、且能导入项目依赖，才算可用。
# 残缺 shim（F:\Scripts\python.exe）会在任一步失败并被跳过。
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
    $candidates = @("F:\python.exe", "python.exe", "python3.exe")
    foreach ($c in $candidates) {
        if (Test-PythonCandidate -Exe $c) { return $c }
    }
    return ""
}

$py = Find-Python
if (-not $py) {
    Write-Host "[错误] 找不到可用的 Python 解释器（需要 3.9+ 且已装依赖）。" -ForegroundColor Red
    Write-Host ""
    Write-Host "请先安装依赖。本机可用的解释器是 F:\python.exe："
    Write-Host "  F:\python.exe -m pip install -r requirements.txt"
    Write-Host ""
    Write-Host "如果 python 命令报 'No pyvenv.cfg file'，说明 PATH 里第一个 python 是残缺 shim。"
    exit 1
}
Write-Host "解释器: $py" -ForegroundColor DarkGray

# ---------------------------------------------------------------- .env 检查
if (-not (Test-Path ".env")) {
    Write-Host "[提示] 未找到 .env。复制模板并填入百炼 Key：" -ForegroundColor Yellow
    Write-Host "  Copy-Item .env.example .env"
    Write-Host "  （不填也能跑：290 项测试与零 token 算术路径都不需要 Key）"
    Write-Host ""
}

# ---------------------------------------------------------------- 自检模式
if ($Check) {
    & $py run.py check
    exit $LASTEXITCODE
}

# ---------------------------------------------------------------- 起服务
if (-not $NoBrowser) {
    Start-Job -ScriptBlock {
        param($p)
        Start-Sleep -Seconds 4
        Start-Process "http://127.0.0.1:$p"
    } -ArgumentList $Port | Out-Null
}

Write-Host "启动中…… 访问 http://127.0.0.1:$Port   （Ctrl+C 停止）" -ForegroundColor Green
& $py run.py web --port $Port
