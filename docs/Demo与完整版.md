# Demo 与完整版

同一套 Next.js 界面有两个独立构建目标。Vercel 当前发布 Demo；Docker 和默认本地开发运行完整版。选择发生在构建时，不能通过网址参数、按钮或接口失败自动切换。

| 项目 | Demo 演示版 | 完整版 |
| --- | --- | --- |
| 用途 | 展示眼镜、戒指和业务操作流程 | 真实处理题目、练习与会议 |
| 入口 | 打开网址直接进入工作空间，无需口令 | 后端校验访问口令 |
| 数据来源 | 预设题解、练习点评、会议样例 | Flask / LangGraph 与模型服务 |
| 处理 | 浏览器模拟进度，不发起 API 请求 | Python 持久任务与有限工作线程 |
| 图片和录音 | 仅本机预览，不上传、不持久保存 | 鉴权上传、处理、持久保存 |
| 记录 | 浏览器保存最近 50 条示例任务 | 后端持久卷，保留已有用户数据 |
| 资料库 | 本机文本、TXT / Markdown；不参与真实检索 | 后端 TXT / Markdown / DOCX 与检索 |
| 导出 | 本地生成 Markdown / TXT | 后端 Markdown / TXT / DOCX / PDF |
| 模型费用 | 不调用模型 | 取决于模型账号与用量 |

页面不再显示 Demo / 演示模式标识或重复说明，预设题目和结果标题保留“示例”名称。任意输入或上传都不会生成真实识别、转写、评分或纪要；会议的发言人修订只更新示例发言记录，不重写固定纪要要点。

## Demo 开发与发布

```sh
cd frontend
npm ci
npm run dev:demo
# 或静态构建：
npm run build:demo
```

Vercel 项目选择 `frontend` 根目录、Next.js 框架、生产分支 `master`。已提交的 `frontend/vercel.json` 固定执行 `npm run build:demo`；不配置 API 代理，不需要后端地址、访问口令或模型密钥。当前生产地址为 https://ai-vert-theta-44.vercel.app/ 。

打开首页或 `/login/` 即可直接使用眼镜与戒指工作空间。可填入示例，体验逐题结果、策论提纲与草稿点评、面试回答与追问、会议纪要和历史。摄像头/麦克风仍需浏览器权限，也可直接使用文本示例。

示例任务与文本资料保存在 `glasses.demo.data.v1`，任务恢复状态保存在 `glasses.demo.session.v1`。它们与完整版的 `glasses.session.v1` 分开。清空浏览器或换设备不会保留这些演示记录；需要清除时，可通过浏览器的网站数据管理删除当前站点数据。

Demo 没有服务端登录、业务 API 或 `/health`。这些路径返回 404 是预期行为；验证应检查页面、静态资源、交互、刷新恢复和没有业务网络请求，不能用完整版健康检查判断 Demo 失败。

## 完整版开发与发布

```sh
# 先按部署说明配置并运行 Python 后端
cd frontend
npm run dev
# 完整版静态构建，npm run build 同样默认完整版：
npm run build:full
```

`API_PROXY_TARGET` 只用于完整版开发代理，默认 `http://127.0.0.1:5000`。Dockerfile 显式执行 `build:full`，生产构建不接受外部 `NEXT_PUBLIC_APP_MODE` 将其改成 Demo。默认构建输出都在 `frontend/out`，构建另一个版本会替换该目录；启动完整版时应使用刚构建的完整版输出，Docker 构建会自行保证这一点。

完整版按 [部署说明](部署.md) 使用单进程、单实例、持久卷、HTTPS 和私密访问口令。当前尚未配置后端云端托管，Demo 上线不代表完整版已上线。

如以后单独在 Vercel 发布完整版前端，应使用独立 Vercel 项目与部署分支，在该分支用 `deploy/vercel.frontend.example.json` 替换前端配置并填入真实后端地址。不要覆盖当前 `master` 的 Demo 配置。

## 验证

```sh
cd frontend
npm test
npm run typecheck
npm run build:full
npm run build:demo
```

测试覆盖 Demo 不请求后端、刷新恢复、请求去重、练习追问、会议修订、资料增删、导出、版本数据隔离，以及完整版连接失败后仍须真实认证。完整模型效果仍需配置凭据后另行验证。
