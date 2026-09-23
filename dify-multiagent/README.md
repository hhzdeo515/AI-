# dify-multiagent · 眼镜端多 Agent 场景层（Dify 1.17）

把「五个场景 Agent」放到 Dify 上跑，**权威路由与确定性工具仍留在 `assistant-lite`**。
这是 `docs/端侧适配设计_眼镜.md` 方案 B 的落地实现。

| 项 | 值 |
|---|---|
| Dify 实例 | `http://localhost`（`F:\dify\docker`，v1.17.0） |
| 应用名 | 眼镜多Agent场景层 |
| 应用 ID | `04e56a64-b60c-4cac-a706-18e354af411e` |
| 模式 | workflow（13 节点 / 12 边） |
| 对比基准 | 「眼镜 AI 智能助手」（`01a51df1-…`，旧版单应用，保留未动） |

## 架构定位

```
端侧 1-3B（唤醒 / 意图 / 决策）
        │  intent 枚举 + 置信度 + 音频/图片
        ▼
assistant-lite（五级路由 · 权威 · 零 token · 端侧判对率统计）   ← 仍是权威
        │  scene hint + 归一化后的 query
        ▼
Dify 工作流（本仓库）                                          ← 只做场景 Agent
  start → normalize(Code) → question-classifier → 5 个 Agent → answer
```

**为什么路由不放进 Dify**：一旦路由进了 Dify，`source="device"` 打标与 reroute 统计就没有落点，
设计文档 §8 的「端侧判对率」直接断掉；而且 `calc.py` 的 AST 求值、`grids.py` 的逐格校验、
160 项不依赖 API 的测试都会失效。

## 工作流结构

| 节点 | 类型 | 作用 |
|---|---|---|
| `start` | start | 入参：`text` / `device_intent` / `device_confidence` / `scene_hint` |
| `normalize` | code | **零 token 确定性预处理**：意图枚举→场景映射、置信度门控、把设备判定作为「证据」拼进 query |
| `scene_router` | question-classifier | 云端权威分类，输出 `class_id` 并据此分发 |
| `agent_meeting` … `agent_general` | llm | 五个场景 Agent（火山 doubao-seed-2-1-pro） |
| `answer_*` | answer | 各自直连对应 Agent |

### 设备门控规则（在 `normalize` 里，可单测）

| 条件 | 行为 |
|---|---|
| 端侧意图高置信（≥0.75） | 映射为 `scene_hint`，并作为证据写入 query |
| 端侧意图低置信 | 标 `LOW - verify`，**不作为 hint**，交云端判定 |
| `train_pain`（不适） | **永不降级**，无条件视为可信（安全优先） |
| assistant-lite 给了 hint | **优先于**端侧猜测 |

## 用法

```bash
# 1) 刷新控制台凭据（access token 60 分钟过期）
python refresh_console_token.py

# 2) 生成 DSL → 导入 → 发布
pwsh -File deploy.ps1

# 3) 端到端验证六个用例
python run_e2e.py <api-key>

# 4) 契约测试（不需要 Dify / 不需要 API Key）
python tests/test_dsl_contract.py
```

`deploy.ps1` 与 `refresh_console_token.py` 里都能改 `-AppId` / `APP_ID` 以部署到别的应用。

## 实测结果（2026-09-26）

| 用例 | 结果 | 耗时 | 说明 |
|---|---|---|---|
| exam | ✅ 5 步 | 5.6s | 正确算得 126 并给出分步 |
| meeting | ✅ 5 步 | — | 纪要含行动项，未编造期限 |
| fitness | ✅ 5 步 | — | 避开深蹲/膝伤，给血压提醒 |
| resource | ✅ 5 步 | — | 明确声明无法直接访问资料库，不编造 |
| general | ✅ 5 步 | 4.3s | — |
| **override** | ✅ 5 步 | 9.0s | **端侧误判为 exam（0.95），云端仍正确路由到 fitness** |

### ⚠️ 模型 provider 已从火山引擎切到 DeepSeek（2026-09-26）

火山方舟账户**欠费**后，其节点返回 `403 AccountOverdueError`，整个工作流
`status=failed`。错误发生在 Dify 插件进程内，表现为一大段 `PluginInvokeError`
（`volcenginesdkarkruntime._exceptions.ArkPermissionDeniedError`）。

已把五个 Agent 节点从 `langgenius/volcengine` 切到 `langgenius/deepseek/deepseek`
（`deepseek-v4-flash`）。同实例内实测可用，且**快了一倍**（exam 10.4s → 5.6s）。

契约测试 `test_agents_use_a_provider_that_is_not_overdue` 锁住这一点。

`reasoning_effort=minimal` + `reasoning_format=separated` 仍然保留——
它们解决的是 `<think>` 泄漏进播报文本的问题，与 provider 无关。

## 两个踩过的坑（已固化为测试）

### 1. `question-classifier` 的边必须用 `class_id` 作 sourceHandle

`graphon/nodes/question_classifier/question_classifier_node.py:447` 设的是
`edge_source_handle = category_id`。我最初按常规接了一条 `sourceHandle: "source"` 的边到 if-else，
结果是：**分类器成功、日志无报错、工作流 `status=succeeded`，但 `outputs` 是 `{}`**——
因为没有任何边匹配，图在分类器之后静默停止（`total_steps=3` 而不是 5）。

这类失败不报错、只输出空，是最难查的一种。现由 `test_classifier_edges_use_class_id_as_source_handle` 守住。

### 2. 不要用 `variable-aggregator` 汇聚五个 Agent

`variable-aggregator` 会等待全部分支完成，而每次请求只有一个分支会执行 → 永久挂起。
因此 `agent_<scene>` 直接连 `answer_<scene>`。由 `test_no_variable_aggregator_between_agents_and_answers` 守住。

## 控制台凭据是怎么来的（重要）

Dify 1.17 的控制台 API 需要 access token。本实例的 `SECRET_KEY` 运行时为**空字符串**，
而 PyJWT 拒绝空 HMAC 密钥，所以**无法自签令牌**。可行路径是 Redis 里的
`account_refresh_token:<account_id>` → `POST /console/api/refresh-token` 换取。

副作用已处理：Dify 的该端点会**轮换** refresh token，因此脚本在换取后立即把新值写回 Redis，
并保留旧 token 的映射（30 天），**浏览器里已登录的会话不会掉线**。

> 后续所有请求需同时带 `Authorization: Bearer <access>` 与 `X-CSRF-Token: <csrf>`，缺 CSRF 会 401。

## assistant-lite 侧接入（已完成）

路由与执行分离：**路由永远在本地**，执行可选转发 Dify。

| 配置（`assistant-lite/.env`） | 默认 | 说明 |
|---|---|---|
| `EXEC_BACKEND` | `local` | `dify` = 场景执行转发给本应用 |
| `DIFY_BASE_URL` | `http://127.0.0.1/v1` | Dify Service API |
| `DIFY_API_KEY` | 空 | `app-` 开头 |
| `DIFY_TIMEOUT` | `300` | Dify 侧含 LLM 推理，放宽 |
| `DIFY_FALLBACK_LOCAL` | `1` | 转发失败回退本地 |

代码落点：
- `assistant_lite/dify_backend.py` — 载荷构造 / 响应解析 / 调用（不导入 `llm`、`agents`，避免循环依赖）
- `assistant_lite/orchestrator.py` — `device_context()` / `_record_routing()` / `_execute_dify()`
- `assistant_lite/session.py` — `routing_telemetry` 表 + `record_routing()` + `routing_stats()`
- `GET /api/routing-stats` — 判对率查询接口

### 三条刻意保留在本地的路径

不是所有请求都该转发，否则会破坏既有约定：

| 情况 | 处置 | 原因 |
|---|---|---|
| 带附件 | 留在本地 | 需先经本地 ASR / VLM 预处理，绕开会破坏会话状态与归档 |
| 粘性会话（`source="sticky"`） | 留在本地 | 会议收集中 / 训练进行中 / 建档问卷是本地状态机，Dify 侧没有这些状态 |
| 转发失败 | 回退本地（可关） | 回退**显式写入回复文本**，不静默降级 |

本地已完成视觉精读时，观察记录会并入 `query` 传给 Dify，避免为同一张图付两次视觉调用。

### 实测：端侧误判被云端纠正，且被计入统计

```
$ EXEC_BACKEND=dify python run.py ask "我膝盖有点疼，练不下去了" \
    -e '{"device":{"intent":"solve","confidence":0.95}}'
[场景 fitness / 动作 pain_report / 状态 ok]
立即停止当前运动，找稳固支撑坐下休息……

routing_telemetry:
  scene=fitness  source=llm  device_intent=solve  device_scene=exam
  device_correct=no   rerouted=yes

GET /api/routing-stats?owner=e2e-dify
  {"samples":1,"device_correct":0,"accuracy":0.0,"by_source":{"llm":1}}
```

端侧说 exam（0.95），云端路由到 fitness 并执行了不适即停——**判错这件事本身被记了下来**。
这就是路由不能进 Dify 的原因：一旦路由进了 Dify，这张表就没有落点。

### 发现的一个路由空隙（待修）

上例 `source=llm`，说明「练不下去了」没命中 fitness 关键词表，是靠 LLM 兜底才路由对的。
`assistant_lite/agents/fitness/agent.py` 的 `keywords` 缺「练不下去 / 不练了」这类说法。
补上会让这条路径变成零 token 的确定性路由。**本轮未改**——改关键词表会影响既有路由行为，
需要单独一轮并配回归测试。

## 关于「把 LLM 节点换成 Agent 节点」（④ 已核查，**当前不可行**）

Dify 1.17 的 **Agent 节点**（`type: agent`）不是自包含的，它通过
`core/workflow/nodes/agent/plugin_strategy_adapter.py` 走
`factories/agent_factory.get_plugin_agent_strategy()`——也就是**必须装一个
`agent_strategy_provider` 类插件**，节点里填 `agent_strategy_provider_name` +
`agent_strategy_name`。

本实例的实际情况（均已实测确认）：

| 检查项 | 结果 |
|---|---|
| 已安装插件 | 只有 5 个模型类：deepseek / ollama / huggingface_hub / openai / volcengine |
| 是否存在 agent 策略插件 | **没有**（`plugin_installations` 里 `like '%agent%'` 零命中） |
| API 侧是否有内置策略兜底 | **没有**，`get_plugin_agent_strategy` 找不到就 `raise ValueError` |
| 能否从市场装 | 容器内 `https://marketplace.dify.ai/` 返回 200，但 `/api/v1/*` 被中断（SSL UNEXPECTED_EOF），装不了 |
| 新架构 `dify_agent` 路径 | `AgentNodeData.agent_node_kind="dify_agent"`，需要先有 Agent 资产并通过 `bound_agent_id` 绑定 |

**结论**：Agent 节点需要「先装策略插件」或「先建 Agent 资产并发布再绑定」，
两条路都不在当前可自动化的范围内。所以本工作流**继续使用 LLM 节点 + 场景专用提示词**，
这是 Dify 官方多 Agent 教程的推荐做法，行为上等价于「场景 Agent」，
只是少了 Agent 节点的工具循环能力。

**如果要真正启用 Agent 节点**，需要先做其中一件事：

1. 在 Dify 控制台「插件市场」手动安装一个 agent 策略插件（需要能访问市场）；
2. 或走 Agent Studio 建 5 个 Agent 资产 → 发布 → 把返回的 `agent_id` 填进节点的
   `bound_agent_id`（`POST /console/api/agent` 只创建空壳，
   真正的定义要走 `build-draft` → `apply` → `publish`）。

在此之前，**不要让 Agent 节点的语义被误当成已实现**——这与项目里
「IMU 不做就不留假数据占位」是同一条纪律。

## 边界与后续


**本版不含**
- 图片/视觉链路：`start` 目前只收文本（本地精读结果以文本并入 query 的方式绕过）。
  要真正读图需改为接收 `sys.files`（旧应用已验证 `vision.variable_selector: [sys, files]` 的写法）。
- 真实检索：`resource` Agent 只解释指令句式，未打通资料库。
- IMU：按设计文档 C3，任何形式都不做（含假数据占位）。

**后续可做**
1. 补 fitness 关键词表的路由空隙（见上）。
2. 用 Dify 1.17 的 **Agent 节点**替换 LLM 节点（需要 `agent_strategy_provider` 插件；
   当前实例只装了 agent-stub 且仅对 `agent-stub` 路径生效，未安装对应策略插件）。
3. 视觉链路：`vision.enabled` + `variable_selector: [sys, files]`。
