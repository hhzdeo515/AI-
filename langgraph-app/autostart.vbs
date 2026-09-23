' 开机静默启动 AI 智能助手
'
' 放进启动文件夹即可：
'   Win+R 输入 shell:startup -> 把本文件复制进去
'
' 本文件只做一件事：隐藏窗口地调用 autostart.ps1。
' 真正的逻辑在那个 .ps1 里（PowerShell 写起来比 VBScript 清楚得多，
' 且 VBScript 里嵌带引号的命令极容易踩“缺少语句”这类语法错）。
'
' 看状态 / 停止服务：langgraph-app\serve.ps1 -Status / -Stop

Option Explicit

Dim here, fso, shell

here = "E:\AI智能助手\langgraph-app"
Set fso = CreateObject("Scripting.FileSystemObject")

If Not fso.FolderExists(here) Then WScript.Quit 1
If Not fso.FileExists(here & "\autostart.ps1") Then WScript.Quit 1

Set shell = CreateObject("WScript.Shell")

' 0 = 隐藏窗口，False = 不等待（开机时不能阻塞）
shell.Run "powershell -NoProfile -ExecutionPolicy Bypass -File """ & here & "\autostart.ps1""", 0, False
