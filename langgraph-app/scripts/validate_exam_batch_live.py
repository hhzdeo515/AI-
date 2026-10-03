"""Build deterministic multi-question photos; opt in to an async HTTP smoke test.

Fixture answers are used only after the application responds. They are never
sent to any model, retrieval corpus, or API request. Generation is offline by
default; --live makes the explicitly requested application call.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys
import time
import uuid

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "outputs" / "exam-batch-20260930"
FONT = Path("C:/Windows/Fonts/msyh.ttc")
EXPECTED = {
    "mixed": {"count": 3, "answered_count": 2, "needs_photo_count": 1, "answers": {"1": "B", "2": "A"}, "partial": ["3"]},
    "shared": {"count": 2, "answered_count": 2, "needs_photo_count": 0, "answers": {"1": "C", "2": "A"}, "partial": []},
    "many": {"count": 6, "answered_count": 6, "needs_photo_count": 0, "answers": {"1": "B", "2": "C", "3": "D", "4": "A", "5": "C", "6": "B"}, "partial": []},
}


def font(size):
    if not FONT.is_file():
        raise RuntimeError("需要 Windows 微软雅黑字体：" + str(FONT))
    return ImageFont.truetype(str(FONT), size=size)


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def text(draw, xy, value, size=42, fill="#111827"):
    draw.text(xy, value, font=font(size), fill=fill)


def choices(draw, x, y, rows, size=42, gap=70):
    for index, value in enumerate(rows):
        text(draw, (x, y + index * gap), value, size)


def mixed():
    full = Image.new("RGB", (1500, 1560), "white")
    draw = ImageDraw.Draw(full)
    text(draw, (70, 40), "单项选择题", 48)
    text(draw, (70, 155), "1. 某车间今年生产240件零件，比去年增长20%。")
    text(draw, (120, 225), "去年生产了多少件？")
    choices(draw, 120, 330, ["A. 180件                 B. 200件", "C. 220件                 D. 288件"])
    draw.line((70, 500, 1430, 500), fill="#CBD5E1", width=2)
    text(draw, (70, 555), "2. 轮胎：汽车")
    text(draw, (120, 625), "选出与题干词语关系相同的一项。")
    choices(draw, 120, 730, ["A. 叶片：树              B. 灯泡：玻璃", "C. 园丁：花园            D. 钥匙：开门"])
    draw.line((70, 900, 1430, 900), fill="#CBD5E1", width=2)
    text(draw, (70, 1010), "3. 某班共有40人，其中男生占60%。")
    text(draw, (120, 1080), "女生有多少人？")
    choices(draw, 120, 1170, ["A. 12人", "B. 16人", "C. 24人", "D. 28人"], gap=90)
    # B is visibly cut by the physical bottom edge; C and D are outside the photo.
    return full.crop((0, 0, 1500, 1284))


def shared():
    image = Image.new("RGB", (1500, 1700), "white")
    draw = ImageDraw.Draw(image)
    text(draw, (70, 40), "根据下列材料回答第1至2题", 48)
    text(draw, (100, 155), "某公司2024年各季度销售额", 44)
    text(draw, (1000, 220), "单位：万元", 36)
    columns = [100, 650, 1350]
    ys = [300, 390, 480, 570, 660, 750]
    for y in ys:
        draw.line((100, y, 1350, y), fill="#111827", width=3)
    for x in columns:
        draw.line((x, 300, x, 750), fill="#111827", width=3)
    rows = [("季度", "销售额"), ("第1季度", "120"), ("第2季度", "150"), ("第3季度", "180"), ("第4季度", "210")]
    for y, row in zip(ys, rows):
        text(draw, (130, y + 20), row[0], 40)
        text(draw, (700, y + 20), row[1], 40)
    text(draw, (70, 840), "1. 第2季度销售额比第1季度增长多少？")
    choices(draw, 120, 945, ["A. 10%                  B. 15%", "C. 25%                  D. 30%"])
    text(draw, (70, 1210), "2. 2024年全年销售额为多少万元？")
    choices(draw, 120, 1315, ["A. 660万元              B. 720万元", "C. 600万元              D. 540万元"])
    return image


def many():
    image = Image.new("RGB", (1600, 1740), "white")
    draw = ImageDraw.Draw(image)
    text(draw, (60, 35), "单项选择题", 44)
    draw.line((800, 120, 800, 1690), fill="#CBD5E1", width=2)
    questions = [
        (["1. 今年产量120件，比去年增长20%。", "去年产量是多少件？"], ["A. 90件       B. 100件", "C. 110件      D. 144件"]),
        (["2. 80人平均分成5组。", "每组有多少人？"], ["A. 8人        B. 12人", "C. 16人       D. 20人"]),
        (["3. 买3件商品，每件18元。", "总价是多少元？"], ["A. 21元       B. 36元", "C. 45元       D. 54元"]),
        (["4. 全班50人，女生占40%。", "女生有多少人？"], ["A. 20人       B. 25人", "C. 30人       D. 40人"]),
        (["5. 仓库原有72箱货，取走24箱。", "还剩多少箱？"], ["A. 24箱       B. 36箱", "C. 48箱       D. 96箱"]),
        (["6. 长方形长8厘米，宽5厘米。", "它的面积是多少平方厘米？"], ["A. 13         B. 40", "C. 26         D. 80"]),
    ]
    for index, (stem, options) in enumerate(questions):
        x = 60 + (index % 2) * 800
        y = 160 + (index // 2) * 510
        choices(draw, x, y, stem, size=34, gap=64)
        choices(draw, x + 35, y + 190, options, size=34, gap=75)
    return image


def generate(case):
    OUTPUT.mkdir(parents=True, exist_ok=True)
    path = OUTPUT / f"{case}.png"
    {"mixed": mixed, "shared": shared, "many": many}[case]().save(path)
    manifest = {"case": case, "image": str(path), "expected": EXPECTED[case],
                "scope": "人工构造的可控冒烟测试，不是独立真实试题准确率评测。",
                "answers_used_only_after_response": True}
    write_json(OUTPUT / f"{case}-fixture.json", manifest)
    return path


def validate(case, result):
    expected = EXPECTED[case]
    artifacts = result.get("artifacts") or []
    summary = next((a for a in artifacts if a.get("kind") == "exam_batch"), None)
    errors = []
    if summary is None:
        return {"passed": False, "errors": ["响应缺少 exam_batch 清单"], "actual": None}
    for key in ("count", "answered_count", "needs_photo_count"):
        if summary.get(key) != expected[key]:
            errors.append(f"{key}: 期望 {expected[key]}，实际 {summary.get(key)}")
    rows = summary.get("questions") or []
    if len(rows) != expected["count"]:
        errors.append(f"逐题行数应为 {expected['count']}，实际 {len(rows)}")
    by_number = {}
    for row in rows:
        match = re.search(r"第\s*(\d+)\s*题", str(row.get("label", "")))
        if match:
            number = match.group(1)
            if number in by_number:
                errors.append(f"题号 {number} 被重复输出")
            by_number[number] = row
    for number, key in expected["answers"].items():
        row = by_number.get(number, {})
        match = re.match(r"\s*[（(]?([A-H])(?=[\s（(、，:：.)]|$)", str(row.get("answer", "")))
        if row.get("status") != "answered" or not match or match.group(1) != key:
            errors.append(f"第{number}题应为已解答且选择 {key}，实际 {row.get('status')} / {row.get('answer')}")
    for number in expected["partial"]:
        row = by_number.get(number, {})
        if row.get("status") != "needs_photo" or row.get("answer") or not row.get("needed"):
            errors.append(f"第{number}题应单独提醒补拍，且答案为空")
    return {"passed": not errors, "errors": errors, "actual": summary}


def live(args, image):
    import requests
    base = args.url.rstrip("/")
    session = requests.Session()
    token = os.environ.get("EXAM_BATCH_ACCESS_TOKEN", "").strip()
    if token:
        session.headers["Authorization"] = "Bearer " + token
    submission_path = OUTPUT / f"{args.case}-{args.backend}-submission.json"
    metadata = json.loads(submission_path.read_text(encoding="utf-8")) if submission_path.is_file() else {}
    task_id = args.task_id or (metadata.get("task_id") if metadata.get("status") in {"pending", "running"} and metadata.get("url") == base else "")
    if not task_id:
        request_id = "batch-" + uuid.uuid4().hex[:16]
        # Only the original image and user choices are uploaded. EXPECTED is not included.
        with image.open("rb") as stream:
            response = session.post(base + "/api/chat/async", data={
                "owner": "batch-smoke-20260930", "session_id": request_id, "request_id": request_id,
                "scene": "exam", "exam_backend": args.backend, "text": "请逐题解答照片中的全部题目，没拍全的请单独提醒。",
                "event": json.dumps({"exam": {"profile": "auto", "allow_web": False, "use_public_knowledge": True}}, ensure_ascii=False),
            }, files={"files": (image.name, stream, "image/png")}, timeout=45)
        response.raise_for_status()
        submitted = response.json()
        task_id = submitted["task_id"]
        metadata = {"task_id": task_id, "request_id": submitted.get("request_id"), "url": base,
                    "case": args.case, "backend": args.backend, "status": "pending"}
        write_json(submission_path, metadata)
    print(json.dumps({"case": args.case, "backend": args.backend, "task_id": task_id, "status": "polling_original_task"}, ensure_ascii=False), flush=True)
    deadline = time.monotonic() + args.timeout
    next_update = 0
    while time.monotonic() < deadline:
        response = session.get(base + "/api/task", params={"task_id": task_id}, timeout=30)
        response.raise_for_status()
        task = response.json()
        status = task.get("status")
        metadata.update(status=status)
        write_json(submission_path, metadata)
        write_json(OUTPUT / f"{args.case}-{args.backend}-task.json", task)
        if status == "done":
            result = task.get("result") or {}
            write_json(OUTPUT / f"{args.case}-{args.backend}-result.json", result)
            checks = validate(args.case, result)
            write_json(OUTPUT / f"{args.case}-{args.backend}-validation.json", checks)
            print(json.dumps({"case": args.case, "backend": args.backend, "task_id": task_id,
                              "passed": checks["passed"], "errors": checks["errors"],
                              "report": str(OUTPUT / f"{args.case}-{args.backend}-validation.json")}, ensure_ascii=False), flush=True)
            return 0 if checks["passed"] else 1
        if status == "error":
            print(json.dumps({"task_id": task_id, "status": status, "error": task.get("error")}, ensure_ascii=False), flush=True)
            return 1
        if time.monotonic() >= next_update:
            print(json.dumps({"task_id": task_id, "status": status}, ensure_ascii=False), flush=True)
            next_update = time.monotonic() + 25
        time.sleep(args.poll)
    print(json.dumps({"task_id": task_id, "status": "still_pending", "message": "保留原任务；再次以相同 case/backend 执行会继续轮询，不重复提交。"}, ensure_ascii=False), flush=True)
    return 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=sorted(EXPECTED), default="mixed")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--backend", choices=("original", "jev"), default="original")
    parser.add_argument("--url", default="http://127.0.0.1:8802")
    parser.add_argument("--task-id", default="", help="继续轮询已有异步任务，不上传新图")
    parser.add_argument("--timeout", type=float, default=900)
    parser.add_argument("--poll", type=float, default=2)
    args = parser.parse_args()
    if args.timeout <= 0 or not 0 < args.poll <= 60:
        parser.error("timeout 必须为正数，poll 必须在 0 到 60 秒之间")
    image = generate(args.case)
    if not args.live:
        print(json.dumps({"case": args.case, "image": str(image), "live": False}, ensure_ascii=False))
        return 0
    return live(args, image)


if __name__ == "__main__":
    sys.exit(main())
