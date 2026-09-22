"""集中配置：全项目唯一的配置入口，全部从 .env 读取。"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent  # assistant-lite/
load_dotenv(ROOT / ".env")

# ---------- 模型 ----------
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY", "").strip()
BASE_URL = os.getenv(
    "DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
).strip()
MODEL_TEXT = os.getenv("MODEL_TEXT", "qwen-plus").strip()
MODEL_VISION = os.getenv("MODEL_VISION", "qwen-vl-max").strip()
MODEL_ASR = os.getenv("MODEL_ASR", "qwen3-asr-flash").strip()
REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "120"))

# ---------- 路径 ----------
DATA_DIR = Path(os.getenv("DATA_DIR", str(ROOT / "data")))
DB_PATH = DATA_DIR / "sessions.sqlite3"
PROFILE_DIR = DATA_DIR / "profiles"
UPLOAD_DIR = DATA_DIR / "uploads"
EXPORT_DIR = DATA_DIR / "exports"

# ---------- 业务上限（沿用旧 service.py 已验证的规则） ----------
MEETING_CHAR_LIMIT = 48000  # 单场会议转写字符上限
VISION_MAX_EDGE = 2048  # 送模型前图片长边上限
ASR_CHUNK_SECONDS = 240  # 长音频分片长度（秒）

# ---------- 外部工具 ----------
# 约定：新增安装物一律放 E 盘，不占系统盘。
TOOLS_DIR = Path(os.getenv("TOOLS_DIR", r"E:\AI智能助手\tools"))
#: ffmpeg 可执行文件路径。留空则按 TOOLS_DIR/ffmpeg/bin -> PATH 顺序自动查找。
FFMPEG_PATH = os.getenv("FFMPEG_PATH", "").strip()

SCENES = ("meeting", "exam", "fitness", "resource", "general")


def ensure_dirs() -> None:
    """创建全部运行时目录。幂等。"""
    for d in (DATA_DIR, PROFILE_DIR, UPLOAD_DIR, EXPORT_DIR):
        d.mkdir(parents=True, exist_ok=True)


def problems() -> list[str]:
    """返回配置问题列表；空列表表示配置可用。"""
    out: list[str] = []
    if not DASHSCOPE_API_KEY:
        out.append("DASHSCOPE_API_KEY 未设置：请复制 .env.example 为 .env 并填入真实 Key")
    elif not DASHSCOPE_API_KEY.startswith("sk-"):
        out.append("DASHSCOPE_API_KEY 格式可疑：百炼 Key 通常以 sk- 开头")
    if not BASE_URL.startswith("http"):
        out.append(f"DASHSCOPE_BASE_URL 不是合法 URL：{BASE_URL!r}")
    return out
