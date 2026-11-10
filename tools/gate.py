"""Human confirmation gates. Downstream tools refuse to run until the required gate is approved.

  gate.py <project> status
  gate.py <project> request <gate> --summary "<what the human is asked to confirm>"
  gate.py <project> approve <gate> [--note "<human's words>"]
  gate.py <project> reject  <gate> --note "<human's reason / requested changes>"

Gates: source, anchors_plan, anchors_review, voices_review, board, plan:<shot>, review:<shot>, assembly
Rules enforced here:
- Order: a gate can only be requested or approved after every earlier gate in ORDER is approved;
  plan:<shot>, review:<shot> and assembly need all of ORDER. If an earlier gate is rejected or
  re-requested later, everything after it stops counting as approved until it is approved again.
- Only a requested (pending) gate can be approved. A plan:<shot> approval covers one run of
  run_shot.py: it becomes "used" once a paid task is submitted, so a re-roll needs --plan and a new approval.
Approvals are only recorded after the human confirms in chat; the brain never self-approves.
"""
import json
import os
import sys
import time
from pathlib import Path

ORDER = ["source", "anchors_plan", "anchors_review", "voices_review", "board"]
LABEL = {"pending": "待确认", "approved": "已通过", "rejected": "未通过", "used": "已用掉", None: "未请求"}


def path(proj):
    return Path(proj).resolve() / "workflow.json"


def load(proj):
    p = path(proj)
    return json.loads(p.read_text()) if p.exists() else {"gates": {}, "log": []}


def save(proj, wf):
    tmp = path(proj).with_suffix(".json.tmp")
    tmp.write_text(json.dumps(wf, ensure_ascii=False, indent=1))
    os.replace(tmp, path(proj))


def check_name(gate):
    if not (gate in ORDER or gate == "assembly" or (gate.startswith(("plan:", "review:")) and gate.split(":", 1)[1])):
        sys.exit(f"没有这道关卡：{gate}（可用：{'、'.join(ORDER)}、plan:<镜号>、review:<镜号>、assembly）")


def prereqs(gate):
    """Gates that must be approved before this one can be requested, approved or relied on."""
    return ORDER[:ORDER.index(gate)] if gate in ORDER else list(ORDER)


def status(proj, gate, wf=None):
    return (wf or load(proj))["gates"].get(gate, {}).get("status")


def missing(proj, gate, wf=None):
    wf = wf or load(proj)
    return [g for g in [*prereqs(gate), gate] if status(proj, g, wf) != "approved"]


def is_approved(proj, gate):
    return not missing(proj, gate)


def require(proj, *gates):
    wf = load(proj)
    lack = []
    for gate in gates:
        for g in missing(proj, gate, wf):
            item = f"{g}（{LABEL.get(status(proj, g, wf), status(proj, g, wf))}）"
            if item not in lack:
                lack.append(item)
    if lack:
        sys.exit("等待人工确认：" + "、".join(lack) + "（先请求确认并在对话中请用户确认，再执行 gate.py approve）")


def set_status(proj, gate, status_, note="", summary=""):
    check_name(gate)
    wf = load(proj)
    if status_ in ("pending", "approved"):
        before = [g for g in prereqs(gate) if status(proj, g, wf) != "approved"]
        if before:
            sys.exit(f"{gate} 之前的关卡还没通过：" + "、".join(before))
    if status_ == "approved" and status(proj, gate, wf) != "pending":
        now = status(proj, gate, wf)
        hint = "这次批准已经用掉，重抽要先重新 run_shot.py --plan 再请用户确认" if now == "used" else "先请求确认（gate.py request 或对应工具），再请用户确认"
        sys.exit(f"{gate} 现在是「{LABEL.get(now, now)}」，不能直接批准：{hint}")
    if status_ == "rejected" and status(proj, gate, wf) is None:
        sys.exit(f"{gate} 还没有请求过确认")
    g = wf["gates"].setdefault(gate, {})
    g.update({"status": status_, "at": time.strftime("%Y-%m-%d %H:%M:%S")})
    if note:
        g["note"] = note
    if summary:
        g["summary"] = summary
    wf["log"].append({"gate": gate, "status": status_, "note": note, "summary": summary, "at": g["at"]})
    save(proj, wf)
    print(json.dumps({gate: g}, ensure_ascii=False))


if __name__ == "__main__":
    if len(sys.argv) < 3 or sys.argv[2] not in ("status", "request", "approve", "reject") or (sys.argv[2] != "status" and len(sys.argv) < 4):
        sys.exit(__doc__)
    proj, cmd = sys.argv[1], sys.argv[2]
    if not Path(proj).is_dir():
        sys.exit(f"找不到项目：{proj}")
    args = sys.argv[3:]
    opt = lambda k: args[args.index(k) + 1] if k in args and args.index(k) + 1 < len(args) else ""
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
