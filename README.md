# AI 眼镜智能助手

基于 **Next.js + Flask + LangGraph** 的 AI 智能眼镜助手，提供会议纪要、拍照解题，以及电脑端「眼镜 + 戒指」交互。React 前端位于 [frontend/](frontend/)，Python AI 后端位于 [langgraph-app/](langgraph-app/)。

## 两个版本

- **[Vercel Demo 演示版](https://ai-vert-theta-44.vercel.app/)**：无需登录、不连接后端、不调用模型；体验眼镜、戒指、拍照、练习和会议流程，结果均为预设示例，历史仅保存在本机浏览器。
- **完整版（Next.js + Flask + LangGraph）**：真实访问口令、AI 处理、任务恢复与持久数据。Docker 和默认本地开发使用此版本；后端云端托管尚未配置。

使用原版眼镜与 3D 戒指界面、独立构建，详见 [Demo 与完整版](docs/Demo与完整版.md)。Vercel 配置固定构建 Demo，不会影响 Docker 完整版。

## 完整版功能

- **拍照解题**：识别单题、多题及共用材料，结合资料检索、计算工具和原图复核输出答案；支持原版与 JEV 决策流程切换。
- **写作与面试练习**：拍照入口可分流至职业能力测试、策论和面试；支持用户草稿批改、实际回答点评及追问。
- **会议纪要**：浏览器录音或导入音频，经转写、纪要生成和原文核验后归档；任务与检查点持久化。
- **资料与历史**：导入 TXT、Markdown、DOCX 参考资料，管理题解、会议记录和导出文件。
- **硬件演示**：在镜片内选择场景、阅读结果，通过虚拟戒指、键盘或触控操作。

眼镜、戒指与传输链路为浏览器模拟；摄像头、麦克风采集以及配置后的模型请求使用真实服务。真实设备接入尚未实现。

## 完整版云端后端（可选，付费模板）

[打开 Render 部署配置](https://render.com/deploy?repo=https%3A%2F%2Fgithub.com%2Fhhzdeo515%2FAI-)

此入口读取根目录 [render.yaml](render.yaml)，先展示配置供账号持有人确认。配置使用一个付费实例和 5 GB 持久磁盘，费用以 Render 页面为准。登录口令由平台私密生成；只需在 Render 环境变量中填写百炼 DASHSCOPE_API_KEY，密钥不要提交到 GitHub。

当前 Vercel 站点发布 Demo，无需使用此付费模板。完整版可通过 Docker 同源部署；如使用独立 Vercel 前端项目，还需接入后端 HTTPS 地址才能登录或调用 AI。具体步骤见[完整应用部署](docs/部署.md)。

## 快速开始

新前端开发与构建见[架构与开发](docs/架构与开发.md)。生产镜像自动构建 Next.js 并与 Python API 同源运行；下面命令单独启动后端，未配置 FRONTEND_DIR 时提供兼容界面。

建议使用 Python 3.11。以下命令在仓库根目录执行：

```powershell
cd langgraph-app
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
# 编辑 .env，填写 DASHSCOPE_API_KEY；使用 JEV 时另填 TYPESAFE_API_KEY。
python run.py web --port 8802
```

打开 <http://127.0.0.1:8802>。Windows 也可在激活环境后运行 `./start.ps1`；后台运行方式见[应用说明](langgraph-app/README.md)。

会议音频转换需要安装 ffmpeg 并加入 PATH，或在 `.env` 中设置 `FFMPEG_PATH`。模型名称可在 `.env` 调整为账号可用的模型。真实模型调用需配置相应服务凭据。

## 项目结构

```text
frontend/             Next.js 页面、React 组件、媒体与任务状态、前端测试
langgraph-app/
  lg_assistant/       LangGraph 编排、场景处理、Web 界面和工具
    knowledge/       内置知识摘要及来源清单
  tests/             Python 回归测试和 Node.js 前端测试
  evaluation/        离线评分、评测存储和事件口径校验
  scripts/           知识库生成、事件用例生成与显式模型验证
  .env.example       配置模板
  requirements.txt   运行依赖
docs/                功能说明和指标设计
deploy/              托管配置、线上验证、数据备份
render.yaml          Render 后端一键部署入口
```

运行数据默认写入 `langgraph-app/data/`。密钥、上传文件、数据库、评测结果、模型文件、第三方安装包、个人资料及旧版原型均不纳入当前版本。

## 验证

在 `langgraph-app` 目录执行：

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest tests -q
Get-ChildItem tests/test_*.cjs | ForEach-Object {
    node $_.FullName
    if ($LASTEXITCODE -ne 0) { throw "前端测试失败：$($_.Name)" }
}
```

这些回归测试使用临时数据与模拟模型响应，不需要 API Key。前端测试仅使用 Node.js 内置模块，无需安装 npm 依赖。真实模型验证需显式运行带 `--live` 的脚本，详见[应用说明](langgraph-app/README.md)。

## 文档与边界

- [应用配置与运行](langgraph-app/README.md)
- [架构与前端开发](docs/架构与开发.md)
- [完整应用部署](docs/部署.md)
- [硬件协同演示](docs/硬件协同演示.md)
- [拍照解题实现与使用](docs/拍照解题Agent_实现与使用.md)
- [指标体系与埋点方案](docs/拍照解题指标体系与埋点方案.md)
- [产品范围](langgraph-app/PRODUCT.md)与[界面设计](langgraph-app/DESIGN.md)

当前仅提供单一访问口令，没有独立账号或多租户鉴权；局域网访问需配置 `ACCESS_TOKEN`。离线测试通过不代表真实题目准确率或设备端验收通过，解题与会议结果仍需用户核验。指标事件已有离线校验器，尚未完整接入应用采集。
