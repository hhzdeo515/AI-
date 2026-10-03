# 原版界面发布工具

部署页面直接使用 `langgraph-app/lg_assistant/web/templates/index.html` 与原版 CSS / JavaScript，恢复镜片布局和 3D 戒指。历史 Next.js 组件仍保留在源码中，但不进入当前构建和发布。

## 开发与构建

```powershell
npm ci
npm run dev:demo
# 完整版需先启动 Python 服务，可用 API_PROXY_TARGET 指定后端。
npm run dev
npm run build:full
npm run build:demo
```

输出目录为 `frontend/out`。两个构建目标互相替换输出。完整版不注入示例适配器，访问口令、用户数据和任务仍由 Flask 负责；Demo 的 `legacy-demo.js` 在浏览器内复用现有示例库，不上传图片或录音，不发起模型请求。

Vercel 使用 `frontend/vercel.json` 的 Other / `out` 配置，允许读取根目录外的原版模板。完整云端后端尚未配置；当前线上发布仍为 Demo。详见 [Demo 与完整版](../docs/Demo与完整版.md) 和 [部署说明](../docs/部署.md)。

## 验证

```powershell
npm run typecheck
npm test
npm run build:full
npm run build:demo
```

原版手势、摄像头、录音、模型切换与请求流程的 JavaScript 回归位于 `langgraph-app/tests/test_*.cjs`。Flask 的登录、鉴权及原首页集成回归位于 `langgraph-app/tests/`。
