# assistant-lite

面向**三个场景**的**轻量级 multi-agent** 智能助手：会议纪要 / 拍照解题 / 锻炼指导。

纯 Python 实现，**不依赖 Dify、不依赖 Docker**，启动一条命令。

---

## 当前阶段 / 下一步

> **这一节是跨上下文的接续锚点。每次中断（上下文将满、会话结束）前必须更新。**

- **当前阶段**：`P4 锻炼-训练状态机与事件提醒` — 已完成
- **状态**：P0–P4 完成；下一步 P5
- **已完成**：
  - **P0**：`config.py` / `schemas.py` / `llm.py` / `agents/base.py` / `run.py` CLI / 根 `.gitignore`
  - **P1**：`session.py`、`orchestrator.py`（五级路由）、`agents/meeting/`、`agents/general.py`
  - **P2**：`agents/exam/`（五步链）、`tools/grids.py`、`tools/calc.py`
  - **P3**：`agents/fitness/`——`profile.py`（建档问卷）、`equipment.py`（15 项器械知识库）
  - **P4**：`agents/fitness/state.py`（训练状态机 + 规则引擎）、`agent.py` 接入四个训练事件、
    `prompts.py` 增加 `WORKOUT_SUMMARY`；`run.py` 支持 `--event` 结构化事件
- **测试**（共 62 项，全部不需要 API Key）：
  ```
  python tests/test_meeting.py     -> 13/13
  python tests/test_exam_tools.py  -> 11/11
  python tests/test_fitness.py     -> 18/18
  python tests/test_workout.py     -> 20/20
  ```
- **实测 CLI（完整训练流程）**：
  ```
  run.py ask "开始卧推 3组10次 60公斤"                      -> start_exercise
  run.py ask "做完一组"                                     -> set_done  「卧推」第 1 组已记录（10 次）
  run.py ask "膝盖有点疼" -e '{"semantic_action":"pain_report"}' -> 已暂停 + 停练/就医建议
  run.py ask "结束训练" -e '{"semantic_action":"end_workout"}'   -> 事实清单 + 归档「训练总结」
  ```
  > 训练规则：组间休息 60–90 秒；每 3 组给一次动作提示（取自器械知识库的常见错误）；
  > 报告疼痛立即暂停并给处置建议；结束输出总结 + 下一步计划（模型不可用时降级为事实清单，不编造）。
  > 路由顺序：事件映射 → 显式场景 → **关键词** → **粘性场景**（会议 collecting / 建档 awaiting / 训练 active）→ LLM 兜底。
- **下一步**：
  1. **填入 `DASHSCOPE_API_KEY`**，跑 `python run.py check`。
     **仍是唯一未验证项**——所有需要真实模型的路径（会议纪要生成、拍题五步链、器械个性化润色、
     训练总结）目前都只跑过降级路径。
  2. **P5 Web 页与 ASR**：新建 `web/app.py`（Flask，`/api/chat` 支持图片/音频上传）+
     `web/templates/index.html`（场景切换、上传、会话历史）；
     `llm.asr()` 接百炼 ASR 模型（`qwen3-asr-flash`），用于会议纪要的语音转写。
     注意：agent-browser 在 Windows 不可用，验收靠 `curl -F` 打接口 + 手动开页面。

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
