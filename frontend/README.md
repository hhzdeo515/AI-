# AI 眼镜 Next.js 前端

保留中文镜片 / 戒指体验。App Router 页面静态导出，由 Python 提供同源业务接口。

## 本地开发

```powershell
npm.cmd ci
# Python 服务监听地址；只用于 next dev 的同源代理。
$env:API_PROXY_TARGET = 'http://127.0.0.1:5000'
npm.cmd run dev
```

浏览器访问 Next.js 开发地址。所有 `/api/*`、`/health` 与 `/legacy` 请求代理到 Python；生产构建不包含该开发代理。

## 检查与构建

```powershell
npm.cmd test
npm.cmd run typecheck
npm.cmd run build
```

输出为 `frontend/out`。用仓库根 Dockerfile 构建时自动复制到 Python 容器。独立运行 Python 时，将 `FRONTEND_DIR` 指向该目录。静态托管必须将 `/api/*`、`/health`、`/legacy` 和兼容页面静态资源路由到常驻 Python 后端，不能只发布静态页面就宣称完整应用已上线。详见根目录部署文档。

## 模块

- `components/studio.tsx`：场景、附件与学习操作；镜片、戒指、结果、资料库、历史各自独立组件。
- `hooks/use-task.ts`：提交、显式继续、恢复原任务；断网不重发 POST。
- `lib/api.ts`：同源 Cookie 请求、超时与错误处理、multipart 契约。
- `lib/tasks.ts`：可测试的轮询与本地任务标识管理。
- `lib/media.ts`：权限请求代际隔离，避免窗口关闭后摄像头被迟到的授权重新打开。
- `tests/`：提交幂等保护、刷新恢复、鉴权错误、HTML 安全渲染、媒体释放、停止后试听再发送。

## 数据与权限

访问口令只用于 `/api/login`，会话使用 HttpOnly Cookie。本地存储仅保存会话、任务与请求标识（包括只读历史任务标识），不保存口令、录音、照片、题目或草稿。任务与原始媒体从鉴权接口恢复。相机与麦克风需要 HTTPS（或 localhost），隐藏页面、关闭窗口、组件卸载均释放设备。

从历史列表打开的任务不允许创建新的后续作答，刷新后仍保留该限制；开始新任务才会生成新的会话。这避免把旧面试题的回答接到已推进的最新会话。历史中断 / 失败任务仍可显式继续原任务，由后端校验检查点安全性。当前正在处理的工作直接刷新页面可正常恢复和继续。

会议发言人支持改名与逐段归属修订。高级拆分、合并与原有辅助功能保留在 `/legacy` 兼容界面。输出使用禁用原始 HTML 的 Markdown 渲染。
