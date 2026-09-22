# assistant-lite

面向**三个场景**的**轻量级 multi-agent** 智能助手：会议纪要 / 拍照解题 / 锻炼指导。

纯 Python 实现，**不依赖 Dify、不依赖 Docker**，启动一条命令。

---

## 当前阶段 / 下一步

> **这一节是跨上下文的接续锚点。每次中断（上下文将满、会话结束）前必须更新。**

- **当前阶段**：`P0–P7 全部完成，真实模型已跑通`
- **状态**：三场景 + CLI + Web + 导出 + 资料查询 全部就绪并已用真实模型验证
- **各阶段**：
  - **P0**：`config.py` / `schemas.py` / `llm.py` / `agents/base.py` / `run.py` CLI / 根 `.gitignore`
  - **P1**：`session.py`、`orchestrator.py`（五级路由）、`agents/meeting/`、`agents/general.py`
  - **P2**：`agents/exam/`（五步链）、`tools/grids.py`、`tools/calc.py`
  - **P3**：`agents/fitness/profile.py`（建档问卷）、`equipment.py`（15 项器械知识库）
  - **P4**：`agents/fitness/state.py`（训练状态机）、`agent.py` 接入四个训练事件
  - **P5**：`web/app.py` + `web/templates/index.html`；`llm.asr()` 走 `qwen3-asr-flash`
  - **P6**：`tools/export.py`（md/txt/json/csv/docx/pdf）、`run.py export|resources`、
    `web` 的 `/api/export`；PDF 用 reportlab 内置 STSong-Light 中文字体（不依赖外部字体文件）
  - **P7**：`agents/resource/`（资料查询与自然语言导出）——「我有哪些资料」「导出刚才的纪要成 Word」
    「导出 6e13c22b 成 PDF」全部零 token 解析；导出意图给 0.95 置信度以压过其它场景关键词
- **测试**（共 108 项，全部不需要 API Key）：
  ```
  python tests/test_meeting.py     -> 13/13   会议状态机、去重、owner 隔离
  python tests/test_exam_tools.py  -> 12/12   算术工具、黑白格、路由、题解归档
  python tests/test_fitness.py     -> 18/18   建档问卷、器械知识库
  python tests/test_workout.py     -> 21/21   训练状态机、事件提醒、粘性路由、降级
  python tests/test_web.py         -> 10/10   Flask 接口、附件上传与拦截
  python tests/test_export.py      -> 16/16   六种格式导出与边界
  python tests/test_resource.py    -> 18/18   资料查询、自然语言导出、跨 owner 拒绝
  ```
  > **测试策略：任何测试都不允许打真实 API。** 需要模型的路径一律打桩
  > （见 `test_exam_result_is_archived`、`test_workout_summary_degrades_without_model`）。
  > 这样无论有没有配 Key，回归结果都一致——曾因为没打桩导致配上 Key 后测试变红。

## 真实模型验证记录（2026-09-22）

| 路径 | 结果 | 耗时 |
| --- | --- | --- |
| 会议纪要生成 | ✅ 结构完整（主题/覆盖范围/讨论要点/决策/行动项/待确认） | ~10s |
| 拍照解题五步链 | ✅ 正确识别 2x+6=14 并选 B)4，含程序计算校验 | ~27s |
| 器械个性化指导 | ✅ 结合 42 岁/高血压/膝盖伤/减脂目标，避开深蹲、休息调至 90s | ~17s |
| 训练总结 + 下一步计划 | ✅ 用臀桥替代深蹲，给血压自测与就医提醒 | ~13s |
| 自然语言导出 | ✅ 「导出最新一份成 Word」生成合法 docx | <1s |
| ASR 通路 | ✅ 鉴权/模型名/请求格式均正常（用正弦音测试，无语音故返回空） | ~2s |

**发现并修掉的两个真实问题**：
1. **题解从未落库**：`exam` agent 只塞了个假的 artifact、没写 `_archive`，导致拍照解题的结果
   不进资料库（meeting / fitness 都写了）。已修 + 加回归测试。
2. **纪要编造约束**：模型曾把"需李四确认"扩写成"需在 2 个工作日内确认"，并凭空生成
   "启动财务流程"行动项。已强化提示词（明确禁止补全原文没有的时间期限/流程步骤），
   复测后正确输出"截止时间：未明确"。

## 用法速查

```bash
python run.py check                    # 配置自检 + 模型连通
python run.py chat                     # 交互对话（/scene 切场景）
python run.py ask "开始卧推 3组10次 60公斤" --session s1
python run.py ask "膝盖有点疼" -e '{"semantic_action":"pain_report"}' --session s1
python run.py ask "我有哪些资料" --owner u            # 列出已归档资料
python run.py ask "导出最新一份成 Word" --owner u      # 自然语言导出
python run.py resources --owner u                      # 同上（命令行形式）
python run.py export --owner u -f docx                 # 直接导出最新一份
python run.py web --port 8801                          # 起本地 Web 页
```

- **已知边界（首版有意不做）**：摄像头抽帧实时分析、运动后视频离线分析；TTS；
  多租户与鉴权；知识库语义检索；导出未做 SRT/ZIP 打包。
- **一个待办**：`DASHSCOPE_API_KEY` 写在 `.env` 里（已被 gitignore 排除）。
  若 Key 泄露或轮换，直接改 `.env` 即可，无需动代码。

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
