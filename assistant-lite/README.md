# assistant-lite

面向**三个场景**的**轻量级 multi-agent** 智能助手：会议纪要 / 拍照解题 / 锻炼指导。

纯 Python 实现，**不依赖 Dify、不依赖 Docker**，启动一条命令。

---

## 当前阶段 / 下一步

> **这一节是跨上下文的接续锚点。每次中断（上下文将满、会话结束）前必须更新。**

- **当前阶段**：`P0 骨架与基础设施`
- **状态**：进行中
- **已完成**：
  - `assistant_lite/config.py` — 配置入口（.env）
  - `assistant_lite/schemas.py` — Task / Reply 契约
  - `assistant_lite/llm.py` — chat / vision / json_chat / asr
  - `assistant_lite/agents/base.py` — Agent 基类
  - `run.py` — CLI（check / ask / chat / web）
  - 根目录 `.gitignore`
- **下一步**：
  1. 装依赖：`pip install -r requirements.txt`
  2. 复制 `.env.example` 为 `.env` 并填 `DASHSCOPE_API_KEY`
  3. `python run.py check` 验证模型连通
  4. 首次 git commit，然后进入 **P1 会议纪要场景**

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
