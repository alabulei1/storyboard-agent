"""Produce one shot automatically, then stop for the brain's review.

  run_shot.py <project_dir> <shot_id> [--max-retries 1]
  run_shot.py <project_dir> --accept <shot_id> --end-state "<actual end state>" [--take N]

Generate (Wan 3.0, continuation from the previous accepted shot) → QA (burned-in subtitles via OCR,
expected dialogue via whisper, voice-sample leakage) → retry within limits → ledger → contact sheet.
A shot is only used downstream after --accept, which records its ACTUAL end state; the next shot's
"当前状态" is built from that.
"""
import argparse
import difflib
import json
import re
import subprocess
import sys
import time
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))
from build_prompt import build  # noqa: E402
import gate  # noqa: E402

def sh(*args, check=True):
    return subprocess.run(args, capture_output=True, text=True, check=check)


def jload(p, default):
    return json.loads(p.read_text()) if p.exists() else default


def norm(text):
    return re.sub(r"[^一-鿿A-Za-z0-9]", "", text)


def whisper(video):
    from lt import env
    model = env().get("WHISPER_MODEL") or sh("bash", "-c", "find ~ -name 'ggml-large-v3-turbo*.bin' -maxdepth 6 2>/dev/null | head -1").stdout.strip()
    if not model:
        sys.exit("whisper model not found: set WHISPER_MODEL in .env")
    wav = video.with_suffix(".qa.wav")
    sh("ffmpeg", "-v", "error", "-y", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000", str(wav))
    out = sh("whisper-cli", "-m", model, "-l", "zh", "-nt", "-f", str(wav), check=False).stdout
    wav.unlink(missing_ok=True)
    return out.strip()


def best_ratio(needle, hay):
    needle, hay = norm(needle), norm(hay)
    if not needle:
        return 1.0, 0
    best, pos = 0.0, 0
    for i in range(0, max(1, len(hay) - len(needle) + 1)):
        r = difflib.SequenceMatcher(None, needle, hay[i:i + len(needle)]).ratio()
        if r > best:
            best, pos = r, i
    return best, pos


def ocr_boxes(images):
    """Text boxes per image, normalized with origin bottom-left: macOS Vision if available, else tesseract."""
    ocr_bin = TOOLS / "ocr"
    if sys.platform == "darwin" and ocr_bin.exists():
        out = sh(str(ocr_bin), *images).stdout
        return {json.loads(l)["image"]: json.loads(l).get("items", []) for l in out.splitlines()}
    res = {}
    for img in images:
        w, h = map(int, sh("ffprobe", "-v", "error", "-show_entries", "stream=width,height", "-of", "csv=p=0", img).stdout.strip().split(","))
        tsv = sh("tesseract", img, "stdout", "-l", "chi_sim+eng", "tsv", check=False).stdout.splitlines()[1:]
        items = []
        for row in tsv:
            c = row.split("\t")
            if len(c) == 12 and c[11].strip() and float(c[10]) > 30:
                left, top, bw, bh = map(int, c[6:10])
                items.append({"text": c[11], "conf": float(c[10]) / 100, "x": left / w, "w": bw / w, "y": 1 - (top + bh) / h, "h": bh / h})
        res[img] = items
    return res


def subtitle_frames(video, qa_dir):
    frames = qa_dir / (video.stem + "_frames")
    frames.mkdir(parents=True, exist_ok=True)
    for f in frames.glob("*.png"):
        f.unlink()
    sh("ffmpeg", "-v", "error", "-y", "-i", str(video), "-vf", "fps=1", str(frames / "f_%02d.png"))
    hits = []
    for img, items in ocr_boxes(sorted(str(f) for f in frames.glob("*.png"))).items():
        sub = [i["text"] for i in items if i["y"] < 0.25 and i["conf"] > 0.3 and i["w"] < 0.9]
        if sub:
            hits.append({"frame": Path(img).name, "text": sub})
    return hits


def contact_sheet(video, out):
    sh("ffmpeg", "-v", "error", "-y", "-i", str(video), "-vf",
       "fps=1,scale=300:-2,drawtext=fontfile=/System/Library/Fonts/Supplemental/Arial.ttf:text='%{pts\\:hms}'"
       ":x=4:y=4:fontsize=14:fontcolor=yellow:box=1:boxcolor=black@0.6,tile=6x3", "-frames:v", "1", str(out))


def qa(video, shot, story, proj):
    transcript = whisper(video)
    checks = []
    last_pos = -1
    for d in shot.get("dialogue", []):
        r, pos = best_ratio(d["text"], transcript)
        checks.append({"line": d["text"], "ratio": round(r, 2), "in_order": pos >= last_pos})
        last_pos = max(last_pos, pos)
    leaks = []
    for key, a in story["anchors"].items():
        if a.get("voice_text"):
            r, _ = best_ratio(a["voice_text"][:8], transcript)
            if r >= 0.75:
                leaks.append(key)
    subs = subtitle_frames(video, proj / "qa")
    problems = []
    if subs:
        problems.append(f"字幕 {len(subs)} 帧")
    bad = [c for i, c in enumerate(checks) if c["ratio"] < (0.9 if i == 0 else 0.75)]
    if bad:
        problems.append("台词不符：" + "；".join(c["line"] for c in bad))
    if any(not c["in_order"] for c in checks):
        problems.append("台词顺序不对")
    if leaks:
        problems.append("样音内容混入：" + ",".join(leaks))
    return {"transcript": transcript, "dialogue": checks, "subtitle_frames": subs, "voice_leaks": leaks,
            "problems": problems, "pass": not problems}


def plan(proj, sid):
    """Show the human exactly what will be generated and what it costs, then wait for approval."""
    gate.require(proj, "board")
    story = json.loads((proj / "story.json").read_text())
    state = jload(proj / "state.json", {})
    shot = next(s for s in story["shots"] if s["id"] == sid)
    idx = [s["id"] for s in story["shots"]].index(sid)
    prev = story["shots"][idx - 1]["id"] if idx else None
    if shot.get("link") == "extend":
        if not prev or state.get(prev, {}).get("status") != "accepted":
            sys.exit(f"{prev} 还没有确认通过")
        shot["start_state_actual"] = state[prev]["end_state"]
        (proj / "story.json").write_text(json.dumps(story, ensure_ascii=False, indent=1))
    prompt, media = build(proj, sid)
    overlap = story.get("continuation_seconds", 4) if shot.get("link") == "extend" else 0
    (proj / f"prompts/{sid}_plan.txt").write_text(prompt)
    est = media["duration"] + overlap
    summary = (f"{sid}：生成 {media['duration']} 秒（含承接 {overlap} 秒），预计计费约 {est} 秒；"
               f"参考图 {len(media['ref_images'])} 张、音色 {len(media['ref_audios'])} 段；提示词见 prompts/{sid}_plan.txt")
    gate.set_status(str(proj), f"plan:{sid}", "pending", summary=summary)
    print(prompt)


def produce(proj, sid, max_retries):
    gate.require(str(proj), f"plan:{sid}")
    story = json.loads((proj / "story.json").read_text())
    state = jload(proj / "state.json", {})
    ledger_p = proj / "ledger.json"
    shot = next(s for s in story["shots"] if s["id"] == sid)
    idx = [s["id"] for s in story["shots"]].index(sid)
    prev = story["shots"][idx - 1]["id"] if idx else None
    if shot.get("link") == "extend":
        if not prev or state.get(prev, {}).get("status") != "accepted":
            sys.exit(f"{prev} not accepted yet; review it and run --accept first")
        shot["start_state_actual"] = state[prev]["end_state"]
        (proj / "story.json").write_text(json.dumps(story, ensure_ascii=False, indent=1))
    budget = story.get("budget_billed_seconds", 1e9)
    spent = sum(e.get("billed_s", 0) for e in jload(ledger_p, []) if isinstance(e, dict))

    tail = None
    if shot.get("link") == "extend":
        tail = proj / f"tails/{sid}_from_{prev}_tail.mp4"
        sh(sys.executable, "-I", str(TOOLS / "lt.py"), "tail", "--in", str(proj / state[prev]["clip"]),
           "--sec", str(story.get("continuation_seconds", 4)), "--out", str(tail), "--clean-subs")

    for take in range(1, max_retries + 2):
        prompt, media = build(proj, sid)
        est = media["duration"] + (story.get("continuation_seconds", 4) if tail else 0)
        if spent + est > budget:
            print(json.dumps({"shot": sid, "stopped": "budget", "spent": spent, "budget": budget}))
            return
        pf = proj / f"prompts/{sid}_take{take}.txt"
        pf.write_text(prompt)
        out = proj / f"clips/{sid}_take{take}.mp4"
        args = [sys.executable, "-I", str(TOOLS / "lt.py"), "wan", "--out", str(out), "--prompt-file", str(pf),
                "--duration", str(media["duration"]), "--aspect", "adaptive" if tail else story.get("ratio", "16:9")]
        if tail:
            args += ["--ref-video", str(tail)]
        if media["ref_images"]:
            args += ["--ref-image", *media["ref_images"]]
        if media["ref_audios"]:
            args += ["--ref-audio", *media["ref_audios"]]
        t = time.time()
        res = subprocess.run(args, capture_output=True, text=True)
        lines = [l for l in res.stdout.splitlines() if l.startswith("{")]
        gen = json.loads(lines[-1]) if lines and '"out"' in lines[-1] else None
        if not gen:
            entry = {"shot": sid, "take": take, "status": "失败", "error": (res.stdout + res.stderr)[-400:]}
            print(json.dumps(entry, ensure_ascii=False))
            ledger = jload(ledger_p, []); ledger.append(entry); ledger_p.write_text(json.dumps(ledger, ensure_ascii=False, indent=1))
            continue
        billed = (gen.get("usage") or {}).get("duration", gen["duration"])
        spent += billed
        report = qa(out, shot, story, proj)
        contact_sheet(out, proj / f"qa/{sid}_take{take}.jpg")
        entry = {"shot": sid, "take": take, "status": "待审" if report["pass"] else "质检不过",
                 "model": "wan3.0-video", "link": shot.get("link"), "node": out.name, "billed_s": billed,
                 "retry": take > 1, "gen_seconds": round(time.time() - t), "problems": report["problems"],
                 "transcript": report["transcript"][:300]}
        ledger = jload(ledger_p, []); ledger.append(entry); ledger_p.write_text(json.dumps(ledger, ensure_ascii=False, indent=1))
        (proj / f"qa/{sid}_take{take}.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))
        print(json.dumps({k: entry[k] for k in ("shot", "take", "status", "billed_s", "problems")}, ensure_ascii=False))
        state[sid] = {"status": "待审", "clip": f"clips/{out.name}", "take": take, "qa_pass": report["pass"]}
        (proj / "state.json").write_text(json.dumps(state, ensure_ascii=False, indent=1))
        if report["pass"] or take == max_retries + 1:
            gate.set_status(str(proj), f"review:{sid}", "pending",
                            summary=f"{sid} 第 {take} 次：{'质检通过' if report['pass'] else '质检不过：' + '；'.join(report['problems'])}；"
                                    f"质检图 qa/{sid}_take{take}.jpg")
            return


def accept(proj, sid, end_state, take):
    gate.require(str(proj), f"review:{sid}")
    state = jload(proj / "state.json", {})
    entry = state.get(sid, {})
    if take:
        entry["clip"] = f"clips/{sid}_take{take}.mp4"
        entry["take"] = take
    entry.update({"status": "accepted", "end_state": end_state})
    state[sid] = entry
    (proj / "state.json").write_text(json.dumps(state, ensure_ascii=False, indent=1))
    print(json.dumps({sid: entry}, ensure_ascii=False))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("shot", nargs="?")
    ap.add_argument("--max-retries", type=int, default=0, help="automatic re-rolls cost money; default 0 = ask the human")
    ap.add_argument("--plan", action="store_true", help="build the prompt and cost estimate, request plan approval")
    ap.add_argument("--accept")
    ap.add_argument("--end-state")
    ap.add_argument("--take", type=int)
    a = ap.parse_args()
    proj = Path(a.project).resolve()
    if a.accept:
        accept(proj, a.accept, a.end_state, a.take)
    elif a.plan:
        plan(proj, a.shot)
    else:
        produce(proj, a.shot, a.max_retries)
