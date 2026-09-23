"""把 Mermaid 定义渲染成 PNG/SVG。

为什么自己写而不用 mermaid-cli：`@mermaid-js/mermaid-cli` 要装 puppeteer + Chromium
（几百 MB，而本机已有 Chrome）。这里用 Python 生成 HTML，再让 headless Chrome 截图，
零额外依赖，且 Mermaid 已下载到本地 vendor/，**离线可重复渲染**。

用法：
    python render_mermaid.py            # 渲染 mermaid/ 下全部 .mmd
    python render_mermaid.py 01         # 只渲染文件名以 01 开头的
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
MMD_DIR = HERE / "mermaid"
OUT_DIR = HERE / "out"
VENDOR = HERE / "vendor" / "mermaid.min.js"

CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]

#: 画布宽高（CSS px）。给得宽一些，避免横向被压扁。
WIDTH = 1800
HEIGHT = 2600
SCALE = 2  # deviceScaleFactor，出图更清晰


def find_browser() -> str:
    for c in CHROME_CANDIDATES:
        if Path(c).is_file():
            return c
    raise SystemExit("找不到 Chrome/Edge，无法渲染。")


PAGE = """<!doctype html>
<html><head><meta charset="utf-8">
<style>
  html,body {{ margin:0; padding:0; background:#ffffff; }}
  #box {{ padding:24px; }}
  .mermaid {{ background:#ffffff; }}
</style>
<script src="{vendor}"></script>
</head><body>
<div id="box"><pre class="mermaid">{code}</pre></div>
<script>
  mermaid.initialize({{
    startOnLoad: true,
    theme: 'base',
    securityLevel: 'loose',
    flowchart: {{ htmlLabels: true, curve: 'basis', useMaxWidth: false }},
    themeVariables: {{
      fontFamily: '"Microsoft YaHei", "PingFang SC", sans-serif',
      fontSize: '15px',
      primaryColor: '#EAF6F5',
      primaryBorderColor: '#3FA7A3',
      primaryTextColor: '#1F2933',
      lineColor: '#5A6B7B',
      secondaryColor: '#F3F6F8',
      tertiaryColor: '#FBFCFD'
    }}
  }});

  // 量出真实内容尺寸，写回 DOM 供 headless 的 --dump-dom 读取。
  // 单遍固定画布会在图小的时候留下大片空白。
  window.addEventListener('load', function () {{
    setTimeout(function () {{
      var svg = document.querySelector('#box svg');
      if (!svg) return;
      var r = svg.getBoundingClientRect();
      document.documentElement.setAttribute('data-render-width', Math.ceil(r.width));
      document.documentElement.setAttribute('data-render-height', Math.ceil(r.height));
    }}, 1200);
  }});
</script>
</body></html>
"""


def render_one(browser: str, mmd: Path) -> tuple[Path, bool]:
    """两遍渲染：先量出内容真实尺寸，再按尺寸截图。

    单遍固定画布会在图小的时候留下大片空白（实测首版就是这样）。
    """
    code = mmd.read_text(encoding="utf-8")
    html = PAGE.format(vendor=VENDOR.as_uri(), code=code)
    tmp_html = OUT_DIR / f"_{mmd.stem}.html"
    tmp_html.write_text(html, encoding="utf-8")
    profile = OUT_DIR / f"_prof_{mmd.stem}"
    png = OUT_DIR / f"{mmd.stem}.png"

    common = [
        browser,
        "--headless=new",
        "--disable-gpu",
        "--hide-scrollbars",
        "--no-first-run",
        "--no-default-browser-check",
        f"--user-data-dir={profile}",
        "--virtual-time-budget=15000",
    ]

    # 第一遍：量尺寸
    measure = subprocess.run(
        common + [f"--window-size={WIDTH},{HEIGHT}", "--dump-dom", tmp_html.as_uri()],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    w, h = WIDTH, HEIGHT
    m = re.search(r'data-render-width="(\d+)"\s+data-render-height="(\d+)"', measure.stdout or "")
    if m:
        w = max(320, int(m.group(1)))
        h = max(200, int(m.group(2)))
    elif "<svg" in (measure.stdout or ""):
        m2 = re.search(r'<svg[^>]*width="([\d.]+)"[^>]*height="([\d.]+)"', measure.stdout)
        if m2:
            w, h = max(320, int(float(m2.group(1)))), max(200, int(float(m2.group(2))))

    # 第二遍：按尺寸截图（PAGE 的 padding 是 24px，两侧共 48）
    shot = subprocess.run(
        common
        + [
            f"--window-size={w + 48},{h + 48}",
            f"--force-device-scale-factor={SCALE}",
            f"--screenshot={png}",
            tmp_html.as_uri(),
        ],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    ok = png.is_file() and png.stat().st_size > 0
    if not ok:
        print(f"  [失败] {mmd.name}: rc={shot.returncode} {(shot.stderr or '')[:300]}")
    else:
        print(f"       尺寸 {w + 48}x{h + 48}")
    return png, ok


def main() -> int:
    if not VENDOR.is_file():
        raise SystemExit(f"缺少 {VENDOR}（Mermaid 本地副本）")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    browser = find_browser()

    prefix = sys.argv[1] if len(sys.argv) > 1 else ""
    files = sorted(p for p in MMD_DIR.glob("*.mmd") if p.name.startswith(prefix))
    if not files:
        raise SystemExit(f"mermaid/ 下没有匹配 {prefix!r} 的 .mmd")

    results = []
    for mmd in files:
        png, ok = render_one(browser, mmd)
        size = f"{png.stat().st_size // 1024}KB" if ok else "-"
        print(f"  {'OK  ' if ok else 'FAIL'} {mmd.stem:38} {size}")
        results.append({"file": mmd.stem, "ok": ok, "png": str(png) if ok else ""})

    # 清理临时 HTML 与 profile
    for p in OUT_DIR.glob("_*.html"):
        p.unlink(missing_ok=True)
    for d in OUT_DIR.glob("_prof_*"):
        subprocess.run(["cmd", "/c", "rmdir", "/s", "/q", str(d)], capture_output=True)

    (OUT_DIR / "_render_report.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    failed = [r for r in results if not r["ok"]]
    print(f"\n{len(results) - len(failed)}/{len(results)} 渲染成功")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
