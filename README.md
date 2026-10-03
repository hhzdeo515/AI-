# AI 眼镜智能助手

基于 **LangGraph + Flask** 的 AI 智能眼镜演示项目，提供会议纪要、拍照解题，以及电脑端「眼镜 + 戒指」交互。当前应用位于 [langgraph-app/](langgraph-app/)。

## 当前功能

- **拍照解题**：识别单题、多题及共用材料，结合资料检索、计算工具和原图复核输出答案；支持原版与 JEV 决策流程切换。
- **写作与面试练习**：拍照入口可分流至职业能力测试、策论和面试；支持用户草稿批改、实际回答点评及追问。
- **会议纪要**：浏览器录音或导入音频，经转写、纪要生成和原文核验后归档；任务与检查点持久化。
- **资料与历史**：导入 TXT、Markdown、DOCX 参考资料，管理题解、会议记录和导出文件。
- **硬件演示**：在镜片内选择场景、阅读结果，通过虚拟戒指、键盘或触控操作。

眼镜、戒指与传输链路为浏览器模拟；摄像头、麦克风采集以及配置后的模型请求使用真实服务。真实设备接入尚未实现。

## 快速开始

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
langgraph-app/
  lg_assistant/       LangGraph 编排、场景处理、Web 界面和工具
    knowledge/       内置知识摘要及来源清单
  tests/             Python 回归测试和 Node.js 前端测试
  evaluation/        离线评分、评测存储和事件口径校验
  scripts/           知识库生成、事件用例生成与显式模型验证
  .env.example       配置模板
  requirements.txt   运行依赖
docs/                功能说明和指标设计
```

运行数据默认写入 `langgraph-app/data/`。密钥、上传文件、数据库、评测结果、模型文件、第三方安装包、个人资料及旧版原型均不纳入当前版本。

## 验证

在 `langgraph-app` 目录执行：

```powershell
python -m pip install pytest
python -m pytest tests -q
Get-ChildItem tests/test_*.cjs | ForEach-Object {
    node $_.FullName
    if ($LASTEXITCODE -ne 0) { throw "前端测试失败：$($_.Name)" }
}
```

这些回归测试使用临时数据与模拟模型响应，不需要 API Key。前端测试仅使用 Node.js 内置模块，无需安装 npm 依赖。真实模型验证需显式运行带 `--live` 的脚本，详见[应用说明](langgraph-app/README.md)。

## 文档与边界

- [应用配置与运行](langgraph-app/README.md)
- [硬件协同演示](docs/硬件协同演示.md)
- [拍照解题实现与使用](docs/拍照解题Agent_实现与使用.md)
- [指标体系与埋点方案](docs/拍照解题指标体系与埋点方案.md)
- [产品范围](langgraph-app/PRODUCT.md)与[界面设计](langgraph-app/DESIGN.md)

当前仅提供单一访问口令，没有独立账号或多租户鉴权；局域网访问需配置 `ACCESS_TOKEN`。离线测试通过不代表真实题目准确率或设备端验收通过，解题与会议结果仍需用户核验。指标事件已有离线校验器，尚未完整接入应用采集。
