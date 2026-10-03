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

原版镜片现包含 15 语种实时翻译、高德地图接入与 IMU 模拟。`api/translate.js` 在 Vercel 上作为 Node.js 函数部署，开发服务在 Demo 模式接入同一函数；题解与会议仍走示例适配器。没有高德密钥时，仍可拖动罗盘、滑杆或用方向键模拟眼镜转向，打开高德官网；镜片内地图显示待配置提示。位置与方向传感器只在用户主动开启后获取。

Vercel production 需通过私密加密环境变量配置至少 24 字符的 `TRANSLATION_ACCESS_TOKEN`；缺少时接口返回 503。界面首次收到 401 后显示口令输入，口令只保存在当前浏览器会话中。本地 Demo 开发不配置口令也可验证；真实口令不进入源码和日志。

高德地图需另配置私密 `AMAP_JS_API_KEY` 和 `AMAP_SECURITY_JS_CODE`，并在高德控制台配置正式域名。`api/map-config.js` 仅向已认证浏览器返回 JS API Key 和同源代理地址；`api/amap-proxy.js` 为 `/_AMapService/` 白名单代理，安全密钥只在服务端使用。未配置时返回 503，手动 IMU 始终可用。实际方位感应还需要电脑提供浏览器可用的方向传感器。

## 验证

```powershell
npm run typecheck
npm test
npm run build:full
npm run build:demo
```

原版手势、摄像头、录音、模型切换与请求流程的 JavaScript 回归位于 `langgraph-app/tests/test_*.cjs`。Flask 的登录、鉴权及原首页集成回归位于 `langgraph-app/tests/`。
