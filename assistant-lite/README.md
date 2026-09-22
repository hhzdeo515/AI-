# assistant-lite

面向**三个场景**的**轻量级 multi-agent** 智能助手：会议纪要 / 拍照解题 / 锻炼指导。

纯 Python 实现，**不依赖 Dify、不依赖 Docker**，启动一条命令。

---

## 当前阶段 / 下一步

> **这一节是跨上下文的接续锚点。每次中断（上下文将满、会话结束）前必须更新。**

- **当前阶段**：`P0–P9 全部完成`
- **状态**：三场景 + CLI + Web + 导出 + 资料查询 全部就绪，真实模型已验证；
  Web UI 已按「AI 智能眼镜 Companion」方向重做
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
| Vision pipeline 真实进度 | ✅ 轮询捕获到 `recognize → solve → verify` 三步真实推进 | — |
| 浏览器端全流程 | ✅ CDP 驱动：注入图片 → 点 Solve → ANSWER 块渲染出 `B) 9` | ~40s |
| 会议白板照片 | ✅ 视觉模型忠实记录中文白板（含编号/颜色），内容进入纪要 | ~5s |
| 白板 + 转写混合纪要 | ✅ 白板里的"风险：测试环境未就绪"被正确提取为行动项，未明确的字段仍标"未明确" | ~12s |

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
    web/                 本地 Web 页
      app.py             Flask：/api/chat /progress /profile /resource /reset /export …
      templates/         index.html
      static/            app.css / app.js
  data/                  运行时数据（不入库）
  tests/
```

## Web UI（Glasses Companion）

设计方向：**面向 AI 智能眼镜的个人智能助手 Companion**——左侧导航 + 工作区 + 可滑出的 Context Panel，
浅色底（`#F5F7F8`）+ 冷青强调色（`#3FA7A3`），细线线性 SVG 图标，克制动效。
Chat 只作为底部的 Command Layer，主区域优先展示当前任务与结果。

- **三个场景各有独立视觉语言**：Meeting = 音频波形，Vision = 取景框四角，Fitness = IMU 活动轨迹。
  不看文字也能区分。
- **状态语言统一**：`READY / LISTENING / ACTIVE / PAUSED / ENDED`，用 badge 表达。
- **没有硬件连接就写 `Demo Mode`**，不伪装成「Glasses Connected」。
- **Pipeline 显示真实阶段，不是假动画**：
  - Vision 四步（Capture → Recognize → Solve → Verify）由 `progress.report()` 在 Agent 内逐步上报，
    前端轮询 `/api/progress` 渲染。实测捕获到的真实序列：
    `recognize(done:capture) → solve(done:capture,recognize) → verify(done:capture,recognize,solve)`
  - Meeting 四步（Content captured → Transcript ready → Structuring → Summary ready）
    由真实会话状态推导；未上传录音时标签自动变为 `Content captured`。
  - 没有进度记录时接口返回空 `steps`，前端回退到「整体处理中」，**不编造阶段**。
- **动画都有目的**：反馈状态（状态点 pulse）、表达进度（scanline / pipeline）、
  提示可交互（hover 抬升、取景框收拢 2–3px、波形流动）、区分场景（三种 motif）。
  统一 `prefers-reduced-motion` 降级。
- **响应式**：Desktop = 导航 + 工作区 + Context Panel；Tablet(≤1080) = 导航 + 工作区，Panel 浮层；
  Mobile(≤820) = 抽屉导航 + 底部 Tab。
- **深链**：视图写入 `location.hash`，支持刷新保持与浏览器前进/后退。

## 分阶段实现（全部完成）

| 阶段 | 内容 |
| --- | --- |
| P0 | 骨架、config、llm、schemas、CLI、git init |
| P1 | session + 会议纪要场景 |
| P2 | 拍照解题场景 + grids/calc 工具 |
| P3 | 锻炼-健康档案与器械识别 |
| P4 | 锻炼-训练状态机与事件提醒 |
| P5 | Web 页与 ASR |
| P6 | 导出（docx/pdf）与收尾 |
| P7 | 资料查询与自然语言导出 |
| P8 | 接真实模型验证：修「题解未落库」与「纪要编造约束」 |
| P9 | Web UI 重做为 Glasses Companion + 真实进度上报 |
| P10 | 会议场景支持白板照片（修功能回退） |
| P11 | 为眼镜接入做准备：`speech` 播报字段 / 长音频分片 / 异步任务 / TTS 与播报打断 |

## 为眼镜接入做的四项准备（P11）

网页版能用，不等于戴在脸上能用。这四项都是**不需要真机就能做完**的部分。

### 1. `speech` — 语音播报字段

旧版设计文档里定义了 `display_text` + `speech_text` 两个输出字段，迁移时丢了。
屏幕上能显示 600 字，耳朵里不行——**你不可能把会议纪要念给用户听**。

现在 `Reply.speech` 与 `Reply.text` 分开：

| 场景 | text（屏幕） | speech（耳朵） |
| --- | --- | --- |
| 会议纪要 | 完整纪要 ~600 字 | `纪要含3条要点、3项行动项，其中1项截止时间未明确` |
| 拍照解题 | 答案+解析+程序校验 | `答案是B，因为5乘7加3等于38，符合方程。` |
| 报告疼痛 | 处置建议+就医分级 | `已暂停训练。膝盖不适已记录，请立即停止该动作…`（安全内容必须完整念出） |

**关键设计：不额外调模型。** 播报语由生成正文的那次调用顺带产出
（结构化输出加 `speech` 字段，自由文本用 `<<<SPEECH>>>` 分隔），
模型没给就用 `speech.truncate()` 规则压缩。这样不会为每个长回复多付一次调用。

### 2. 长音频分片

以前整段音频一次提交，1 小时会议必崩。现在按 `ASR_CHUNK_SECONDS`（默认 240 秒）切分后逐段转写再拼接。

- `.wav` 用标准库 `wave` 切，**零依赖**
- `.mp3/.m4a/...` 用 ffmpeg（`-c copy` 不重编码，快且无损）
- **没有 ffmpeg 不静默失败**：原样提交并把原因写进回复的「未处理的部分」

> ffmpeg 按约定装在 **E 盘**：`E:\AI智能助手\tools\ffmpeg\bin\ffmpeg.exe`（79MB，静态构建）。
> 路径可通过 `TOOLS_DIR` / `FFMPEG_PATH` 覆盖。

### 3. 异步任务

一次拍题要 ~27 秒（三次视觉调用）。网页上用户能等，**戴在脸上 27 秒没有任何反馈是不能接受的**。

```
POST /api/chat/async   -> 202 {task_id, request_id}   立刻返回
GET  /api/task?task_id -> {status: pending|running|done|error, result}
GET  /api/progress?request_id -> 真实执行阶段（pipeline 用）
```

实测：**提交耗时 0.06 秒**，22 秒后取到结果，期间 pipeline 真实推进
`recognize → solve → verify`。

实现取舍：进程内线程池 + 内存登记表，够单机本地用，不引入 Celery / Redis。
同步接口 `/api/chat` 保留，供短请求使用。

### 4. TTS 与播报打断

`speech` 有了但没人把它变成声音，这一环也补上了：

```
GET /api/speak?text=...&owner=...&session_id=...   -> audio/mpeg
    响应头 X-Playback-Generation: 当前播报代际号
GET /api/playback?owner=...&session_id=...         -> {generation}
```

走 dashscope 的 **tts_v2（cosyvoice-v1）**。
> 注意：**sambert 那套在百炼新账号上返回空音频**，实测过，不要用。

**播报打断**（旧版设计里叫「设备立即停止当前播放；使旧播放队列失效」）
服务端只做一半——维护一个**播报代际号**：收到 `stop_playback` 事件就 +1。
设备端只需实现「播放前/播放中比对代际号，不一致就丢弃并停播」。

实测：`纪要已生成，共三条要点、三项行动项，其中一项截止时间未明确。`
→ 82KB mp3、5.15 秒、生成耗时 2.1 秒；
打断后代际号 0→1，后续 `/api/speak` 自动带上新代际号。

### 还差什么（需要真机或需要你决策）

| 缺口 | 说明 |
| --- | --- |
| 设备接入层 | 摄像头取帧策略、麦克风 PCM 流、按键/手势事件产生逻辑 —— 必须等真机 SDK |
| 设备端播放器 | 代际号比对与停播要写在设备上，服务端已经就绪 |
| 身份与鉴权 | 现在 owner 是本地字符串，无设备绑定、无离线队列 |

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
