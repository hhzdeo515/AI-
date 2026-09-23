# 静默启动器：给 autostart.vbs 用（也可手动跑，但手动请用 serve.ps1）
#
# 职责单一：若 8802 没在监听，就后台把服务起起来。
# 不打开浏览器、不打印提示——开机自启不需要这些。

$here = "E:\AI智能助手\langgraph-app"

if (-not (Test-Path $here)) { exit 1 }

# 已在跑就不重复启动，避免多个实例抢同一个端口
$existing = Get-NetTCPConnection -LocalPort 8802 -State Listen -ErrorAction SilentlyContinue
if ($existing) { exit 0 }

Start-Process -FilePath "python" `
              -ArgumentList @("run.py", "web", "--port", "8802") `
              -WorkingDirectory $here `
              -WindowStyle Hidden

exit 0
