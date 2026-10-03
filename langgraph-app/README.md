# Flask / LangGraph 智能眼镜助手后端

本目录提供会议纪要、拍照解题、策论与面试练习的 Python 后端。新版 Next.js 界面位于仓库根目录 `frontend/`；前后端开发和构建方法见[架构与开发](../docs/架构与开发.md)。本目录仍保留可独立运行的兼容界面，用于原有功能回归。

## 安装与启动

建议 Python 3.11，在本目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
python run.py web --port 8802
```

先在 `.env` 填写模型凭据，再使用真实解题或转写。默认地址为 <http://127.0.0.1:8802>。

Windows 启动脚本：

| 命令 | 用途 |
|---|---|
| `./start.ps1` | 前台启动，Ctrl+C 停止 |
| `./start.ps1 -Check` | 配置与模型连通性自检；配置密钥后会调用模型 |
| `./serve.ps1` | 后台启动；已有服务会先停止再启动 |
| `./serve.ps1 -Status` | 查看运行状态与日志 |
| `./serve.ps1 -Stop` | 停止服务 |
| `./start.ps1 -LAN` | 允许局域网访问，需设置 `ACCESS_TOKEN` |

## 配置

以 `.env.example` 为准。密钥只放在本地 `.env`，不要提交到仓库。

| 配置项 | 作用 |
|---|---|
| `DASHSCOPE_API_KEY` | 百炼模型服务凭据 |
| `MODEL_TEXT`、`MODEL_VISION` | 文字与视觉模型 |
| `EXAM_MODEL`、`EXAM_INDEPENDENT_MODEL`、`EXAM_REVIEW_MODEL` | 解题、独立解答及复核模型 |
| `MODEL_ASR`、`MODEL_TTS` | 语音识别与播报模型 |
| `TYPESAFE_API_KEY`、`JEV_MODEL` | 可选 JEV 决策服务 |
| `ACCESS_TOKEN` | Web 访问口令；默认本机使用 |
| `FFMPEG_PATH` | 可选 ffmpeg 可执行文件绝对路径 |
| `DATA_DIR` | 可选运行数据目录，默认本目录下的 `data/` |

模型名称需与服务账号可用模型匹配。原版与 JEV 在页面中切换，共用本应用的资料、上传文件及历史。未配置 JEV 凭据时使用原版流程即可。

会议录音转换和长音频处理依赖 ffmpeg：可加入 PATH，或设置 `FFMPEG_PATH`。外部安装包不随仓库分发。

默认 `EXEC_BACKEND=python`，由应用内部场景处理器执行。代码保留了外部 Dify 接口适配器；如自行配置 `EXEC_BACKEND=dify`，还需单独部署兼容工作流并填写服务地址和密钥，旧版 Dify 工程不包含在当前仓库中。

## 代码结构

- `graph.py`、`routing.py`、`nodes.py`：设备事件、场景路由、状态图及结果处理。
- `exam_graph.py`、`jev_exam_graph.py`、`exam_batch.py`：解题子图、JEV 决策与多题处理。
- `photo_practice.py`、`practice_agents.py`：题型分流、策论与面试练习。
- `meeting_graph.py`、`meeting_jobs.py`、`transcribe.py`：会议转写、纪要和恢复任务。
- `exam_knowledge.py`、`public_knowledge.py`、`knowledge/`：用户资料与内置知识检索。
- `store.py`、`state.py`、`progress.py`、`jobs.py`：SQLite 存储、检查点状态、持久任务与处理进度。
- `web/`：Flask 接口、访问鉴权、Next.js 静态资源和兼容界面。
- `tools/`、`ported/`：音频、导出、算术、图形及播报工具。

以上路径相对于 `lg_assistant/`。运行数据库、上传素材、导出文件与日志均由应用在本地生成，不纳入版本管理。

## 测试与可复跑脚本

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest tests -q
Get-ChildItem tests/test_*.cjs | ForEach-Object {
    node $_.FullName
    if ($LASTEXITCODE -ne 0) { throw "前端测试失败：$($_.Name)" }
}
```

Python 回归使用临时数据库和模拟模型响应；前端测试使用 Node.js 内置模块。无需旧版代码、真实用户数据或模型凭据。

| 脚本 | 用途 |
|---|---|
| `python scripts/build_exam_knowledge.py` | 根据脚本内的知识摘要与来源重新生成内置知识文件 |
| `python scripts/build_north_star_event_cases.py --verify` | 生成并校验合成指标事件用例 |
| `python scripts/evaluate_exam.py --live` | 调用真实模型验证三个合成样例，使用独立数据库 |
| `python scripts/validate_exam_batch_live.py --case mixed --live --backend original` | 对已启动服务验证多题照片；可选 `shared`、`many` 及 `jev` |

`--live` 会调用配置的模型服务并产生正常费用。图片生成脚本默认使用 Windows 微软雅黑；`evaluate_exam.py` 可通过 `--font` 指定其他中文字体。脚本输出保存在仓库根目录 `outputs/`，不会上传 GitHub。这些样例用于流程验证，不代表真实准确率。

## 使用说明

- [硬件协同与戒指操作](../docs/硬件协同演示.md)
- [指标口径与采集边界](../docs/拍照解题指标体系与埋点方案.md)
- [拍照解题实现](../docs/拍照解题Agent_实现与使用.md)

会议页的录音按钮控制真实麦克风；停止后可试听、下载或显式提交转写。离开录音页面或切到后台会释放麦克风。策论批改需提交用户草稿，面试点评需提交实际文字或录音回答。硬件传输仍是模拟，真实设备接入与验收尚未完成。
