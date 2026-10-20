# 智能眼镜模型

原创建模，直接用于助手原有眼镜视图。镜框、镜腿、铰链、鼻托、曲面镜片、摄像头、指示灯和扬声器开孔均为实际网格；不依赖外部模型站。

- `smart-glasses.blend`：Blender 4.5 LTS 源文件，包含分件模型、材质和产品摄影灯光。
- `smart-glasses-preview.png`：Blender Cycles 渲染预览。
- `build_glasses.py`：可重建源文件与 GLB 的参数化建模脚本。
- 网页模型：`langgraph-app/lg_assistant/web/static/models/smart-glasses.glb`，约 1.9 MB，使用米为单位。

重建命令：

```powershell
blender --background --python assets/glasses/build_glasses.py
cd frontend
npm run build:demo
```

Blender 仅用于修改或重新生成模型，访问线上页面不需要安装 Blender。`.blend` 中的 Studio 集合不导出到 GLB。

浏览器通过 Three.js 0.180.0 加载同源 GLB，依赖已经打包到 `static/smart-glasses.js`，不会运行时从 CDN 下载脚本。渲染器源码是 `frontend/lib/smart-glasses.js`；完整许可证保留在 `static/models/THREE-LICENSE.txt`。Flask 直接使用提交的 bundle，前端构建会重新生成它。

交互镜片继续使用原来的 HTML 控件与摄像头视频。WebGL 模型与镜片共享旋转、缩放和 1600px 透视参数；进入“放大镜片”时两者共同定位。模型加载失败或 WebGL 上下文丢失时恢复原兼容视图。隐藏页面暂停绘制；没有操作时不持续运行渲染循环。

这里的摄像头、指示灯、充电触点是产品外观模型，不表示已连接真实眼镜硬件。原助手的摄像头权限、题解、会议、翻译与地图能力边界不变。
