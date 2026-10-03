"""Opt-in real-model smoke cases, synthetic inputs, isolated DB, no production records.

Run: python scripts/evaluate_exam.py --live
Requires the existing model credentials; incurs normal provider API usage.
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lg_assistant import config, exam_knowledge
from lg_assistant.exam_graph import EXAM_GRAPH


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--font", default="C:/Windows/Fonts/msyh.ttc")
    parser.add_argument("--case", choices=["recruitment", "internal", "campus"])
    args = parser.parse_args()
    if not args.live:
        parser.error("Use --live to explicitly enable provider calls")
    from PIL import Image, ImageDraw, ImageFont
    root = config.ROOT.parent / "outputs" / "exam-evaluation"
    root.mkdir(parents=True, exist_ok=True)
    config.DB_PATH = root / "evaluation.sqlite3"
    doc = exam_knowledge.add("evaluation", "示例单位培训考核办法（合成测试资料）", "第一条：员工培训考核成绩达到八十分为合格。第二条：未通过者可在十个工作日内申请一次补考。", "2025版")["id"]
    cases = [
        ("recruitment", ["招聘笔试 · 资料分析（合成测试）", "某企业2024年营收为1200万元，", "2025年营收为1500万元。同比增长率是多少？", "A. 20%    B. 25%    C. 30%    D. 35%"], "B，25%", {}),
        ("internal", ["单位内部考试（合成测试）", "根据《示例单位培训考核办法》2025版，", "员工培训考核成绩达到多少分为合格？", "A. 60分    B. 70分    C. 80分    D. 90分"], "C，80分", {"document_ids": [doc]}),
        ("campus", ["高校微积分测试（合成测试，多选）", "下列求导公式正确的是哪些？", "A. (x²)' = 2x", "B. (sin x)' = -cos x", "C. 常数的导数为0", "D. (eˣ)' = x eˣ"], "A、C", {}),
    ]
    report = {"model": config.MODEL_VISION, "scope": "3 synthetic smoke cases; not an accuracy benchmark", "cases": []}
    for name, lines, expected, extra in cases:
        if args.case and args.case != name:
            continue
        image = Image.new("RGB", (1250, 620), "white")
        draw = ImageDraw.Draw(image)
        font = ImageFont.truetype(args.font, 34)
        for i, line in enumerate(lines): draw.text((45, 45 + 78 * i), line, fill="black", font=font)
        path = root / f"{name}.png"
        image.save(path)
        print(f"Running {name}...", flush=True)
        started = time.perf_counter()
        out = EXAM_GRAPH.invoke({"owner": "evaluation", "files": [str(path)], "text": "请解题，给简洁依据。", "event": {"exam": {"profile": name, **extra}}})
        row = {"case": name, "expected": expected, "answer": out.get("final", {}).get("answer", ""), "answerable": out["result"]["artifacts"][0]["answerable"],
               "seconds": round(time.perf_counter() - started, 2), "result": out["result"], "error": out.get("error", "")}
        report["cases"].append(row)
        report_name = f"report-{args.case}.json" if args.case else "report.json"
        (root / report_name).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({k: row[k] for k in ("case", "expected", "answer", "answerable", "seconds", "error")}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
