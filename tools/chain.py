"""Run one continuation chain serially: derive the continuation anchor from the previous
clip, build the shot prompt, generate, record in ledger.json.

  chain.py <project_dir> <chain: A|B> <first_prev_clip> S2 S3 ...
Chain A = video extension (previous tail as reference video); chain B = last frame as first frame.
"""
import json
import subprocess
import sys
import time
from pathlib import Path

proj = Path(sys.argv[1]).resolve()
chain = sys.argv[2]
prev = proj / sys.argv[3]
shots = sys.argv[4:]
LT = Path(__file__).with_name("lt.py")
shotdefs = json.loads((proj / "prompts/shots.json").read_text())
anchors = json.loads((proj / "anchors/anchors.json").read_text())
ledger_path = proj / "ledger.json"
STYLE = ("保持人物身份、数量、服装、道具归属、空间方向和声音关系稳定。\n"
         "写实电影质感，梅雨夜的冷青色调与修表铺台灯的暖黄光形成对比，画面细腻，轻微胶片颗粒，浅景深。\n"
         "画面中不出现字幕；除道具锚点上的刻字外不出现任何可读文字。")


def run(*args):
    out = subprocess.run([sys.executable, "-I", str(LT), *args], capture_output=True, text=True)
    if out.returncode:
        raise RuntimeError(out.stdout + out.stderr)
    return json.loads(out.stdout.strip().splitlines()[-1])


def record(entry):
    ledger = json.loads(ledger_path.read_text()) if ledger_path.exists() else []
    ledger.append(entry)
    ledger_path.write_text(json.dumps(ledger, ensure_ascii=False, indent=1))


for sid in shots:
    d = shotdefs[sid]
    refs = d["refs"]
    lines = []
    for i, r in enumerate(refs[:3], 1):
        a = anchors[r]
        lines.append(f"图片{i} {a['use']}")
    role = "【参考】" + "；".join(lines) + "。各人物的外貌、服装、动作、站位和台词不互相交换。"
    if chain == "A":
        tail = proj / f"tails/{sid}_from_{prev.stem}_tail.mp4"
        run("tail", "--in", str(prev), "--sec", "4", "--out", str(tail), "--compress")  # H3 via Token Station: sync gateway times out on large payloads
        link = "【承接】视频1 是上一条的结尾。向后延长视频1进行续写，从视频1的最后画面无缝接着拍，不重复视频1里已经说过的台词。"
        prompt = "\n".join([link, role, d["stages"], STYLE])
        args = ["--ref-video", str(tail)]
        anchor_file = tail
    else:
        last = proj / f"tails/{sid}_from_{prev.stem}_last.jpg"
        run("lastframe", "--in", str(prev), "--out", str(last))
        link = "【承接】首帧就是上一条的最后画面，从这一帧开始继续拍。"
        prompt = "\n".join([link, role, d["stages"], STYLE])
        args = ["--first-frame", str(last)]
        anchor_file = last
    if chain == "B":
        # MiniMax H3 rejects mixing frame inputs with reference inputs: last frame only.
        prompt = "\n".join([link, d["stages"], STYLE])
        ref_args = []
    else:
        ref_args = ["--ref-image", *[str(proj / anchors[r]["file"]) for r in refs[:3]]]
    pf = proj / f"prompts/{sid}_{chain}.txt"
    pf.write_text(prompt)
    out = proj / f"clips/{sid}_{chain}.mp4"
    t = time.time()
    try:
        for attempt in (1, 2):
            try:
                res = run("video", "--model", "h3", "--duration", str(d["duration"]), "--out", str(out),
                          "--prompt-file", str(pf), *args, *ref_args)
                break
            except RuntimeError as e:
                if "HTTP 504" not in str(e) or attempt == 2:
                    raise
                print(f"{sid}_{chain} gateway timeout, retrying (upstream may have billed)", flush=True)
        record({"shot": sid, "chain": chain, "version": "v1", "status": "已生效", "model": "minimax/MiniMax-H3",
                "link": "续写" if chain == "A" else "尾帧", "continuation_anchor": anchor_file.name,
                "node": out.name, "planned_s": d["duration"], "actual_s": res["duration"],
                "size": f'{res["w"]}x{res["h"]}', "gen_seconds": round(time.time() - t)})
        print(f"{sid}_{chain} OK {res}", flush=True)
        prev = out
    except RuntimeError as e:
        record({"shot": sid, "chain": chain, "version": "v1", "status": "失败", "error": str(e)[:400]})
        print(f"{sid}_{chain} FAILED {str(e)[:400]}", flush=True)
        break
