"""转写层测试：结果解析、热词表、分离可信度标注。不需要 API Key、不联网。

覆盖的都是**纯函数**：网络部分（上传 / 提交 / 轮询）由 tests/test_web.py 的
接口契约与真机跑动覆盖，这里只把「本地怎么整理服务端结果」钉死。

真实数据的来历：一段 645.76 秒的具身智能圆桌（7 人：主持 + 6 位嘉宾），
服务端聚成 6 类，其中一位嘉宾 8 秒的自我介绍被并进了上一位发言人的类里。
``_REAL_UTTERANCES`` 就是那次转写的说话人分段（文本做了截断），
下面几个测试都围绕它——不用真实数据编出来的规则，换个会议就不成立。
"""

from __future__ import annotations

import json
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lg_assistant import config, store, transcribe  # noqa: E402

#: 真实录音的分离结果（段/秒/说话人编号都来自实测）
_REAL_UTTERANCES = [
    {"speaker": 0, "begin_ms": 2600, "end_ms": 9600, "text": "你好，那我先正式开个场…我是智源模型。"},
    {"speaker": 1, "begin_ms": 9600, "end_ms": 12600, "text": "研究师辛，我是你们先ok了。Ok."},
    {"speaker": 0, "begin_ms": 13200, "end_ms": 61600, "text": "主持人王鹏伟，也是本次圆桌论坛的负责人…"},
    {"speaker": 2, "begin_ms": 61700, "end_ms": 87100, "text": "我是银河通用的创始人及cto…"},
    {"speaker": 1, "begin_ms": 88200, "end_ms": 104200, "text": "我是罗建南，来自上海…"},
    {"speaker": 3, "begin_ms": 105900, "end_ms": 120900, "text": "我是高阳，千寻智能…"},
    {"speaker": 2, "begin_ms": 122100, "end_ms": 129300, "text": "大家好啊，我罗荣青，北京大学计算机学院…"},
    {"speaker": 4, "begin_ms": 129400, "end_ms": 148800, "text": "其他没什么。嗯，大家好，我是沐瑶…"},
    {"speaker": 5, "begin_ms": 149400, "end_ms": 163800, "text": "我是丁文超，复旦大学的青年研究员…"},
    {"speaker": 0, "begin_ms": 163800, "end_ms": 233100, "text": "各位感谢各位老师吧…"},
    {"speaker": 2, "begin_ms": 233500, "end_ms": 400000, "text": "我觉得其实大家可能说的不是一个拆gbt…"},
    {"speaker": 0, "begin_ms": 400000, "end_ms": 420300, "text": "好嘞好嘞，那我问一下那个王老师…"},
    {"speaker": 2, "begin_ms": 420300, "end_ms": 509900, "text": "其实我们回看这个数字…"},
    {"speaker": 0, "begin_ms": 509900, "end_ms": 516800, "text": "好嘞好嘞，感谢王贺老师…"},
    {"speaker": 1, "begin_ms": 519000, "end_ms": 645700, "text": "呃，王老师讲的很好啊，我补充一点…"},
]


def _fresh() -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="lgasr-"))
    config.DATA_DIR = tmp
    config.DB_PATH = tmp / "app.sqlite3"
    config.CHECKPOINT_DB = tmp / "ck.sqlite3"
    config.EXPORT_DIR = tmp / "exports"
    config.UPLOAD_DIR = tmp / "uploads"
    config.HOTWORDS_PATH = tmp / "hotwords.txt"
    store._local = threading.local()
    store._initialised = False
    return tmp


# --------------------------------------------------------------------------- #
# 服务端结果 -> 统一结构
# --------------------------------------------------------------------------- #
def test_parse_result_merges_consecutive_same_speaker() -> None:
    data = {
        "properties": {"original_duration_in_milliseconds": 20000},
        "transcripts": [
            {
                "sentences": [
                    {"speaker_id": 0, "begin_time": 0, "end_time": 3000, "text": "第一句。"},
                    {"speaker_id": 0, "begin_time": 3000, "end_time": 6000, "text": "第二句。"},
                    {"speaker_id": 1, "begin_time": 6000, "end_time": 9000, "text": "换人了。"},
                ]
            }
        ],
    }
    res = transcribe.parse_result(data)
    assert len(res["sentences"]) == 3
    assert len(res["utterances"]) == 2, "同一人的连续句子要并成一段"
    assert res["utterances"][0]["text"] == "第一句。第二句。"
    assert res["utterances"][0]["end_ms"] == 6000
    assert res["speakers"] == [0, 1]
    assert res["duration_ms"] == 20000


def test_parse_result_skips_empty_and_sorts_by_time() -> None:
    data = {
        "transcripts": [
            {
                "sentences": [
                    {"speaker_id": 1, "begin_time": 9000, "end_time": 12000, "text": "后面的。"},
                    {"speaker_id": 0, "begin_time": 1000, "end_time": 4000, "text": "   "},
                    {"speaker_id": 0, "begin_time": 2000, "end_time": 5000, "text": "前面的。"},
                ]
            }
        ]
    }
    res = transcribe.parse_result(data)
    assert [s["text"] for s in res["sentences"]] == ["前面的。", "后面的。"]
    assert res["utterances"][0]["begin_ms"] == 2000


def test_speaker_label_never_invents_names() -> None:
    """声纹只知道「是不是同一个人」，不知道是谁——编号就是编号。"""
    assert transcribe.speaker_label(0) == "发言人1"
    assert transcribe.speaker_label(2) == "发言人3"
    assert transcribe.speaker_label(None) == "发言人"


# --------------------------------------------------------------------------- #
# 分离可信度：只标注，不改判
# --------------------------------------------------------------------------- #
def test_fragile_flags_catches_the_real_split() -> None:
    """真实那段：主持人 2.6s 说完、3 秒的短片段、主持人接着说 —— 就是它。"""
    flags = transcribe.fragile_flags(_REAL_UTTERANCES)
    assert sum(1 for f in flags if f) == 1
    assert flags[1] is True, "第 2 段（3 秒夹在同一个人的两段之间）应被判为存疑"
    assert not any(flags[i] for i in (0, 2, 3, 14))


def test_fragile_flags_ignores_long_turns_between_same_speaker() -> None:
    """A → B（20 秒，正常发言）→ A 不算存疑：那是真的轮换，不是被切开。"""
    utt = [
        {"speaker": 0, "begin_ms": 0, "end_ms": 30000, "text": "A 说。"},
        {"speaker": 1, "begin_ms": 30000, "end_ms": 50000, "text": "B 说了一大段。"},
        {"speaker": 0, "begin_ms": 50000, "end_ms": 80000, "text": "A 接着说。"},
    ]
    assert transcribe.fragile_flags(utt) == [False, False, False]


def test_fragile_flags_ignores_gap_between_turns() -> None:
    """短片段与两侧有明显间隔 → 是独立插话，不是被切开的碎片。"""
    utt = [
        {"speaker": 0, "begin_ms": 0, "end_ms": 30000, "text": "A 说。"},
        {"speaker": 1, "begin_ms": 45000, "end_ms": 47000, "text": "嗯。"},
        {"speaker": 0, "begin_ms": 60000, "end_ms": 80000, "text": "A 接着说。"},
    ]
    assert transcribe.fragile_flags(utt) == [False, False, False]


def test_format_transcript_marks_uncertain_line_only() -> None:
    text = transcribe.format_transcript(_REAL_UTTERANCES)
    lines = text.splitlines()
    assert len(lines) == len(_REAL_UTTERANCES)
    assert "（短促片段·归属存疑）" in lines[1]
    assert lines[0].startswith("[00:02] 发言人1：")
    assert "归属存疑" not in lines[2]
    # 关掉标注时正文一字不变（给模型的那份不带标注也行）
    plain = transcribe.format_transcript(_REAL_UTTERANCES, mark_uncertain=False)
    assert "归属存疑" not in plain


def test_diarization_report_counts_speakers_and_suspects() -> None:
    report = transcribe.diarization_report(_REAL_UTTERANCES)
    assert report["speakers"] == 6, "实测就是 6 类（真实 7 人，服务端合了一类）"
    assert report["fragile_count"] == 1
    assert report["fragile_index"] == [1]
    line = transcribe.describe_diarization(report)
    assert "共识别出 6 位发言人" in line
    assert "归属存疑" in line


def test_diarization_report_flags_too_short_speaker() -> None:
    """只说了 2 秒的「发言人」大概率是误分——报告里要点出来。"""
    utt = [
        {"speaker": 0, "begin_ms": 0, "end_ms": 60000, "text": "主持人说了一大段。"},
        {"speaker": 1, "begin_ms": 60000, "end_ms": 62000, "text": "嗯嗯。"},
        {"speaker": 0, "begin_ms": 70000, "end_ms": 130000, "text": "主持人接着说。"},
    ]
    report = transcribe.diarization_report(utt)
    assert [s["label"] for s in report["suspect"]] == ["发言人2"]
    assert "发言过少" in transcribe.describe_diarization(report)


# --------------------------------------------------------------------------- #
# 定制热词
# --------------------------------------------------------------------------- #
def test_load_hot_words_defaults_when_file_missing() -> None:
    tmp = _fresh()
    words = transcribe.load_hot_words()
    assert "具身智能" in words and "ChatGPT" in words
    assert not (tmp / "hotwords.txt").exists(), "不偷偷创建文件"


def test_load_hot_words_reads_file_and_skips_comments() -> None:
    tmp = _fresh()
    p = tmp / "hotwords.txt"
    p.write_text(
        "# 会议领域词\n具身智能\n\n  ChatGPT  \n具身智能\n# 注释行\n",
        encoding="utf-8",
    )
    assert transcribe.load_hot_words() == ["具身智能", "ChatGPT"], "去重、去空行、去注释"


def test_clean_hot_words_drops_over_long_entries() -> None:
    """接口限制：中文不超过 15 字、纯英文空格分段不超过 7 段。"""
    words = transcribe.clean_hot_words(
        ["具身智能", "一二三四五六七八九十一二三四五六", "scaling law", "a b c d e f g h"]
    )
    assert words == ["具身智能", "scaling law"]


def test_ensure_vocabulary_reuses_cache_until_words_change() -> None:
    """词表不变就复用同一个 vocabulary_id；改了词表才重建。"""
    tmp = _fresh()
    calls: list[list[str]] = []

    def fake_create(words, **kw):
        calls.append(list(words))
        return f"vocab-test-{len(calls)}"

    original = transcribe.create_vocabulary
    transcribe.create_vocabulary = fake_create
    try:
        first = transcribe.ensure_vocabulary(["具身智能", "ChatGPT"], cache_path=tmp / "c.json")
        second = transcribe.ensure_vocabulary(["ChatGPT", "具身智能"], cache_path=tmp / "c.json")
        assert first == second == "vocab-test-1", "词表内容相同（顺序不同）不该重建"
        assert len(calls) == 1
        third = transcribe.ensure_vocabulary(["具身智能"], cache_path=tmp / "c.json")
        assert third == "vocab-test-2" and len(calls) == 2, "词表变了必须重建"
        rec = json.loads((tmp / "c.json").read_text(encoding="utf-8"))
        assert rec["vocabulary_id"] == "vocab-test-2"
    finally:
        transcribe.create_vocabulary = original


def test_ensure_vocabulary_raises_instead_of_silently_skipping() -> None:
    """建表失败必须抛出去：以为加了热词其实没加，会让所有效果对比都不可信。"""
    tmp = _fresh()

    def boom(words, **kw):
        raise transcribe.TranscriptionError("模拟建表失败")

    original = transcribe.create_vocabulary
    transcribe.create_vocabulary = boom
    try:
        try:
            transcribe.ensure_vocabulary(["具身智能"], cache_path=tmp / "c.json")
        except transcribe.TranscriptionError as e:
            assert "模拟建表失败" in str(e)
        else:
            raise AssertionError("应当抛出 TranscriptionError")
    finally:
        transcribe.create_vocabulary = original


def test_submit_sends_hotwords_and_language_hints() -> None:
    """提交参数必须真的带上 vocabulary_id / language_hints（不打桩就看不出漏参）。"""
    captured: dict = {}

    class FakeResp:
        status_code = 200

        @staticmethod
        def json():
            return {"output": {"task_id": "t-1"}}

    class FakeReq:
        @staticmethod
        def post(url, headers=None, json=None, timeout=None):  # noqa: A002
            captured.update(json or {})
            return FakeResp()

    original = transcribe._requests
    transcribe._requests = lambda: FakeReq
    try:
        tid = transcribe.submit("oss://x/a.mp3", vocabulary_id="vocab-1", speaker_count=7)
    finally:
        transcribe._requests = original

    assert tid == "t-1"
    params = captured["parameters"]
    assert params["vocabulary_id"] == "vocab-1"
    assert params["speaker_count"] == 7
    assert params["diarization_enabled"] is True
    assert params["language_hints"] == ["zh", "en"]


def test_submit_omits_optional_params_by_default() -> None:
    captured: dict = {}

    class FakeResp:
        status_code = 200

        @staticmethod
        def json():
            return {"output": {"task_id": "t-2"}}

    class FakeReq:
        @staticmethod
        def post(url, headers=None, json=None, timeout=None):  # noqa: A002
            captured.update(json or {})
            return FakeResp()

    original = transcribe._requests
    transcribe._requests = lambda: FakeReq
    try:
        transcribe.submit("oss://x/a.mp3")
    finally:
        transcribe._requests = original

    params = captured["parameters"]
    assert "vocabulary_id" not in params, "没给热词就不该塞空字段"
    assert "speaker_count" not in params, "speaker_count 只是参考值，默认不传"


# --------------------------------------------------------------------------- #
# 整条链路：会议节点有没有把热词表带下去、有没有如实报告分离质量
# --------------------------------------------------------------------------- #
def test_meeting_node_passes_hotwords_and_reports_quality() -> None:
    from lg_assistant import llm, nodes

    seen: dict = {}
    prompts: list = []

    def fake_ensure(words=None, **kw):
        return "vocab-1"

    def fake_transcribe(path, *, speaker_count=None, vocabulary_id=None):
        seen["vocabulary_id"] = vocabulary_id
        seen["path"] = str(path)
        return {
            "sentences": [],
            "utterances": _REAL_UTTERANCES,
            "speakers": [0, 1, 2, 3, 4, 5],
            "duration_ms": 645760,
            "text": "",
        }

    def fake_chat(messages, **kw):
        prompts.append(messages)
        return "## 会议纪要\n\n- 讨论了拆gpt 时刻。"

    originals = (transcribe.ensure_vocabulary, transcribe.transcribe_with_speakers, llm.chat)
    transcribe.ensure_vocabulary = fake_ensure
    transcribe.transcribe_with_speakers = fake_transcribe
    llm.chat = fake_chat
    try:
        out = nodes.meeting_audio(
            {"files": ["E:/tmp/roundtable.mp3"], "text": "会议纪要整理",
             "routing": {"scene": "meeting"}}
        )
    finally:
        (transcribe.ensure_vocabulary, transcribe.transcribe_with_speakers, llm.chat) = originals

    assert seen["vocabulary_id"] == "vocab-1", "热词表 ID 必须真的传到转写调用上"
    prompt = prompts[0][-1]["content"]
    assert "短促片段·归属存疑" in prompt, "存疑标注要进入整理提示，否则纪照样会算错人"
    text = out["result"]["text"]
    assert "## 会议文字记录 （共识别出 6 位发言人" in text
    assert "1 个短促片段归属存疑" in text
    assert "[00:09] 发言人2（短促片段·归属存疑）" in text
    report = [a for a in out["result"]["artifacts"] if a["kind"] == "speaker_stats"][0]
    assert len(report["stats"]) == 6


def test_meeting_node_survives_hotword_failure() -> None:
    """热词表建不出来（比如子业务空间不支持）也必须照转，只记一条警告。"""
    from lg_assistant import llm, nodes

    def boom(words=None, **kw):
        raise transcribe.TranscriptionError("模拟：仅主业务空间支持热词")

    def fake_transcribe(path, *, speaker_count=None, vocabulary_id=None):
        assert vocabulary_id is None, "建表失败就不该传空 ID 下去"
        return {"sentences": [], "utterances": _REAL_UTTERANCES, "speakers": [0], "duration_ms": 0, "text": ""}

    originals = (transcribe.ensure_vocabulary, transcribe.transcribe_with_speakers, llm.chat)
    transcribe.ensure_vocabulary = boom
    transcribe.transcribe_with_speakers = fake_transcribe
    llm.chat = lambda messages, **kw: "## 纪要"
    try:
        out = nodes.meeting_audio(
            {"files": ["E:/tmp/roundtable.mp3"], "text": "会议纪要整理",
             "routing": {"scene": "meeting"}}
        )
    finally:
        (transcribe.ensure_vocabulary, transcribe.transcribe_with_speakers, llm.chat) = originals

    text = out["result"]["text"]
    assert "会议文字记录" in text
    assert "热词表未生效" in text and "未处理的部分" in text


# --------------------------------------------------------------------------- #
# 人工改判：纯函数部分
# --------------------------------------------------------------------------- #
def test_record_split_pulls_one_segment_out() -> None:
    """用户看到的那个错：罗荣青 8 秒的自我介绍被算成王浩那一类。"""
    rec = transcribe.new_record(_REAL_UTTERANCES, summary="## 纪要")
    assert [s["label"] for s in transcribe.record_speakers(rec)][0] == "发言人3"  # 王浩（时长最长）

    edited = transcribe.apply_edit(rec, "rename", speaker=2, name="王浩")
    edited = transcribe.apply_edit(edited, "split", index=6)          # 罗荣青那段
    edited = transcribe.apply_edit(edited, "rename", speaker=edited["next_speaker"] - 1, name="罗荣青")

    labels = [s["label"] for s in transcribe.record_speakers(edited)]
    assert "王浩" in labels and "罗荣青" in labels
    assert len(edited["utterances"]) == len(_REAL_UTTERANCES), "只改归属，不多不少"
    text = transcribe.format_record(edited)
    assert "王浩：大家好啊，我罗荣青" not in text
    assert "罗荣青：大家好啊，我罗荣青" in text
    assert "王浩：我是银河通用的创始人" in text


def test_record_merge_folds_and_remerges_neighbours() -> None:
    """合并之后同一个人的前后两段要重新贴回一段，否则正文会出现连续两行同一人。"""
    rec = transcribe.new_record([
        {"speaker": 0, "begin_ms": 0, "end_ms": 5000, "text": "A 前。"},
        {"speaker": 1, "begin_ms": 5000, "end_ms": 8000, "text": "B 插一句。"},
        {"speaker": 0, "begin_ms": 8000, "end_ms": 12000, "text": "A 后。"},
    ])
    edited = transcribe.apply_edit(rec, "merge", source=1, target=0)
    assert len(edited["utterances"]) == 1
    assert edited["utterances"][0]["text"] == "A 前。B 插一句。A 后。"
    assert edited["utterances"][0]["end_ms"] == 12000


def test_record_merge_rejects_self_and_missing() -> None:
    rec = transcribe.new_record(_REAL_UTTERANCES)
    for op, kw in (("merge", {"source": 1, "target": 1}), ("merge", {"source": 9, "target": 0})):
        try:
            transcribe.apply_edit(rec, op, **kw)
        except ValueError:
            continue
        raise AssertionError(f"{op} {kw} 应当被拒绝")


def test_record_reassign_to_new_speaker() -> None:
    rec = transcribe.new_record(_REAL_UTTERANCES)
    edited = transcribe.apply_edit(rec, "reassign", index=6, speaker="new")
    assert edited["next_speaker"] == rec["next_speaker"] + 1
    assert edited["utterances"][6]["speaker"] == rec["next_speaker"]


def test_record_reset_restores_original() -> None:
    rec = transcribe.new_record(_REAL_UTTERANCES)
    edited = transcribe.apply_edit(rec, "rename", speaker=0, name="主持人")
    edited = transcribe.apply_edit(edited, "split", index=1)
    back = transcribe.apply_edit(edited, "reset")
    assert back["names"] == {}
    assert back["utterances"] == [dict(u) for u in _REAL_UTTERANCES]
    # 按发言时长排序：王浩(288.7s) > 主持人(151.9s) > 罗建南(145.7s) > 沐瑶 > 高阳 > 丁文超
    assert [s["label"] for s in transcribe.record_speakers(back)] == [
        "发言人3", "发言人1", "发言人2", "发言人5", "发言人4", "发言人6",
    ]


def test_record_edit_does_not_mutate_input() -> None:
    """纯函数：编辑返回新记录，原记录一个字都不许变（前端靠这个做撤销）。"""
    rec = transcribe.new_record(_REAL_UTTERANCES)
    before = json.dumps(rec, ensure_ascii=False, sort_keys=True)
    transcribe.apply_edit(rec, "split", index=3)
    transcribe.apply_edit(rec, "rename", speaker=0, name="主持人")
    assert json.dumps(rec, ensure_ascii=False, sort_keys=True) == before


def test_record_unknown_action_rejected() -> None:
    rec = transcribe.new_record(_REAL_UTTERANCES)
    try:
        transcribe.apply_edit(rec, "explode")
    except ValueError as e:
        assert "不认识的操作" in str(e)
    else:
        raise AssertionError("未知操作应当报错")


def test_transcript_store_roundtrip() -> None:
    tmp = _fresh()
    store.init()
    rec = transcribe.new_record(_REAL_UTTERANCES, summary="## 纪要")
    store.save_transcript("local", "rid-1", scene="meeting", record=rec)
    got = store.get_transcript("local", "rid-1")
    assert got and got["summary"] == "## 纪要"
    assert len(got["utterances"]) == len(_REAL_UTTERANCES)
    assert got["next_speaker"] == rec["next_speaker"]
    assert store.get_transcript("local", "nope") is None
    # 覆盖写：改判后再存，读到的是新内容
    edited = transcribe.apply_edit(rec, "rename", speaker=0, name="主持人")
    store.save_transcript("local", "rid-1", scene="meeting", record=edited)
    assert store.get_transcript("local", "rid-1")["names"] == {"0": "主持人"}
    assert tmp.exists()


# --------------------------------------------------------------------------- #
# 人工改判：HTTP 层（Flask test client，不联网）
# --------------------------------------------------------------------------- #
_ORIGINAL_TOKEN: str | None = None


def _client():
    """每套测试一个独立库 + 桩图。**显式关掉鉴权**（.env 里可能设了口令）。"""
    global _ORIGINAL_TOKEN
    from lg_assistant import graph
    from lg_assistant.web.app import create_app

    _ORIGINAL_TOKEN = config.ACCESS_TOKEN
    config.ACCESS_TOKEN = ""
    cp = graph.open_checkpointer(config.DATA_DIR / "ck.sqlite3")
    app = create_app(graph.build_graph(cp))
    app.config["TESTING"] = True
    return app.test_client()


def _restore_token() -> None:
    global _ORIGINAL_TOKEN
    if _ORIGINAL_TOKEN is not None:
        config.ACCESS_TOKEN = _ORIGINAL_TOKEN


def _seed_meeting() -> tuple[str, dict]:
    """造一份「已归档的会议产出 + 转写记录」，等价于跑完一次上传录音。"""
    from lg_assistant import nodes

    rec = transcribe.new_record(_REAL_UTTERANCES, summary="## 会议纪要\n\n- 讨论了拆gpt 时刻。")
    report = transcribe.describe_diarization(transcribe.diarization_report(rec["utterances"]))
    rid = store.archive(
        owner="local", scene="meeting", title="meeting·产出",
        content=nodes.meeting_body(rec["summary"], report, transcribe.format_record(rec)),
        source="录音",
    )
    store.save_transcript("local", rid, scene="meeting", record=rec)
    return rid, rec


def test_api_transcript_get_and_edit_flow() -> None:
    _fresh()
    store.init()
    rid, rec = _seed_meeting()
    client = _client()
    try:
        got = client.get(f"/api/transcript?owner=local&id={rid}").get_json()
        assert got["id"] == rid
        assert len(got["utterances"]) == len(_REAL_UTTERANCES)
        assert got["speakers"][0]["label"] == "发言人3"
        assert got["fragile"][1] is True
        assert "短促片段·归属存疑" in got["text"]

        # 改名
        r = client.post("/api/transcript", data={"owner": "local", "id": rid,
                                                 "action": "rename", "speaker": 2, "name": "王浩"})
        assert r.status_code == 200
        assert "王浩" in r.get_json()["text"]

        # 拆出罗荣青
        r = client.post("/api/transcript", data={"owner": "local", "id": rid,
                                                 "action": "split", "index": 6})
        body = r.get_json()
        assert len(body["speakers"]) == 7, "6 类里拆出一个新的"
        assert "发言人7：大家好啊，我罗荣青" in body["text"]
        assert "发言人7" in body["report"]

        # 归档正文跟着变（导出的就是它）
        row = store.get_resource("local", rid)
        assert "发言人7：大家好啊，我罗荣青" in row["content"]
        assert "王浩" in row["content"]

        # 恢复
        body = client.post("/api/transcript", data={"owner": "local", "id": rid,
                                                    "action": "reset"}).get_json()
        assert "发言人7" not in body["text"]
        assert "发言人3：大家好啊，我罗荣青" in body["text"]

        # 资源接口要告诉前端「这份产出可以改判」
        assert client.get(f"/api/resource?owner=local&id={rid}").get_json()["has_transcript"] is True
    finally:
        _restore_token()


def test_api_transcript_merge_and_errors() -> None:
    _fresh()
    store.init()
    rid, _ = _seed_meeting()
    client = _client()
    try:
        body = client.post("/api/transcript", data={"owner": "local", "id": rid,
                                                    "action": "merge", "source": 1, "target": 0}).get_json()
        assert len(body["speakers"]) == 5
        assert "发言人2" not in body["text"]

        bad = client.post("/api/transcript", data={"owner": "local", "id": rid, "action": "merge",
                                                   "source": 0, "target": 0})
        assert bad.status_code == 400 and "自己" in bad.get_json()["error"]

        missing = client.post("/api/transcript", data={"owner": "local", "id": "nope", "action": "reset"})
        assert missing.status_code == 404
        assert client.get("/api/transcript?id=nope").status_code == 404
    finally:
        _restore_token()


def test_api_resummarize_rebuilds_resource_with_stub_model() -> None:
    """改判后重算纪要：用同一套提示词，重算结果要落回归档正文。"""
    from lg_assistant import llm, nodes

    _fresh()
    store.init()
    rid, _ = _seed_meeting()
    client = _client()
    prompts: list = []
    original = llm.chat
    llm.chat = lambda messages, **kw: (prompts.append(messages), "## 新纪要\n\n- 王浩说了 WAM。")[1]
    try:
        client.post("/api/transcript", data={"owner": "local", "id": rid,
                                             "action": "rename", "speaker": 2, "name": "王浩"})
        body = client.post("/api/transcript", data={"owner": "local", "id": rid,
                                                    "action": "resummarize"}).get_json()
        assert "新纪要" in body["summary"]
        assert "新纪要" in store.get_resource("local", rid)["content"]
        assert "王浩" in store.get_resource("local", rid)["content"]
        # 提示词必须与会议链路一致，否则两份纪要对不上
        assert nodes.MEETING_INSTRUCTION in prompts[0][-1]["content"]
        assert "王浩：" in prompts[0][-1]["content"], "重算要用改判后的正文"
    finally:
        llm.chat = original
        _restore_token()


# --------------------------------------------------------------------------- #
def _run_all() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL  {fn.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  ERROR {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass
    raise SystemExit(_run_all())
