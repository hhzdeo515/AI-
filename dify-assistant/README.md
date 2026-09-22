# 眼镜 AI 智能助手：本机运行版

已在本机 Dify 1.17.0 搭建并发布。公考增强版为 30 个节点、34 条连线，包括系统提示词、总控路由、场景分支、上下文、真实 HTTP 工具和文件导出。实现形式是 Chatflow 场景编排。

- 使用入口：http://localhost/chat/OPC9xib8SiFvairE
- 工作流：http://localhost/app/01a51df1-fa63-4ff2-84d4-321a21c2c5a3/workflow
- 应用：眼镜 AI 智能助手
- Dify 知识库：眼镜助手·个人资料知识库
- 配套服务健康检查：http://localhost:8792/health

## 直接试用

1. 输入“计算 (18+24)*3，并说明步骤”，再追问“为什么先算括号”。
2. 上传题目图片，说“识别这道题并解答”。
3. 输入“生成会议纪要。测试转写：李四说我周五前提交接口文档，张三决定先做本机版。”
4. 继续说“导出刚才的纪要成 Word”或“导出成 PDF”，点击返回的下载链接。
5. 图片识别后说“导出刚才图片的原件”，得到含原图的 ZIP。
6. 说“查找会议纪要”查看资料与 ID。指定资料 ID 可以选择导出对象。

下载链接有效期一小时，仅当前电脑访问；到期后重新要求导出即可。支持 Word、PDF、Markdown、TXT、JSON、CSV、ZIP 和原附件打包。CSV 是资料内容导出，不是 Excel 工作簿。

## 按键与会议事件

可把下面 JSON 直接作为聊天消息发送。每次新事件使用新的 event_id；重试同一事件保留原 ID，服务会去重。

```json
{"input_type":"button","event_id":"button-001","semantic_action":"switch_scene","scene":"homework"}
```

```json
{"input_type":"button","event_id":"meeting-001","semantic_action":"start_meeting"}
```

```json
{"input_type":"voice","event_id":"meeting-002","semantic_action":"append_meeting","transcript":"李四：我负责在周五前提交接口文档。"}
```

```json
{"input_type":"button","event_id":"meeting-003","semantic_action":"summarize_meeting"}
```

也支持 stop_meeting、solve_captured_question、stop_playback、confirm_pending_action、cancel_pending_action。后两项在无待确认动作时明确回复无待办；停止播报事件当前不控制真实设备。图片仍通过 Dify 上传控件提交。

## 公考拍题增强（2026-09-20）

四个行测模块加政治理论、常识，共六类题，已配置分类解题和复核规则：

- 言语理解：主旨、细节、填空和排序，检查转折、指代、语义程度与选项范围。
- 数量关系：列式、单位、约束、结果代回；把关键算式交给真实计算工具。
- 判断推理：定义、类比、逻辑及图形。图形题重点核对旋转/镜像、位置、数量、属性、叠加和空间关系，并排除选项。
- 资料分析：材料定位、年份、单位、基期/现期、增长率/百分点和舍入。
- 政治理论与常识：稳定基础知识可作答；依赖最新政策、法规或时事而无资料时明确待核实，不冒称已查证。

拍题分支：图片精读 → 六类初解（再次直接查看原图）→ 算术/黑白格工具校验 → 终审（再次直接查看原图）→ 答案质量门槛。初解与终审是两次模型调用，使用同一个视觉模型，不是独立模型投票。前者思考强度 low，后者 medium；比原版增加耗时与模型调用费用。

等尺寸黑白格九宫格会提取逐格01数据，由 /api/exam-check 检查异或、并集、交集、同或及方向差集能否解释已知行/列，并给出匹配选项。此工具只处理指定的二元黑白格规则，不能覆盖所有图形推理；识图是否正确仍需终审回看原图。当前没有自动图形矢量化或通用立体折叠求解器。

拍照建议：一次一题，完整保留题干、全部题图及所有选项；资料分析同时附完整材料和单位。复杂题可上传整题图与局部清晰图，并注明属于同一题。不同题目开新对话，避免沿用上一题条件。

系统没有接入权威真题答案库或联网搜索。已保存的模型题解只是历史资料，不是官方答案；本次合成测试不能推算真实公考正确率。要测准确率，需要用未参与调试的真实题目和可靠标准答案做独立评测，同时看解释是否成立。

专项提示词在 exam_prompts.py；确定性格子工具在 exam_tools.py；合成端到端测试在 test_exam.py，工具边界测试在 test_exam_tools.py。结果保存在 test-results/exam/。

## 变量与工具

- 系统输入：sys.query、sys.files、sys.user_id、sys.conversation_id。
- 会话变量：context_json、active_scene、last_resource_id，均为字符串，初始上下文为 {}，由服务结果实际回写。
- 环境变量：service_token，已在当前应用配置，用于 HTTP 服务认证。
- 节点变量：输入规范化、图片识别聚合、路由校验、工具结果解析、回复聚合、最终状态解析；生成脚本检查节点引用。
- /api/context：读取会话上下文和服务能力。
- /api/prepare：执行计算、会议记录、资料检索/导出和按键处理。
- /api/finalize：保存题解、纪要和图片，排队同步 Dify 知识库并更新上下文。
- /api/exam-check：批量算术计算、逐行/列黑白格运算验证，返回具体结果供终审核对。

工具请求使用 Dify 的用户和会话标识；资料服务还会按 owner 过滤资料。现阶段按单机个人开发用途配置，未做面向公网的多租户部署。

## 当前边界

- 图片由上传模拟摄像头；语音由转写文字模拟。真实眼镜、麦克风采集、ASR、TTS、定位和地图接口待接入。
- 导航可以记住目的地与出行方式，会明确说明不能计算真实路线。
- 本机版指编排与资料服务部署本机；推理使用本机 Dify 已配置的 DeepSeek 和豆包服务，需要联网及可用额度。
- 原图保存在本机，题解/纪要文字同步到本机 Dify 知识库。尚未接外部云存储；资料查询使用本地资料库文本过滤，尚未接知识库语义问答。
- 单场会议上限 48000 字符。知识库异步索引失败会记录 failed，当前没有持久化重试队列。
- PDF 已嵌入本机中文字体并完成页面检查；Word 已验证可下载及 OOXML 结构，当前环境没有可用的文档渲染器，未完成 Word 页面视觉检查。复杂数学公式暂未实现专业公式排版。

## 文件与运行维护

- build_workflow.py：生成工作流、检查变量引用。
- assistant.yaml：不包含真实令牌的可分享 DSL；迁移导入后需配置 service_token、模型、知识库和服务地址。
- assistant.private.yaml、.private/settings.json：当前机器运行配置，包含密钥，不要分享。
- service.py：配套服务；data/ 保存 SQLite 数据、原图、导出及本机中文字体。
- test_live.py、verify_remaining.py、verify_service.py：实际运行测试；test-results/ 保存结果。知识库中已有合成测试样例。

Docker Desktop 和原有 Dify 容器应保持运行。配套容器已设置自动重启：

```powershell
docker start dify-glasses-service
docker logs --tail 40 dify-glasses-service
docker restart dify-glasses-service
```

修改 exam_tools.py 后，在本目录执行以下命令同步容器中的工具模块，再重启：

```powershell
docker cp exam_tools.py dify-glasses-service:/service/exam_tools.py
docker restart dify-glasses-service
```

Dockerfile 也已将 exam_tools.py 打包进镜像；重新创建容器前需用本目录最新 Dockerfile 构建镜像，或显式挂载该模块。service.py 仍通过原有只读文件挂载更新。

服务只绑定 127.0.0.1:8792。Dify 容器通过 host.docker.internal:8792 调用，使用现有网络允许配置。备份需要同时保留 Dify 数据、data/ 和私有配置；不要只备份 DSL。

## 验证记录

2026-09-20 公考增强版：10 项合成端到端用例均通过（旋转图形、异或叠加、缺图拒猜、言语、数量、资料分析、逻辑、政治理论基础、常识、最新材料缺失）。旋转和叠加题同时检查了解释；叠加题运行记录确认实际调用工具并逐行验证 XOR。另通过工具边界测试，包括行列方向、重复匹配选项、非法格子、除零和非法表达式。该测试集规模小且用于开发调试，不代表独立真题准确率。本次图形题耗时约 49 秒和 78 秒，其他可作答样例约 27–35 秒，实际时延随模型服务变化。

2026-09-19：Dify 显示已发布，检查清单“所有问题均已解决”。通过真实工作流验证计算 126、多轮解释、图片方程 x=5、会议纪要、Word/PDF 下载、按键切换、导航目的地继承、原始 PNG 打包；中文 PDF 渲染无字体报错且页面可读。服务测试通过未认证请求拒绝、重复会议片段去重、跨 owner 导出拒绝及下载签名校验。外部地图、实际硬件和语音接口未测，因为尚未接入。
