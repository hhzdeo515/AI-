"""集中配置。本应用独立于 assistant-lite：自己的 .env、自己的数据目录。"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent  # langgraph-app/
load_dotenv(ROOT / ".env")

# ---------- 模型（沿用百炼 OpenAI 兼容接口）----------
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY", "").strip()
BASE_URL = os.getenv(
    "DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
).strip()
MODEL_TEXT = os.getenv("MODEL_TEXT", "qwen-plus").strip()
MODEL_VISION = os.getenv("MODEL_VISION", "qwen-vl-max").strip()
MODEL_ASR = os.getenv("MODEL_ASR", "qwen3-asr-flash").strip()
#: 语音合成。只用 tts_v2(cosyvoice)——sambert 那套在百炼新账号上返回空数据。
MODEL_TTS = os.getenv("MODEL_TTS", "cosyvoice-v1").strip()
TTS_VOICE = os.getenv("TTS_VOICE", "longxiaochun").strip()
REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "120"))

# ---------- 路径 ----------
DATA_DIR = Path(os.getenv("DATA_DIR", str(ROOT / "data")))
#: LangGraph checkpointer 的 SQLite 文件。断点续跑靠它，**不要放进临时目录**。
CHECKPOINT_DB = Path(os.getenv("CHECKPOINT_DB", str(DATA_DIR / "checkpoints.sqlite3")))
#: 路由遥测与资料的 SQLite 文件（沿用 assistant-lite 的表结构）
DB_PATH = DATA_DIR / "app.sqlite3"
#: 导出产物目录
EXPORT_DIR = Path(os.getenv("EXPORT_DIR", str(DATA_DIR / "exports")))
#: 上传附件目录
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", str(DATA_DIR / "uploads")))
#: Web 层静态资源与模板目录
WEB_DIR = Path(__file__).resolve().parent / "web"

# ---------- 访问口令 ----------
#: 设置后 Web 层启用口令鉴权（局域网/公网暴露时必须设）。
#: 留空 = 不鉴权，仅适合本机单机使用。
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN", "").strip()
#: 监听地址。127.0.0.1 只允许本机；0.0.0.0 允许局域网访问。
WEB_HOST = os.getenv("WEB_HOST", "127.0.0.1").strip()
WEB_PORT = int(os.getenv("WEB_PORT", "8802"))

# ---------- 外部工具 ----------
#: 外部安装物统一放 E 盘（沿用 assistant-lite 的约定）
TOOLS_DIR = Path(os.getenv("TOOLS_DIR", r"E:\AI智能助手\tools"))
#: ffmpeg 可执行文件。留空则按 TOOLS_DIR/ffmpeg/bin -> PATH 顺序查找。
FFMPEG_PATH = os.getenv("FFMPEG_PATH", "").strip()
#: 长音频分片长度（秒）。ASR 单次调用有时长上限，长会议必须切。
ASR_CHUNK_SECONDS = int(os.getenv("ASR_CHUNK_SECONDS", "240"))
#: 单次语音合成的文本上限（播报语本该很短）
SPEECH_MAX_CHARS = int(os.getenv("SPEECH_MAX_CHARS", "300"))

# ---------- 执行后端 ----------
#: python = 本地确定性/场景节点；dify = 转发给 Dify 工作流（dify-multiagent/）
EXEC_BACKEND = os.getenv("EXEC_BACKEND", "python").strip().lower()
DIFY_BASE_URL = os.getenv("DIFY_BASE_URL", "http://127.0.0.1/v1").strip().rstrip("/")
DIFY_API_KEY = os.getenv("DIFY_API_KEY", "").strip()
DIFY_TIMEOUT = float(os.getenv("DIFY_TIMEOUT", "300"))
DIFY_FALLBACK_LOCAL = os.getenv("DIFY_FALLBACK_LOCAL", "1").strip() not in ("0", "false", "False")

# ---------- 端侧契约（见 docs/端侧适配设计_眼镜.md §3）----------
#: 端侧 1–3B 判定可信阈值。低于此值只作证据、不作提示。
DEVICE_CONFIDENCE_FLOOR = float(os.getenv("DEVICE_CONFIDENCE_FLOOR", "0.75"))
#: 安全相关意图永不降级（与锻炼场景的「不适即停」一致）
DEVICE_NEVER_DOWNGRADE = frozenset({"train_pain"})

SCENES = ("meeting", "exam", "fitness", "resource", "general")


def ensure_dirs() -> None:
    """创建全部运行时目录。幂等。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def problems() -> list[str]:
    """返回配置问题列表；空列表表示可用。"""
    out: list[str] = []
    if not DASHSCOPE_API_KEY:
        out.append("DASHSCOPE_API_KEY 未设置：复制 .env.example 为 .env 并填入真实 Key")
    elif not DASHSCOPE_API_KEY.startswith("sk-"):
        out.append("DASHSCOPE_API_KEY 格式可疑：百炼 Key 通常以 sk- 开头")
    if EXEC_BACKEND not in ("python", "dify"):
        out.append(f"EXEC_BACKEND 只能是 python 或 dify，当前 {EXEC_BACKEND!r}")
    if EXEC_BACKEND == "dify" and not DIFY_API_KEY:
        out.append("EXEC_BACKEND=dify 但 DIFY_API_KEY 为空")
    return out
