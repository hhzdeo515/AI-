## LangGraph 图（draw_mermaid 原始输出）

```mermaid
---
config:
  flowchart:
    curve: linear
---
graph TD;
	__start__([<p>__start__</p>]):::first
	device_gate(device_gate)
	route(route)
	telemetry(telemetry)
	meta_command(meta_command)
	calc_quick(calc_quick)
	exam_vision(exam_vision)
	dify_scene(dify_scene)
	local_llm(local_llm)
	postprocess(postprocess)
	__end__([<p>__end__</p>]):::last
	__start__ --> device_gate;
	calc_quick -. &nbsp;llm&nbsp; .-> local_llm;
	calc_quick -. &nbsp;done&nbsp; .-> postprocess;
	device_gate --> route;
	dify_scene --> postprocess;
	exam_vision --> postprocess;
	local_llm --> postprocess;
	meta_command --> postprocess;
	route --> telemetry;
	telemetry -. &nbsp;local&nbsp; .-> calc_quick;
	telemetry -. &nbsp;dify&nbsp; .-> dify_scene;
	telemetry -. &nbsp;vision&nbsp; .-> exam_vision;
	telemetry -. &nbsp;meta&nbsp; .-> meta_command;
	postprocess --> __end__;
	classDef default fill:#f2f0ff,line-height:1.2
	classDef first fill-opacity:0
	classDef last fill:#bfb6fc

```

## LangGraph 节点

- `calc_quick`
- `device_gate`
- `dify_scene`
- `exam_vision`
- `local_llm`
- `meta_command`
- `postprocess`
- `route`
- `telemetry`

## LangGraph 条件边（dispatch 的可能去向）

- `meta`   → meta_command
- `vision` → exam_vision（带图片且场景=exam）
- `dify`   → dify_scene（EXEC_BACKEND=dify）
- `local`  → calc_quick

## calc_quick 之后的二选一

- `done` → postprocess（命中纯算术，零 token）
- `llm`  → local_llm
