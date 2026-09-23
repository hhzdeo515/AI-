# langgraph-app · 眼镜助手（LangGraph 编排层）

把编排建模成**显式状态图**，替代 `../assistant-lite/` 里手写的 `handle()` 分支。
三套并存，各司其职：

| 目录 | 角色 | 状态 |
|---|---|---|
| `../assistant-lite/` | 自研编排层（基线） | **保留不动**，188 项测试可跑，作行为对照 |
| `langgraph-app/`（本目录） | LangGraph 编排层 | 本版 |
| `../dify-multiagent/` | Dify 场景 Agent 执行层 | 保留，作为本图的一个**可选执行后端** |

## 为什么值得迁移（不是换个写法）

你此前记录过「暂不引入框架」的判断，并写下重新评估的触发条件——其中一条正是
**「设备断连后需从任意步骤恢复（眼镜场景）」**。这条现在是硬需求，而基线做不到：

| 需求 | assistant-lite | 本版 |
|---|---|---|
| 断连后从任意步骤恢复 | `tasks.py` 进程内线程池 + 内存表，**重启即丢** | checkpointer 落盘，同 `thread_id` 续跑 |
| 危险动作需人工确认 | 无 | `interrupt_before` / `interrupt()` |
| 排查"为什么走到这个分支" | 读代码 + 日志 | 状态快照 / 时间旅行 |
| 编排规则可读性 | 240 行 `handle()` 里的 if | 图上的节点与条件边 |

**实测证据**（`tests/test_graph.py::test_interrupt_then_resume_does_not_redo_decided_work`）：
在 `local_llm` 之前中断，此时路由已决定（`scene=general`）、正文为空；
用同一 `thread_id` 恢复后，**只有未完成的那一步真正执行**（模型调用计数 = 1）。

```
中断时 result      = {}
中断时 routing     = {'scene': 'general', 'action': 'answer', 'source': 'llm_failed'}
中断时 next        = ('local_llm',)
恢复后 result.text = '恢复之后才生成的回答'
```

## 图结构

```
START → device_gate → route → telemetry → dispatch
                                              ├─ meta  → meta_command ─┐
                                              ├─ dify  → dify_scene ───┤
                                              └─ local → calc_quick ───┤
                                                          ├─ 命中 → postprocess
                                                          └─ 未命中 → local_llm → postprocess
                                                                                    ↓
                                                                                   END
```

| 节点 | 作用 |
|---|---|
| `device_gate` | 端侧 1–3B 判定归一化（零 token，纯函数） |
| `route` | 五级权威路由：事件 → 显式 → **关键词** → 粘性 → LLM 兜底 |
| `telemetry` | 端侧判对率落库 |
| `meta_command` | `stop_playback` / `switch_scene`，不产生内容 |
| `calc_quick` | 纯算术快路径（AST 安全求值，零 token） |
| `dify_scene` | 转发 Dify 场景 Agent（可选后端，失败回退 `local_llm`） |
| `local_llm` | 本地场景回复 |
| `postprocess` | 播报语 + 归档 |

### 两条守卫从代码 if 变成了图结构

`dispatch` 把「什么情况下**不**转发 Dify」写成条件边，可单独测试：

| 情况 | 去向 | 原因 |
|---|---|---|
| 带附件 | `local` | 需先经本地 ASR/VLM 预处理，绕开会破坏会话状态与归档 |
| 粘性会话（`_sticky`） | `local` | 会议收集中 / 训练进行中是本地状态机，Dify 侧没有这些状态 |
| 其余 + `EXEC_BACKEND=dify` | `dify` | 失败由图内回退，回退会**显式写入回复文本**，不静默 |

## 用法

```powershell
.\start.ps1                     # 起 Web 页（127.0.0.1:8802，本机免口令）
.\start.ps1 -Check              # 只做自检（配置 + 模型连通）
.\start.ps1 -LAN                # 允许局域网访问（需已设 ACCESS_TOKEN）
.\start.ps1 -LAN -AllowNoAuth   # 局域网访问且不要口令（需明确确认风险）
.\start.ps1 -Port 9000          # 换端口
.\start.ps1 -NoBrowser          # 不自动开浏览器
```

启动后浏览器打开 **<http://127.0.0.1:8802>** 即可，本机模式**不需要口令**。

> **口令策略**：本机监听（`127.0.0.1`）默认免口令——只有这台机器能连，摩擦最低。
> 局域网监听（`0.0.0.0`）默认要求口令，因为同网络任何人都能用你的 API Key
> 并读写本机 `data/`。确实不想要口令时必须显式加 `-AllowNoAuth`——
> 这道保护防的不是"不让你做"，而是"手滑把没口令的服务暴露给整个 WiFi"。

或者直接用 `python`：

```powershell
python run.py check                       # 配置自检 + 模型连通
python run.py graph                       # 打印图结构（需 grandalf）
python run.py ask "计算 (18+24)*3"         # 零 token 算术快路径
python run.py ask "hello" --session s1     # 走模型
python run.py ask "解这道题" -f q.png       # 拍照解题（本地视觉链）
python run.py ask "我膝盖疼" -e '{"device":{"intent":"solve","confidence":0.95}}'
python run.py stats                        # 端侧判对率
python run.py resume <owner> <session>      # 从检查点续跑（断连恢复）
python run.py web --port 8802
python run.py web --host 0.0.0.0 --port 8802

python tests\test_graph.py                 # 47 项，不需要 Dify / API Key / 联网
python tests\test_web.py                   # 27 项
python tests\test_tools.py                 # 13 项
python tests\test_auth.py                  # 15 项鉴权
```

## 局域网访问与访问口令

默认本机使用**不需要口令**。想用手机或平板打开时：

```powershell
# 1) 生成并写入访问口令
python -c "import secrets,pathlib;p=pathlib.Path('.env');s=p.read_text(encoding='utf-8');p.write_text(s.replace('ACCESS_TOKEN=','ACCESS_TOKEN='+secrets.token_urlsafe(12)),encoding='utf-8')"

# 2) 以局域网模式启动（会打印手机可打开的地址）
.\start.ps1 -LAN
```

浏览器打开后输一次口令，之后靠 cookie 记住（30 天）。

设计上的取舍：

| 行为 | 原因 |
|---|---|
| 本机监听默认免口令 | 只有这台机器能连，摩擦最低 |
| 局域网监听默认要口令，除非 `-AllowNoAuth` | 同网络任何人都能用你的 Key 并读 `data/` |
| 未通过时 API 返回 **JSON 401**，页面才跳登录页 | 返回 HTML 登录页会让前端拿不到可读错误 |
| 放行面只有 `/login` `/health` `/static/*` | 登录页自己得能打开，否则重定向死循环 |
| 口令用 `secrets.compare_digest` 比较 | 避免时序侧信道 |
| `/health` 只报 `auth_enabled`，不回显口令 | 别让健康检查变成泄露点 |
| 支持 `Authorization: Bearer <token>` | 设备端/自动化调用比 cookie 方便 |

> ⚠️ 这是**单一口令**，不是账号体系：没有用户表、没有找回密码、没有会话吊销。
> 换口令后旧 cookie 自动失效。仅适合个人或极小范围使用。

### 首次部署

```powershell
python -m pip install -r requirements.txt
Copy-Item .env.example .env      # 填入 DASHSCOPE_API_KEY（百炼）
.\start.ps1 -Check               # 应当看到「模型连通 : OK」
```

不填 Key 也能跑：102 项测试与零 token 算术路径都不需要 Key，
只有涉及模型的场景会失败（会显式报错，不会静默）。

切 Dify 执行后端（`.env`）：

```
EXEC_BACKEND=dify
DIFY_API_KEY=app-xxxxxxxx
DIFY_FALLBACK_LOCAL=1
```

## Web 层

`lg_assistant/web/` 是**接口与基线逐一对齐**的 Flask 层，前端 `app.js` / `app.css`
原样复用，没有重写 UI：

| 接口 | 说明 |
|---|---|
| `POST /api/chat` `POST /api/chat/async` `GET /api/task` | 对话（同步/异步）+ 按 `request_id` 幂等 |
| `GET /api/state` | 读某 thread 的检查点状态 |
| `GET /api/resume` | **本版新增**：断点续跑（基线的内存任务表做不到） |
| `GET /api/routing-stats` | 端侧判对率 |
| `GET /api/resources` `/api/resource` `/api/export` | 资料与六种格式导出 |
| `GET /api/speak` `/api/progress` | 语音合成、请求级进度 |
| `GET /api/profile` | **未实现**：返回空结构并注明，不编造数据 |

## 拍照解题：真读图

带图片且路由到 `exam` 的请求走 `exam_vision` 节点，本地四步链：

```
精读（只记录，不解题）→ 初解（重新看原图）→ 工具校验（零 token）→ 终审（再重看原图）
```

**为什么不在 Dify 里做**：Dify 工作流的 `start` 只收文本。要么本地做完精读再把文本带过去
（丢掉「终审重新看原图」这条关键设计），要么在本地图里完整跑完。图形推理题一旦只靠文字转述，
行列位置与黑白关系就没了，题就废了。

实测（`dify-assistant/test-results/test-equation.png`，16.9s）：

```
**答案：5**
分类：数量关系 / 一元一次方程求解
**解析** 原图显示方程为 3x + 7 = 22。移项得 3x = 15，两边除以 3 得 x = 5。
**程序计算校验**  22 - 7 = 15 ／ 15 / 3 = 5.0
**核对说明** 直接核对原图，方程清晰无误；初解与程序校验均支持 x = 5，无矛盾。
[播报] 答案是5，因为3x等于15，除以3得x等于5。
```

程序校验确实执行了（`22-7=15`、`15/3=5.0` 来自确定性工具，不是模型算的）。

## 移植的资产（原样搬运，不重写）

`lg_assistant/ported/`：`calc.py`（AST 安全求值）、`grids.py`（黑白格逐格校验）、
`speech.py`（屏幕长文 → 可朗读短句）。
`lg_assistant/tools/`：`audio.py`（长音频分片，wav 用标准库 / 其余走 ffmpeg）、
`export.py`（Markdown / 纯文本 / JSON / CSV / Word / PDF）。
`lg_assistant/vision_prompts.py`：公考六类题的视觉/初解/终审提示词。

这些都是无依赖或有明确外部依赖边界的模块，是「关键路径不走模型」的落地，
重写等于把已验证的东西再赌一次。

**注意**：移植的是**副本**。对副本的修复需另外回流（见下）。

## 迁移中发现并修掉的三个真 bug

### 1. 检查点里的旧状态污染下一条请求（最严重）

LangGraph 对 TypedDict 结构的字段**默认做合并**，而节点只写 `{"result": {...}}`。
表现为：同一会话先问「计算 (18+24)*3」再问「hello」，**第二条请求返回第一条的答案**。

更隐蔽的是我第一版修复反而引入了第二个 bug：

- 用「`result.text` 是否为空」做条件边 → `calc_quick` 未命中时返回 `None`，
  条件边读到检查点恢复的**上一次** `result`，误判「已完成」，`local_llm` 从不执行；
- 改用 `Command(goto=...)` → 它与静态边**并存**，两条路都跑并竞态。

最终修法：节点每轮都写一个显式路由字段 `calc_hit`，条件边**只读它**。
教训写进了代码注释：**LangGraph 里不要用"数据值"驱动控制流**。

### 2. 播报语残留 Markdown 标题（**已回流到 assistant-lite**）

`to_plain` 的标题正则是 `^\s{0,3}#{1,6}\s*`（`re.M`）——**只匹配第一个标题**。
重复串里后续的 `## 标题` 前面是空格而非行首，会原样送去朗读：
屏幕上是标题，耳朵里听到「井号井号 标题」。短文本分支尤其明显（没有截断兜底）。
改为不锚定的 `#{1,6}`。

这个 bug 是移植时**在副本里发现**的，已回流修复 `assistant-lite/assistant_lite/speech.py`，
并在两边都加了回归测试（`test_to_plain_strips_all_headings_not_just_first`），
避免它再被搬来搬去。

### 3. 路由空隙：「练不下去了」命中不了 fitness

这条在 Dify 阶段就实测发现过（当时靠 LLM 兜底才路由对）。本版把
`练不下去 / 不练了 / 练不动 / 膝盖 / 腰疼 / 肩膀疼 / 脚踝` 补进关键词表，
现在成为零 token 确定性路由。

## 与基线的对照结论

**LangGraph 换掉的是编排层，不是工具层。** 五级路由、确定性工具、
端侧判对率统计、幂等去重这些都没有变，也不需要变——它们与编排框架无关。
真正变的是：状态从「`Task` / `Reply` / session 三处分散」收敛成一份可持久化的 State，
以及由此获得的**断点续跑**能力。

**代价**（如实记录）：`requirements.txt` 从 8 个包涨到 6 条直接依赖（含
langchain-core 等传递依赖）；测试从 188 项降到 41 项（覆盖的是编排与新代码，
不含基线的 Web/导出/音频分片等）；`run.py graph` 需要额外的 `grandalf`。

## 边界

- **视觉链路未接**：`start` 只收文本。带附件的请求在图里就被 `dispatch` 拦在本地。
- **`resource` 场景未打通资料库**：提示词里明确要求不得编造资料标题/ID。
- **IMU 不做**：按 `docs/端侧适配设计_眼镜.md` C3，任何形式都不做（含假数据占位）。
- 流式 token 未接：LangGraph 支持，但设备侧播放链路还没需求验证。
