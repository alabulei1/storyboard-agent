"""Create a new project folder with the standard layout, a story.json draft and the first gate.

  new_project.py <name> [--source novel.txt] [--title "…"] [--ratio 16:9] [--budget 200]

Creates projects/<name>/ with source/ anchors/voice/ prompts/ clips/ tails/ qa/ exports/,
copies the source text to source/novel.md, writes a story.json draft (style, anchors and shots
are filled in later by Claude, each step confirmed by the human) and requests the `source` gate.
Refuses to touch an existing project.
"""
import argparse
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
import gate  # noqa: E402

DIRS = ["source", "anchors/voice", "prompts", "clips", "tails", "qa", "exports"]

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("name", help="folder name: letters, digits, Chinese, - or _")
    ap.add_argument("--source", help="path to the story text; can also be added later as source/novel.md")
    ap.add_argument("--title", default="")
    ap.add_argument("--ratio", default="16:9", help="16:9 (landscape) or 9:16 (portrait)")
    ap.add_argument("--budget", type=int, default=200, help="max billed video seconds for this project")
    a = ap.parse_args()

    if not re.fullmatch(r"[\w-]+", a.name):
        sys.exit("项目名只能包含字母、数字、中文、- 或 _")
    proj = ROOT / "projects" / a.name
    if proj.exists():
        sys.exit(f"项目已存在：{proj}（要继续做这个项目，直接用它；不会覆盖）")
    if a.source and not Path(a.source).is_file():
        sys.exit(f"找不到原文：{a.source}")

    for d in DIRS:
        (proj / d).mkdir(parents=True)
    if a.source:
        shutil.copyfile(a.source, proj / "source/novel.md")
    story = {
        "title": a.title or a.name,
        "model": "wan3.0-video",
        "ratio": a.ratio,
        "continuation_seconds": 4,
        "budget_billed_seconds": a.budget,
        "style": "",
        "sound": "",
        "negative": ["不要字幕", "不要水印", "不要复杂文字", "不要品牌标志"],
        "anchors": {},
        "shots": [],
    }
    (proj / "story.json").write_text(json.dumps(story, ensure_ascii=False, indent=1))
    gate.set_status(str(proj), "source", "pending",
                    summary=f"新项目 {a.name}：原文 {'source/novel.md' if a.source else '（待提供）'}，"
                            f"画幅 {a.ratio}，模型 wan3.0-video，预算 {a.budget} 计费秒")
    print(f"已创建 {proj}")
