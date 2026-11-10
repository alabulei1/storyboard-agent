"""End-to-end check of the gates, the money rules and the assembly, without network and without cost.

  python3 tests/test_workflow.py

Copies tools/ into a temporary folder and swaps the Wan HTTP layer for a fake that returns a small
synthetic clip. Everything else is the real code. Needs the same local tools as a normal run
(ffmpeg, whisper-cli + model, tools/ocr or tesseract).
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FAKE_LT = '''"""Test double: the real lt.py with the Wan HTTP layer replaced (no network, no cost)."""
import importlib.util, json, os, shutil, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("lt_real", HERE / "lt_real.py")
real = importlib.util.module_from_spec(spec); spec.loader.exec_module(real)
env = real.env
CALLS = HERE.parent / "fake_wan_calls.jsonl"

def fake_call(method, url, key_, body=None):
    with open(CALLS, "a") as f:
        f.write(json.dumps({"method": method}) + "\\n")
    mode = os.environ.get("FAKE_WAN", "ok")
    if method == "POST":
        if mode == "reject":
            sys.exit("HTTP 400: rejected by the fake")
        posts = sum(1 for l in open(CALLS) if "POST" in l)
        return {"output": {"task_id": f"task-{posts}"}}
    if mode == "offline":
        raise real.Transient("connection reset (fake)")
    if mode == "failed":
        return {"output": {"task_status": "FAILED", "message": "fake failure"}}
    return {"output": {"task_status": "SUCCEEDED", "video_url": "fake://clip"}, "usage": {"duration": 7.5}}

real.wan_call = fake_call
real.wan_env = lambda: ("http://fake.invalid", "no-key")
real.urllib.request.urlretrieve = lambda url, out: shutil.copyfile(os.environ["FAKE_WAN_VIDEO"], out)
if __name__ == "__main__":
    real.main()
'''
STORY_SHOTS = [
    {"id": "S1", "link": "none", "duration": 6, "summary": "空镜一", "refs": [], "scenes": [{"seconds": 6, "desc": "灰色画面。"}],
     "start_state": "没有人"},
    {"id": "S2", "link": "extend", "duration": 2, "summary": "空镜二", "refs": [], "scenes": [{"seconds": 2, "desc": "灰色画面。"}]},
    {"id": "S3", "link": "lastframe", "duration": 6, "summary": "空镜三", "refs": [], "scenes": [{"seconds": 6, "desc": "灰色画面。"}]},
]
passed = 0


def check(name, cond, detail=""):
    global passed
    if not cond:
        sys.exit(f"FAIL  {name}\n{detail}")
    passed += 1
    print(f"ok    {name}")


def main():
    box = Path(tempfile.mkdtemp(prefix="storyboard-test-"))
    shutil.copytree(REPO / "tools", box / "tools", ignore=shutil.ignore_patterns("__pycache__"))
    (box / "tools/lt.py").rename(box / "tools/lt_real.py")
    (box / "tools/lt.py").write_text(FAKE_LT)
    proj, tools = box / "projects/demo", box / "tools"
    clip, silent = box / "fake.mp4", box / "fake_silent.mp4"
    src = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=gray:size=320x568:rate=30"]
    subprocess.run([*src, "-f", "lavfi", "-i", "sine=frequency=220", "-t", "6", "-c:v", "libx264", "-c:a", "aac", str(clip)], check=True)
    subprocess.run([*src, "-t", "6", "-c:v", "libx264", str(silent)], check=True)
    base_env = {**os.environ, "FAKE_WAN_VIDEO": str(clip), "WAN_POLL_SECONDS": "0.05", "WAN_POLL_RETRIES": "2",
                "TOKEN_STATION_API_KEY": "", "TOKEN_STATION_BASE_URL": "http://127.0.0.1:9"}

    def run(script, *args, **env):
        r = subprocess.run([sys.executable, str(tools / script), *map(str, args)], capture_output=True, text=True, env={**base_env, **env})
        return r.returncode, r.stdout + r.stderr

    def gate(*args):
        return run("gate.py", proj, *args)

    def shot(*args, **env):
        return run("run_shot.py", proj, *args, **env)

    def posts():
        f = box / "fake_wan_calls.jsonl"
        return sum(1 for l in f.read_text().splitlines() if "POST" in l) if f.exists() else 0

    def ledger(sid=None):
        return [e for e in json.loads((proj / "ledger.json").read_text()) if sid is None or e.get("shot") == sid]

    def status(g):
        return json.loads((proj / "workflow.json").read_text())["gates"].get(g, {}).get("status")

    def edit_story(fn):
        s = json.loads((proj / "story.json").read_text())
        fn(s)
        (proj / "story.json").write_text(json.dumps(s, ensure_ascii=False, indent=1))

    # --- gates: order, request before approve -------------------------------------------------
    code, out = run("new_project.py", "demo", "--ratio", "9:16", "--budget", "200")
    check("new project requests the source gate", code == 0 and status("source") == "pending", out)
    edit_story(lambda s: s.update(shots=STORY_SHOTS))
    check("a gate that was never requested cannot be approved", gate("approve", "board")[0] != 0 and status("board") is None)
    check("a later gate cannot be requested before the earlier ones pass", gate("request", "anchors_plan")[0] != 0)
    check("unknown gate names are refused", gate("request", "anchor_plan")[0] != 0)
    (box / "p.txt").write_text("x")
    code, out = run("lt.py", "image", "--out", proj / "anchors/a.png", "--prompt-file", box / "p.txt")
    check("anchor images are refused before the anchor plan is approved", code != 0 and "anchors_plan" in out, out)
    check("source can be approved once requested", gate("approve", "source", "--note", "ok")[0] == 0)
    check("planning a shot is refused before the board gate", shot("S1", "--plan")[0] != 0)
    for g in ("anchors_plan", "anchors_review", "voices_review", "board"):
        ok = gate("request", g)[0] == 0 and gate("approve", g)[0] == 0
        check(f"{g}: request then approve", ok)

    # --- one approval = one paid run; takes are never overwritten --------------------------------
    check("generating without a plan is refused", shot("S1")[0] != 0 and posts() == 0)
    check("plan S1", shot("S1", "--plan")[0] == 0 and status("plan:S1") == "pending")
    check("generating before the plan is approved is refused", shot("S1")[0] != 0 and posts() == 0)
    gate("approve", "plan:S1")
    code, out = shot("S1")
    e = ledger("S1")
    check("approved plan generates take 1 and records the billed seconds",
          code == 0 and posts() == 1 and len(e) == 1 and e[0]["take"] == 1 and e[0]["billed_s"] == 7.5 and e[0].get("task_id"), out)
    check("the plan approval is used up by that run", status("plan:S1") == "used" and status("review:S1") == "pending")
    check("a second run without a new approval is refused", shot("S1")[0] != 0 and posts() == 1)
    check("a used approval cannot be re-approved without a new plan", gate("approve", "plan:S1")[0] != 0)
    shot("S1", "--plan"); gate("approve", "plan:S1")
    code, out = shot("S1")
    check("re-roll after a new plan and approval becomes take 2, take 1 is kept",
          code == 0 and (proj / "clips/S1_take1.mp4").exists() and (proj / "clips/S1_take2.mp4").exists()
          and [x["take"] for x in ledger("S1")] == [1, 2], out)

    # --- a paid take is recorded even when QA crashes ----------------------------------------------
    shot("S1", "--plan"); gate("approve", "plan:S1")
    code, out = shot("S1", FAKE_WAN_VIDEO=str(silent))
    e = ledger("S1")[-1]
    check("a take whose QA crashes is still in the ledger and goes to review",
          e["take"] == 3 and e["billed_s"] == 7.5 and any("质检没跑完" in p for p in e["problems"]) and status("review:S1") == "pending", out)

    # --- accept needs the review gate, an end state and an existing clip ---------------------------
    check("accept is refused before the review is approved", shot("--accept", "S1", "--end-state", "x")[0] != 0)
    gate("approve", "review:S1")
    check("accept without an end state is refused", shot("--accept", "S1")[0] != 0)
    check("accept of a take that does not exist is refused", shot("--accept", "S1", "--end-state", "x", "--take", "9")[0] != 0)
    code, out = shot("--accept", "S1", "--end-state", "灰色画面，没有人", "--take", "1")
    st = json.loads((proj / "state.json").read_text())["S1"]
    check("accept records the chosen take and the end state",
          code == 0 and st["clip"] == "clips/S1_take1.mp4" and st["end_state"] == "灰色画面，没有人", out)

    # --- the approved prompt is the one that runs; dropped connections do not pay twice ----------
    shot("S2", "--plan"); gate("approve", "plan:S2")
    check("the end state reaches the next prompt with punctuation",
          "当前状态：灰色画面，没有人。全程保持" in (proj / "prompts/S2_plan.txt").read_text())
    edit_story(lambda s: s["shots"][1].update(summary="改过的空镜二"))
    n = posts()
    code, out = shot("S2")
    check("a prompt changed after approval is refused", code != 0 and posts() == n and "不一样" in out, out)
    shot("S2", "--plan"); gate("approve", "plan:S2")
    code, out = shot("S2", FAKE_WAN="offline")
    e = ledger("S2")
    pending = json.loads((proj / "state.json").read_text())["S2"].get("pending_task")
    check("a task lost to network trouble is recorded as submitted, with its task id",
          posts() == n + 1 and len(e) == 1 and e[0]["status"] == "已提交" and e[0]["task_id"] == pending["task_id"]
          and status("plan:S2") == "used", out)
    check("a new plan is refused while a submitted task is waiting", shot("S2", "--plan")[0] != 0)
    code, out = shot("S2")
    e = ledger("S2")
    check("running again fetches that task instead of paying for a new one",
          code == 0 and posts() == n + 1 and len(e) == 1 and e[0]["billed_s"] == 7.5 and e[0]["take"] == 1
          and "pending_task" not in json.loads((proj / "state.json").read_text())["S2"], out)
    gate("approve", "review:S2"); shot("--accept", "S2", "--end-state", "灰色画面。")

    # --- failures ---------------------------------------------------------------------------------
    shot("S3", "--plan"); gate("approve", "plan:S3")
    n = posts()
    code, out = shot("S3", FAKE_WAN="reject")
    check("a rejected submission costs nothing and keeps the approval",
          ledger("S3")[-1]["status"] == "失败" and "billed_s" not in ledger("S3")[-1] and status("plan:S3") == "approved", out)
    code, out = shot("S3", FAKE_WAN="failed")
    check("a task that fails on the server is recorded and uses up the approval",
          ledger("S3")[-1]["status"] == "失败" and ledger("S3")[-1].get("task_id") and status("plan:S3") == "used"
          and "pending_task" not in json.loads((proj / "state.json").read_text()).get("S3", {}), out)
    shot("S3", "--plan"); gate("approve", "plan:S3")
    code, out = shot("S3")
    check("last-frame shot generates after a new approval", code == 0 and (proj / "clips/S3_take3.mp4").exists(), out)
    gate("approve", "review:S3"); shot("--accept", "S3", "--end-state", "灰色画面。")

    # --- budget -------------------------------------------------------------------------------------
    spent = sum(x.get("billed_s", 0) for x in ledger())
    edit_story(lambda s: s.update(budget_billed_seconds=spent + 5))
    shot("S1", "--plan"); gate("approve", "plan:S1")
    n = posts()
    code, out = shot("S1")
    check("generation stops when the estimate would exceed the budget", posts() == n and '"stopped": "budget"' in out, out)

    # --- assembly keeps the footage's own size ------------------------------------------------------
    check("assembly is refused before its gate", run("assemble.py", proj, proj / "exports/out.mp4")[0] != 0)
    gate("request", "assembly"); gate("approve", "assembly")
    code, out = run("assemble.py", proj, proj / "exports/out.mp4")
    size = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height:format=duration",
                           "-of", "csv=p=0", str(proj / "exports/out.mp4")], capture_output=True, text=True).stdout.split()
    check("a portrait project exports portrait, at the clips' own size", code == 0 and size[0] == "320,568", f"{out}{size}")
    check("the previous shot's tail is trimmed only before an extend shot", abs(float(size[1]) - 14) < 0.2, str(size))
    code, out = run("assemble.py", proj, proj / "exports/out2.mp4", "--crop-subs")
    check("subtitle crop keeps the same output size", code == 0, out)

    # --- rejecting an earlier gate blocks everything after it --------------------------------------
    gate("reject", "board", "--note", "改分镜")
    check("after the board is rejected, planning and assembly are refused",
          shot("S1", "--plan")[0] != 0 and run("assemble.py", proj, proj / "exports/out3.mp4")[0] != 0)

    shutil.rmtree(box)
    print(f"\n{passed} checks passed")


if __name__ == "__main__":
    main()
