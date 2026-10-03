# Next.js 前端与可靠 Python 后端

## 已确认目标
用户批准 Next.js 前端、Flask + LangGraph 后端的方向，并要求完善项目、检查后提交同步、部署到固定 HTTPS 地址。保留中文眼镜与戒指体验、真实模型处理和已有用户数据。

## 架构与范围
- frontend/：Next.js App Router + TypeScript，按界面和业务功能拆成 React 组件。静态导出，无需在前端运行模型或持有模型密钥。
- langgraph-app/：保留 Flask API、LangGraph、知识检索、模型工具和原有数据库。新增持久任务管理，替换 Web 内存任务表。
- 同源部署：Docker 构建 Next.js 静态文件，由 Flask 统一提供页面与 API；运行时仍为单 Python 进程、单实例、持久数据卷。
- 可选 Vercel：仅托管 frontend；必须连接独立常驻云端 Python 后端，不允许代理到用户电脑。该拆分部署需额外配置后端地址与上传链路，不能宣称 Flask 函数可承载常驻任务。
- 暂不引入多租户、付费、真实硬件接入或将 Python AI 流程重写为 JavaScript。

## 界面与数据契约
保留深石墨绿和薄荷色。镜片是主交互与阅读区，戒指提供场景/结果导航；手机上顺序排列。页面明确标识设备模拟。
功能包含拍照/相册/粘贴、文字输入、原版/JEV选择、策论草稿、面试回答、录音预览后提交、任务进度与中断恢复、逐题结果、历史与导出、资料导入和选择。
保持既有 /api/chat/async multipart 协议及 /api/task JSON；新增 GET /api/tasks 列表及 POST /api/task/retry。任务状态 pending/running/interrupted/done/error；task_id 与 request_id 在刷新、断网、重启后保持。
新增 GET /api/session、POST /api/login（JSON token）、POST /api/logout；口令存 HttpOnly Cookie，不放 localStorage。模型密钥仅后端私密配置。

## 可靠性
任务输入、状态、结果持久化到现有 app.sqlite3 的新增表，兼容原数据；任务 API 不公开上传绝对路径。排队任务可恢复，执行中的任务在重启后标 interrupted，需显式继续，不能盲目重新提交外部转写任务。
同一 request_id 去重、同会话执行串行、有限工作线程，避免上下文互串。复用既有回执与图检查点。进度落库，重新打开页面可查。
持久卷保存 SQLite、检查点、上传和导出。发布前等待 active_tasks=0；提供备份与检查脚本，不删除旧库、不上传用户资料。

## 验收
Python 回归、新增持久任务/并发/鉴权测试；Next.js 类型检查、生产构建、前端交互测试；实际浏览器验证桌面/窄屏、登录、镜片与戒指、上传、零模型算术任务、刷新与历史、鉴权拒绝。
构建容器并验证登录、静态文件、health、受保护 API。推送源码并在具备托管连接时发布，逐项验证实际 HTTPS 地址；缺少托管连接时明确报告未上线，不将本地测试当作线上发布。
