"""Produce one shot, then stop for review.

  run_shot.py <project_dir> <shot_id> --plan                      prompt + cost estimate, requests plan:<shot>
  run_shot.py <project_dir> <shot_id> [--max-retries 0]           generate (needs plan:<shot> approved)
  run_shot.py <project_dir> --accept <shot_id> --end-state "<actual end state>" [--take N]

Generate (Wan 3.0, continuation from the previous accepted shot) → ledger → QA (burned-in subtitles
via OCR, expected dialogue via whisper, voice-sample leakage) → contact sheet → requests review:<shot>.
A shot is only used downstream after --accept, which records its ACTUAL end state; the next shot's
"当前状态" is built from that.

Money rules:
- One plan approval = one run. The approval is marked "used" as soon as a task is submitted, and the
  prompt must still be exactly what was approved. A re-roll needs --plan and a new approval.
- Takes are numbered upwards and never overwritten (clips/<shot>_take<N>.mp4).
- A take is written to ledger.json when it is submitted and again when it is fetched, before QA runs.
  If the connection drops while waiting, run the same command again: it fetches the submitted task
  instead of paying for a new one.
"""
import argparse
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
CHAINED = ("extend", "lastframe")  # shots that start from the previous accepted shot
SUBMITTED = "已提交"               # ledger status: task sent to Wan, result not fetched yet
RESUMABLE = 75                     # lt.py wan exit code: submitted (billed) but not fetched
sys.path.insert(0, str(TOOLS))
from build_prompt import build  # noqa: E402
import gate  # noqa: E402

def sh(*args, check=True):
    return subprocess.run(args, capture_output=True, text=True, check=check)


def jload(p, default):
    return json.loads(p.read_text()) if p.exists() else default


def jsave(p, obj):
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1))
    os.replace(tmp, p)


_whisper_model = []


def whisper_model():
    if not _whisper_model:
        from lt import env
        _whisper_model.append(env().get("WHISPER_MODEL") or sh(
            "bash", "-c", "find ~ -maxdepth 6 -name 'ggml-large-v3-turbo*.bin' 2>/dev/null | head -1", check=False).stdout.strip())
    return _whisper_model[0]


def preflight():
    """Everything QA needs must be in place before any money is spent."""
    lack = [t for t in ("ffmpeg", "ffprobe", "whisper-cli") if not shutil.which(t)]
    if not (sys.platform == "darwin" and (TOOLS / "ocr").exists()) and not shutil.which("tesseract"):
        lack.append("字幕检测（macOS 运行 ./setup.sh 编译 tools/ocr，Linux 安装 tesseract）")
    if not whisper_model() or not Path(whisper_model()).is_file():
        lack.append("whisper 模型（在 .env 里设置 WHISPER_MODEL）")
    if lack:
        sys.exit("质检需要的工具不齐，先装好再生成（否则花了钱却没法质检）：" + "、".join(lack))


def norm(text):
    return re.sub(r"[^一-鿿A-Za-z0-9]", "", text)


def whisper(video):
    model = whisper_model()
    if not model:
        raise RuntimeError("whisper model not found: set WHISPER_MODEL in .env")
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


def load_shot(proj, sid):
    """story, state, the shot and the previous shot id; a chained shot gets the previous shot's ACTUAL end state."""
    story = json.loads((proj / "story.json").read_text())
    state = jload(proj / "state.json", {})
    ids = [s["id"] for s in story["shots"]]
    if sid not in ids:
        sys.exit(f"story.json 里没有 {sid}")
    idx = ids.index(sid)
    shot, prev = story["shots"][idx], (ids[idx - 1] if idx else None)
    if shot.get("link") in CHAINED:
        if not prev:
            sys.exit(f"{sid} 是第一条，link 只能是 none")
        if state.get(prev, {}).get("status") != "accepted":
            sys.exit(f"{prev} 还没有确认通过（先审片，再 --accept）")
        if not state[prev].get("end_state"):
            sys.exit(f"{prev} 没有记录实际结束状态：重新 --accept {prev} 并写上 --end-state")
        if shot.get("start_state_actual") != state[prev]["end_state"]:
            shot["start_state_actual"] = state[prev]["end_state"]
            jsave(proj / "story.json", story)
    return story, state, shot, prev


def next_take(proj, sid, ledger):
    """Takes are never reused: one more than the highest take in the ledger or on disk."""
    takes = [e.get("take") or 0 for e in ledger if isinstance(e, dict) and e.get("shot") == sid]
    for f in (proj / "clips").glob(f"{sid}_take*.mp4"):
        m = re.fullmatch(re.escape(sid) + r"_take(\d+)", f.stem)
        if m:
            takes.append(int(m.group(1)))
    return max(takes, default=0) + 1


def spent_seconds(ledger):
    """Billed seconds so far; a submitted task that was not fetched yet counts at its estimate."""
    total = 0
    for e in ledger:
        if isinstance(e, dict):
            total += e["billed_s"] if e.get("billed_s") is not None else (e.get("est_s", 0) if e.get("status") == SUBMITTED else 0)
    return total


def estimate(story, shot, media):
    return media["duration"] + (story.get("continuation_seconds", 4) if shot.get("link") == "extend" else 0)


def plan(proj, sid):
    """Show the human exactly what will be generated and what it costs, then wait for approval."""
    gate.require(str(proj), "board")
    story, state, shot, prev = load_shot(proj, sid)
    if state.get(sid, {}).get("pending_task"):
        sys.exit(f"{sid} 有一个已提交但还没取回的任务：先运行 run_shot.py <项目> {sid} 取回，不要重新计划")
    ledger = jload(proj / "ledger.json", [])
    prompt, media = build(proj, sid)
    overlap = story.get("continuation_seconds", 4) if shot.get("link") == "extend" else 0
    (proj / "prompts").mkdir(exist_ok=True)
    (proj / f"prompts/{sid}_plan.txt").write_text(prompt)
    est, take = estimate(story, shot, media), next_take(proj, sid, ledger)
    how = {"extend": f"续写上一条，含承接 {overlap} 秒", "lastframe": "上一条最后一帧作首帧"}.get(shot.get("link"), "独立镜头")
    summary = (f"{sid} 第 {take} 次生成：{media['duration']} 秒（{how}），预计计费约 {est} 秒；"
               f"项目已计费 {spent_seconds(ledger):g} 秒，预算 {story.get('budget_billed_seconds', '未设')} 秒；"
               f"参考图 {len(media['ref_images'])} 张、音色 {len(media['ref_audios'])} 段；提示词见 prompts/{sid}_plan.txt")
    gate.set_status(str(proj), f"plan:{sid}", "pending", summary=summary)
    print(prompt)


def generate(args, on_task):
    """Run lt.py wan; call on_task(task_id) the moment the task is submitted. Returns (result or None, exit code, log)."""
    p = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    lines = []
    for line in p.stdout:
        lines.append(line)
        if line.startswith("{") and '"task_id"' in line and '"out"' not in line:
            try:
                on_task(json.loads(line)["task_id"])
            except ValueError:
                pass
    p.wait()
    done = [l for l in lines if l.startswith("{") and '"out"' in l]
    return (json.loads(done[-1]) if done and p.returncode == 0 else None), p.returncode, "".join(lines)


def produce(proj, sid, max_retries):
    story, state, shot, prev = load_shot(proj, sid)
    ledger_p, state_p = proj / "ledger.json", proj / "state.json"
    ledger = jload(ledger_p, [])
    pending = state.get(sid, {}).get("pending_task")  # submitted earlier, never fetched: fetch it, do not pay again
    if not pending:
        gate.require(str(proj), f"plan:{sid}")
        plan_p = proj / f"prompts/{sid}_plan.txt"
        if not plan_p.exists() or plan_p.read_text() != build(proj, sid)[0]:
            sys.exit("提示词和用户批准的那一版不一样了（story.json 或上一条的结束状态在批准之后有改动）。"
                     "重新运行 run_shot.py --plan，再请用户确认。")
    preflight()
    budget = story.get("budget_billed_seconds", 1e9)
    for d in ("clips", "prompts", "qa", "tails"):
        (proj / d).mkdir(exist_ok=True)

    tail = first = None
    if not pending and shot.get("link") == "extend":
        tail = proj / f"tails/{sid}_from_{prev}_tail.mp4"
        sh(sys.executable, "-I", str(TOOLS / "lt.py"), "tail", "--in", str(proj / state[prev]["clip"]),
           "--sec", str(story.get("continuation_seconds", 4)), "--out", str(tail), "--clean-subs")
    if not pending and shot.get("link") == "lastframe":
        first = proj / f"tails/{sid}_from_{prev}_lastframe.png"
        sh(sys.executable, "-I", str(TOOLS / "lt.py"), "lastframe", "--in", str(proj / state[prev]["clip"]),
           "--out", str(first))

    first_take = pending["take"] if pending else next_take(proj, sid, ledger)
    for n in range(max_retries + 1):
        take = first_take + n
        resume = pending if n == 0 else None
        out = proj / f"clips/{sid}_take{take}.mp4"
        prompt, media = build(proj, sid)
        est = estimate(story, shot, media)
        if resume:
            entry = next((e for e in ledger if isinstance(e, dict) and e.get("task_id") == resume["task_id"]), None)
            if entry is None:
                entry = {"shot": sid, "take": take, "status": SUBMITTED, "model": "wan3.0-video", "link": shot.get("link"),
                         "node": out.name, "est_s": est, "task_id": resume["task_id"]}
                ledger.append(entry)
            args = [sys.executable, "-I", str(TOOLS / "lt.py"), "wan", "--out", str(out), "--resume", resume["task_id"]]
        else:
            spent = spent_seconds(ledger)
            if spent + est > budget:
                print(json.dumps({"shot": sid, "stopped": "budget", "spent": spent, "estimate": est, "budget": budget}, ensure_ascii=False))
                return
            pf = proj / f"prompts/{sid}_take{take}.txt"
            pf.write_text(prompt)
            entry = {"shot": sid, "take": take, "status": SUBMITTED, "model": "wan3.0-video", "link": shot.get("link"),
                     "node": out.name, "est_s": est, "retry": n > 0}
            args = [sys.executable, "-I", str(TOOLS / "lt.py"), "wan", "--out", str(out), "--prompt-file", str(pf),
                    "--duration", str(media["duration"]), "--aspect", "adaptive" if (tail or first) else story.get("ratio", "16:9")]
            if tail:
                args += ["--ref-video", str(tail)]
            if first:
                args += ["--first-frame", str(first)]
            if media["ref_images"]:
                args += ["--ref-image", *media["ref_images"]]
            if media["ref_audios"]:
                args += ["--ref-audio", *media["ref_audios"]]

        def submitted(tid, entry=entry, take=take):
            """The task exists and will be billed: record it and use up the approval before anything else can fail."""
            if entry.get("task_id"):
                return
            entry["task_id"] = tid
            ledger.append(entry)
            jsave(ledger_p, ledger)
            state.setdefault(sid, {})["pending_task"] = {"task_id": tid, "take": take}
            jsave(state_p, state)
            if gate.status(str(proj), f"plan:{sid}") == "approved":
                gate.set_status(str(proj), f"plan:{sid}", "used", note=f"第 {take} 次生成已提交")

        t = time.time()
        gen, code, log = generate(args, submitted)
        if not gen:
            entry["error"] = log[-400:]
            if code == RESUMABLE and entry.get("task_id"):
                jsave(ledger_p, ledger)
                print(json.dumps({"shot": sid, "take": take, "status": "已提交，还没取回", "task_id": entry["task_id"],
                                  "next": f"再运行一次 run_shot.py <项目> {sid}：会取回这个任务，不会重复扣费"}, ensure_ascii=False))
                return
            entry["status"] = "失败"
            if not entry.get("task_id"):
                ledger.append(entry)
            jsave(ledger_p, ledger)
            if sid in state and state[sid].pop("pending_task", None):
                if not state[sid]:
                    del state[sid]
                jsave(state_p, state)
            print(json.dumps({k: entry.get(k) for k in ("shot", "take", "status", "task_id", "error")}, ensure_ascii=False))
            if not entry.get("task_id"):
                return  # never submitted, nothing was billed; the approval is still valid once the cause is fixed
            continue

        # The take is paid for: write it down before QA, so a QA crash can never lose it.
        billed = (gen.get("usage") or {}).get("duration", gen["duration"])
        entry.pop("error", None)
        entry.update({"status": "已生成", "billed_s": billed, "gen_seconds": round(time.time() - t)})
        jsave(ledger_p, ledger)
        state[sid] = {"status": "待审", "clip": f"clips/{out.name}", "take": take, "qa_pass": None}
        jsave(state_p, state)
        try:
            report = qa(out, shot, story, proj)
        except (Exception, SystemExit) as e:
            report = {"transcript": "", "dialogue": [], "subtitle_frames": [], "voice_leaks": [],
                      "problems": [f"质检没跑完，需要人工检查：{e}"], "pass": False}
        try:
            contact_sheet(out, proj / f"qa/{sid}_take{take}.jpg")
        except Exception:
            report["problems"].append("质检图没生成")
        entry.update({"status": "待审" if report["pass"] else "质检不过", "problems": report["problems"],
                      "transcript": report["transcript"][:300]})
        jsave(ledger_p, ledger)
        (proj / f"qa/{sid}_take{take}.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))
        state[sid]["qa_pass"] = report["pass"]
        jsave(state_p, state)
        print(json.dumps({k: entry[k] for k in ("shot", "take", "status", "billed_s", "problems")}, ensure_ascii=False))
        if report["pass"] or n == max_retries:
            gate.set_status(str(proj), f"review:{sid}", "pending",
                            summary=f"{sid} 第 {take} 次：{'质检通过' if report['pass'] else '质检不过：' + '；'.join(report['problems'])}；"
                                    f"视频 clips/{out.name}，质检图 qa/{sid}_take{take}.jpg")
            return


def accept(proj, sid, end_state, take):
    gate.require(str(proj), f"review:{sid}")
    end_state = (end_state or "").strip()
    if not end_state:
        sys.exit("--end-state 必填：写下这一条实际拍出来的结束状态（道具、服装、站位），下一条用它作“当前状态”")
    state = jload(proj / "state.json", {})
    entry = state.get(sid)
    if not entry or not entry.get("clip"):
        sys.exit(f"{sid} 还没有生成过")
    if take:
        entry["clip"] = f"clips/{sid}_take{take}.mp4"
        entry["take"] = take
    if not (proj / entry["clip"]).is_file():
        sys.exit(f"找不到 {entry['clip']}")
    entry.update({"status": "accepted", "end_state": end_state})
    state[sid] = entry
    jsave(proj / "state.json", state)
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
    if not (proj / "story.json").is_file():
        sys.exit(f"找不到项目：{proj}")
    if a.accept:
        accept(proj, a.accept, a.end_state, a.take)
    elif not a.shot:
        sys.exit("缺少镜号，例如：run_shot.py projects/demo S1 --plan")
    elif a.plan:
        plan(proj, a.shot)
    else:
        produce(proj, a.shot, a.max_retries)
