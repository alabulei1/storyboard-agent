"""Human confirmation gates. Downstream tools refuse to run until the required gate is approved.

  gate.py <project> status
  gate.py <project> request <gate> --summary "<what the human is asked to confirm>"
  gate.py <project> approve <gate> [--note "<human's words>"]
  gate.py <project> reject  <gate> --note "<human's reason / requested changes>"

Gates: source, anchors_plan, anchors_review, voices_review, board, plan:<shot>, review:<shot>, assembly
Approvals are only recorded after the human confirms in chat; the brain never self-approves.
"""
import json
import sys
import time
from pathlib import Path

ORDER = ["source", "anchors_plan", "anchors_review", "voices_review", "board"]


def path(proj):
    return Path(proj).resolve() / "workflow.json"


def load(proj):
    p = path(proj)
    return json.loads(p.read_text()) if p.exists() else {"gates": {}, "log": []}


def save(proj, wf):
    path(proj).write_text(json.dumps(wf, ensure_ascii=False, indent=1))


def is_approved(proj, gate):
    return load(proj)["gates"].get(gate, {}).get("status") == "approved"


def require(proj, *gates):
    missing = [g for g in gates if not is_approved(proj, g)]
    if missing:
        sys.exit("等待人工确认：" + "、".join(missing) + "（先在对话中请用户确认，再执行 gate.py approve）")


def set_status(proj, gate, status, note="", summary=""):
    wf = load(proj)
    g = wf["gates"].setdefault(gate, {})
    g.update({"status": status, "at": time.strftime("%Y-%m-%d %H:%M:%S")})
    if note:
        g["note"] = note
    if summary:
        g["summary"] = summary
    wf["log"].append({"gate": gate, "status": status, "note": note, "summary": summary, "at": g["at"]})
    save(proj, wf)
    print(json.dumps({gate: g}, ensure_ascii=False))


if __name__ == "__main__":
    proj, cmd = sys.argv[1], sys.argv[2]
    args = sys.argv[3:]
    opt = lambda k: args[args.index(k) + 1] if k in args else ""
    if cmd == "status":
        wf = load(proj)
        for k, v in wf["gates"].items():
            print(f"{k:18} {v['status']:10} {v.get('at', '')}  {v.get('note', '')[:60]}")
    elif cmd == "request":
        set_status(proj, args[0], "pending", summary=opt("--summary"))
    elif cmd == "approve":
        set_status(proj, args[0], "approved", note=opt("--note"))
    elif cmd == "reject":
        set_status(proj, args[0], "rejected", note=opt("--note"))
