# assistant-lite

面向**三个场景**的**轻量级 multi-agent** 智能助手：会议纪要 / 拍照解题 / 锻炼指导。

纯 Python 实现，**不依赖 Dify、不依赖 Docker**，启动一条命令。

---

## 当前阶段 / 下一步

> **这一节是跨上下文的接续锚点。每次中断（上下文将满、会话结束）前必须更新。**

- **当前阶段**：`P5 Web 页与 ASR` — 已完成
- **状态**：P0–P5 完成；下一步 P6
- **已完成**：
  - **P0**：`config.py` / `schemas.py` / `llm.py` / `agents/base.py` / `run.py` CLI / 根 `.gitignore`
  - **P1**：`session.py`、`orchestrator.py`（五级路由）、`agents/meeting/`、`agents/general.py`
  - **P2**：`agents/exam/`（五步链）、`tools/grids.py`、`tools/calc.py`
  - **P3**：`agents/fitness/profile.py`（建档问卷）、`equipment.py`（15 项器械知识库）
  - **P4**：`agents/fitness/state.py`（训练状态机）、`agent.py` 接入四个训练事件
  - **P5**：`web/app.py`（Flask：`/`、`/health`、`/api/chat`、`/api/resources`、`/api/state`）、
    `web/templates/index.html`（深色单页：场景切换 / 附件上传 / 对话历史 / 状态徽标）；
    `llm.asr()` 走 dashscope 的 `qwen3-asr-flash`，供会议录音转写
- **测试**（共 72 项，全部不需要 API Key）：
  ```
  python tests/test_meeting.py     -> 13/13
  python tests/test_exam_tools.py  -> 11/11
  python tests/test_fitness.py     -> 18/18
  python tests/test_workout.py     -> 20/20
  python tests/test_web.py         -> 10/10
  ```
- **实测 CLI / Web**：
  ```
  # CLI
  run.py ask "开始卧推 3组10次 60公斤"                      -> start_exercise
  run.py ask "做完一组"                                     -> set_done  「卧推」第 1 组已记录（10 次）
  run.py ask "膝盖有点疼" -e '{"semantic_action":"pain_report"}' -> 已暂停 + 停练/就医建议
  run.py ask "结束训练" -e '{"semantic_action":"end_workout"}'   -> 事实清单 + 归档「训练总结」

  # Web（真实起服务 + curl 验收）
  python run.py web --port 8801
  curl http://127.0.0.1:8801/health
  curl -X POST http://127.0.0.1:8801/api/chat -F "text=开始会议记录" -F "session_id=c1"
  curl -X POST ... -F "files=@t.png"    -> 落盘为 uuid 名；bad.sh 被拦且不落盘
  ```
  > 路由顺序：事件映射 → 显式场景 → **关键词** → **粘性场景**（会议 collecting / 建档 awaiting / 训练 active）→ LLM 兜底。
  > 附件只允许图片与音频扩展名，一律重命名为 uuid 存 `data/uploads/`，非法格式跳过而不整单失败。
- **下一步**：
  1. **填入 `DASHSCOPE_API_KEY`**，跑 `python run.py check`。
     **仍是唯一未验证项**——所有需要真实模型的路径（会议纪要生成、拍题五步链、器械个性化润色、
     训练总结、ASR 转写）目前都只跑过降级路径。
  2. **P6 导出与收尾**：`tools/export.py` 参考旧 `service.py` 的 `export_resources`
     实现 docx/pdf 导出（旧项目用 `python-docx` / `reportlab` + 中文字体）；
     补 `requirements.txt` 里的导出依赖；整体回归与 README 收尾。

---

## 快速开始

```bash
# 1. 装依赖（用项目 venv）
pip install -r requirements.txt

# 2. 配置密钥
cp .env.example .env      # 然后编辑 .env，填入 DASHSCOPE_API_KEY

# 3. 自检
python run.py check

# 4. 对话
python run.py chat
python run.py ask "帮我总结这场会议"
python run.py ask "解这道题" -f question.png
```

## 架构要点

**核心思路**：把"问答型"和"主动提醒型"统一成同一条事件流——
`event → orchestrator → agent → Reply`。区别只在 Agent 内部有没有状态机。
这样"会议总结"和"第3组做完了"走的是同一条路，不需要为持续监测写第二套机制。

**编排**：规则优先（事件映射表 → 关键词 → LLM 兜底），尽量零 token 完成路由。

**契约**：全项目只有 `Task`（进）和 `Reply`（出）两个数据结构，见 `assistant_lite/schemas.py`。

## 目录结构

```
assistant-lite/
  run.py                 CLI 入口
  requirements.txt
  .env.example           配置模板（.env 不入库）
  assistant_lite/
    config.py            配置入口
    llm.py               模型调用（文本/视觉/语音）
    schemas.py           Task / Reply 契约
    session.py           会话状态存储（P1）
    orchestrator.py      路由与分发（P1）
    agents/
      base.py            Agent 基类
      meeting/           会议纪要（P1）
      exam/              拍照解题（P2）
      fitness/           锻炼指导（P3/P4）
    tools/               纯函数工具：grids / calc / export
    web/                 本地 Web 页（P5）
  data/                  运行时数据（不入库）
  tests/
```

## 分阶段计划

| 阶段 | 内容 | 状态 |
| --- | --- | --- |
| P0 | 骨架、config、llm、schemas、CLI、git init | 进行中 |
| P1 | session + 会议纪要场景 | 待做 |
| P2 | 拍照解题场景 + grids/calc 工具 | 待做 |
| P3 | 锻炼-健康档案与器械识别 | 待做 |
| P4 | 锻炼-训练状态机与事件提醒 | 待做 |
| P5 | Web 页与 ASR | 待做 |
| P6 | 导出（docx/pdf）与收尾 | 待做 |

## 环境

- Python 3.13
- 模型：阿里云百炼 OpenAI 兼容接口
  - 文本 `qwen-plus`
  - 视觉 `qwen-vl-max`
  - 语音识别 `qwen3-asr-flash`

## 与旧项目的关系

`../dify-assistant/` 是上一版基于 Dify Chatflow 的实现（可运行，但依赖 Docker + Dify 容器常驻）。
本项目复用它的**提示词与业务规则**（`exam_prompts.py` 的公考提示词、`exam_tools.py` 的 `check_grids()`、
`service.py` 的会议状态机规则），但**编排层全部重写**为纯代码。
导航场景与 Dify 知识库同步逻辑已弃用。
