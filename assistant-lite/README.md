# assistant-lite

面向**三个场景**的**轻量级 multi-agent** 智能助手：会议纪要 / 拍照解题 / 锻炼指导。

纯 Python 实现，**不依赖 Dify、不依赖 Docker**，启动一条命令。

---

## 当前阶段 / 下一步

> **这一节是跨上下文的接续锚点。每次中断（上下文将满、会话结束）前必须更新。**

- **当前阶段**：`P0–P6 全部完成`
- **状态**：三场景 + CLI + Web + 导出 全部就绪；**唯一未验证项是真实模型调用**
- **各阶段**：
  - **P0**：`config.py` / `schemas.py` / `llm.py` / `agents/base.py` / `run.py` CLI / 根 `.gitignore`
  - **P1**：`session.py`、`orchestrator.py`（五级路由）、`agents/meeting/`、`agents/general.py`
  - **P2**：`agents/exam/`（五步链）、`tools/grids.py`、`tools/calc.py`
  - **P3**：`agents/fitness/profile.py`（建档问卷）、`equipment.py`（15 项器械知识库）
  - **P4**：`agents/fitness/state.py`（训练状态机）、`agent.py` 接入四个训练事件
  - **P5**：`web/app.py` + `web/templates/index.html`；`llm.asr()` 走 `qwen3-asr-flash`
  - **P6**：`tools/export.py`（md/txt/json/csv/docx/pdf）、`run.py export|resources`、
    `web` 的 `/api/export`；PDF 用 reportlab 内置 STSong-Light 中文字体（不依赖外部字体文件）
- **测试**（共 88 项，全部不需要 API Key）：
  ```
  python tests/test_meeting.py     -> 13/13   会议状态机、去重、owner 隔离
  python tests/test_exam_tools.py  -> 11/11   算术工具、黑白格、路由
  python tests/test_fitness.py     -> 18/18   建档问卷、器械知识库
  python tests/test_workout.py     -> 20/20   训练状态机、事件提醒、粘性路由
  python tests/test_web.py         -> 10/10   Flask 接口、附件上传与拦截
  python tests/test_export.py      -> 16/16   六种格式导出与边界
  ```

## 用法速查

```bash
python run.py check                    # 配置自检 + 模型连通
python run.py chat                     # 交互对话（/scene 切场景）
python run.py ask "开始卧推 3组10次 60公斤" --session s1
python run.py ask "膝盖有点疼" -e '{"semantic_action":"pain_report"}' --session s1
python run.py resources --owner u      # 列出已归档资料
python run.py export --owner u -f docx # 导出最新一份资料（md/txt/json/csv/docx/pdf）
python run.py web --port 8801          # 起本地 Web 页
```

- **下一步（唯一待办）**：**填入 `DASHSCOPE_API_KEY`**（复制 `.env.example` 为 `.env`），
  跑 `python run.py check`。目前所有需要真实模型的路径——会议纪要生成、拍题五步链、
  器械个性化润色、训练总结、ASR 转写——都只跑过降级路径（降级逻辑本身已验证正确）。
- **已知边界（首版有意不做）**：自然语言「导出刚才的纪要」的意图路由（现用 CLI/Web 接口导出）；
  摄像头抽帧实时分析、运动后视频离线分析；TTS；多租户与鉴权；知识库语义检索。

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
