"""Check story.json before it is shown to the human or used to spend money.

  check_story.py <project_dir | story.json> [--stage anchors_plan|anchors_review|voices_review|board] [--no-files]

Prints the shot table (link, seconds, estimated billed seconds, speaking time) and every problem found;
exit code 1 if there are errors. Errors block, warnings are for the human to judge.
The same check runs inside gate.py when anchors_plan, anchors_review, voices_review or board is
requested or approved, and inside run_shot.py before a shot is planned or generated, so a broken
story.json is caught before any money is spent.

Stages add up: anchors_plan = settings and anchor entries; anchors_review = + anchor image files exist;
voices_review = + voice sample files exist; board = + style, sound and every shot.
--no-files skips the "file exists" checks (for a story.json outside a project, like the example).
"""
import argparse
import json
import re
import sys
from pathlib import Path

STAGES = ["anchors_plan", "anchors_review", "voices_review", "board"]
LINKS = ("none", "extend", "lastframe")
MAX_SECONDS = 30        # Wan 3.0: longest single clip, continuation overlap included (SPEC 2)
MAX_IMAGES = 10         # Wan 3.0: reference images per clip (SPEC 7)
MAX_AUDIOS = 5          # Wan 3.0: reference voices per clip (SPEC 7)
CHARS_PER_SECOND = 4.5  # spoken Chinese (SPEC 2)


def is_num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def is_text(x):
    return isinstance(x, str) and bool(x.strip())


def speech_seconds(shot):
    lines = shot.get("dialogue") if isinstance(shot.get("dialogue"), list) else []
    chars = sum(len(re.sub(r"[^一-鿿A-Za-z0-9]", "", d.get("text", ""))) for d in lines
                if isinstance(d, dict) and isinstance(d.get("text"), str))
    return chars / CHARS_PER_SECOND


def billed_estimate(story, shot):
    """Same figure as run_shot.py: output seconds, plus the reference clip again as input for extend."""
    overlap = story.get("continuation_seconds", 4) if shot.get("link") == "extend" else 0
    return shot["duration"] + 2 * overlap


def check(proj, stage="board", files=True, story=None):
    """(errors, warnings) for story.json at the given stage."""
    if story is None:
        try:
            story = json.loads((Path(proj) / "story.json").read_text())
        except (OSError, ValueError) as e:
            return [f"story.json 读不出来：{e}"], []
    if not isinstance(story, dict):
        return ["story.json 最外层必须是一个对象"], []
    level = STAGES.index(stage)
    errors, warnings = [], []

    if not is_text(story.get("ratio")):
        errors.append("缺少 ratio（画幅，例如 16:9 或 9:16）")
    overlap = story.get("continuation_seconds", 4)
    if not (isinstance(overlap, int) and not isinstance(overlap, bool) and overlap >= 1):
        errors.append("continuation_seconds（续写承接秒数）必须是正整数")
        overlap = 4
    budget = story.get("budget_billed_seconds")
    if budget is None:
        warnings.append("没有设置 budget_billed_seconds（预算），生成不会自动停下")
    elif not (is_num(budget) and budget > 0):
        errors.append("budget_billed_seconds（预算）必须是正数")
        budget = None

    anchors = story.get("anchors")
    if not isinstance(anchors, dict):
        errors.append("anchors 必须是对象（可以为空）")
        anchors = {}
    for key, a in anchors.items():
        if not re.fullmatch(r"[\w-]+", key):
            errors.append(f"锚点键「{key}」只能用字母、数字、中文、- 或 _")
        if not isinstance(a, dict):
            errors.append(f"锚点 {key} 必须是对象")
            continue
        if not is_text(a.get("name")):
            errors.append(f"锚点 {key} 缺少 name（提示词里怎么称呼它）")
        for field in ("image", "voice"):
            if field in a and not is_text(a[field]):
                errors.append(f"锚点 {key} 的 {field} 必须是相对项目目录的文件路径")
        if is_text(a.get("image")):
            if not is_text(a.get("use")):
                errors.append(f"锚点 {key} 有参考图，缺少 use（这张图用来确定什么）")
            if files and level >= 1 and not (Path(proj) / a["image"]).is_file():
                errors.append(f"锚点 {key} 的参考图不存在：{a['image']}")
        if is_text(a.get("voice")):
            if not is_text(a.get("voice_text")):
                errors.append(f"锚点 {key} 有音色样音，缺少 voice_text（样音里说的原话，质检用它检查有没有混进台词）")
            if files and level >= 2 and not (Path(proj) / a["voice"]).is_file():
                errors.append(f"锚点 {key} 的音色样音不存在：{a['voice']}")
    if level < 3:
        return errors, warnings

    if not is_text(story.get("style")):
        errors.append("缺少 style（全片统一的视觉风格，每条都会原句带上）")
    if not is_text(story.get("sound")):
        errors.append("缺少 sound（全片默认的音效说明；没有声音也要写明）")
    neg = story.get("negative")
    if not (isinstance(neg, list) and all(is_text(n) for n in neg)):
        errors.append("negative（负向清单）必须是文字列表（可以为空）")
    shots = story.get("shots")
    if not (isinstance(shots, list) and shots):
        errors.append("shots 为空：还没有分镜")
        return errors, warnings

    seen, total_est = set(), 0
    for i, shot in enumerate(shots):
        if not isinstance(shot, dict) or not is_text(shot.get("id")):
            errors.append(f"第 {i + 1} 条分镜缺少 id")
            continue
        sid = shot["id"]
        err = lambda msg: errors.append(f"{sid}：{msg}")
        warn = lambda msg: warnings.append(f"{sid}：{msg}")
        if not re.fullmatch(r"[\w-]+", sid):
            err("id 只能用字母、数字、中文、- 或 _")
        if sid in seen:
            err("id 重复")
        seen.add(sid)

        link = shot.get("link")
        if link not in LINKS:
            err(f"link 必须是 {' / '.join(LINKS)} 之一")
        elif i == 0 and link != "none":
            err("第一条没有上一条可接，link 只能是 none")
        dur = shot.get("duration")
        if not (isinstance(dur, int) and not isinstance(dur, bool) and dur >= 1):
            err("duration（新内容的秒数）必须是正整数")
            dur = None
        else:
            clip = dur + (overlap if link == "extend" else 0)
            if clip > MAX_SECONDS:
                err(f"单条 {clip} 秒" + (f"（含承接 {overlap} 秒）" if link == "extend" else "") + f"，超过模型上限 {MAX_SECONDS} 秒，拆成两条")
            total_est += dur + (2 * overlap if link == "extend" else 0)
        if not is_text(shot.get("summary")):
            err("缺少 summary（一句话说明这条拍什么）")
        for field in ("summary", "start_state"):
            used = [k for k in anchors if "{" + k + "}" in str(shot.get(field, ""))]
            if used:
                err(f"{field} 里的 {{{used[0]}}} 不会被替换成参考图编号，直接写名字（{{角色键}} 只在 scenes 的 desc 里有效）")

        refs = shot.get("refs", [])
        if not isinstance(refs, list):
            err("refs 必须是锚点键的列表")
            refs = []
        elif link == "lastframe":
            if refs:
                warn("尾帧接首帧不能带参考图，refs 不会被发送")
        else:
            if "refs" not in shot:
                err("缺少 refs（本条画面里出现的锚点；没有就写空列表）")
            for k in refs:
                if not isinstance(k, str) or k not in anchors:
                    err(f"refs 里的 {k} 不在 anchors 里")
                elif not is_text(anchors[k].get("image") if isinstance(anchors[k], dict) else None):
                    err(f"refs 里的 {k} 没有参考图")
            if len(set(map(str, refs))) != len(refs):
                err("refs 里有重复的锚点")
            if len(refs) > MAX_IMAGES:
                err(f"参考图 {len(refs)} 张，超过上限 {MAX_IMAGES} 张")

        scenes = shot.get("scenes")
        if not (isinstance(scenes, list) and scenes):
            err("缺少 scenes（分镜时间段）")
        else:
            secs = 0
            for n, sc in enumerate(scenes, 1):
                if not isinstance(sc, dict) or not (is_num(sc.get("seconds")) and sc["seconds"] > 0) or not is_text(sc.get("desc")):
                    err(f"scenes 第 {n} 段要有正数的 seconds 和非空的 desc")
                    secs = None
                    break
                secs += sc["seconds"]
                for k in re.findall(r"\{([^{}]*)\}", sc["desc"]):
                    if k not in anchors:
                        err(f"scenes 第 {n} 段的 {{{k}}} 不在 anchors 里")
                    elif link != "lastframe" and k not in refs and isinstance(anchors[k], dict) and is_text(anchors[k].get("image")):
                        warn(f"画面里提到 {k}，但 refs 没带它的参考图（会只用名字称呼）")
            if secs is not None and dur is not None and abs(secs - dur) > 0.01:
                err(f"scenes 的秒数加起来是 {secs:g}，和 duration {dur} 不一致")

        dia = shot.get("dialogue", [])
        if not isinstance(dia, list):
            err("dialogue 必须是列表")
            dia = []
        voiced = []
        for n, d in enumerate(dia, 1):
            if not isinstance(d, dict) or not is_text(d.get("text")):
                err(f"dialogue 第 {n} 句缺少 text（原文台词）")
                continue
            who = d.get("who")
            if not isinstance(who, str) or who not in anchors:
                err(f"dialogue 第 {n} 句的 who「{who}」不在 anchors 里")
                continue
            a = anchors[who] if isinstance(anchors[who], dict) else {}
            if "verb" in d and not is_text(d["verb"]):
                err(f"dialogue 第 {n} 句的 verb 不能为空（不写就默认是“说”）")
            if link != "lastframe":
                if is_text(a.get("voice")) and who not in voiced:
                    voiced.append(who)
                if is_text(a.get("image")) and who not in refs:
                    warn(f"{who} 有台词，但 refs 没带他的参考图")
        if len(voiced) > MAX_AUDIOS:
            err(f"有音色的说话人 {len(voiced)} 个，超过上限 {MAX_AUDIOS} 个")
        if dia and link == "lastframe":
            warn("尾帧接首帧带不了音色样音，这条的嗓音可能和前面不一样")
        need = speech_seconds(shot)
        if dur is not None and need > dur:
            warn(f"台词按每秒 {CHARS_PER_SECOND:g} 字要说 {need:.1f} 秒，超过这条的 {dur} 秒")
        if "negative" in shot and not (isinstance(shot["negative"], list) and all(is_text(x) for x in shot["negative"])):
            err("negative 必须是文字列表")
        if "sound" in shot and not is_text(shot["sound"]):
            err("sound 不能为空（不写就用全片的 sound）")

    if budget is not None and total_est > budget:
        warnings.append(f"每条只生成一次也要计费约 {total_est:g} 秒，超过预算 {budget:g} 秒")
    return errors, warnings


def table(story):
    """One line per shot, for the human at the board gate."""
    rows, total = [], 0
    for s in story.get("shots") or []:
        try:
            est = billed_estimate(story, s)
            total += est
            rows.append(f"{s['id']:6} {s['link']:9} 新内容 {s['duration']:>2} 秒  预计计费 {est:>2} 秒  台词约 {speech_seconds(s):4.1f} 秒  {s.get('summary', '')}")
        except (KeyError, TypeError, ValueError):
            rows.append(f"{s.get('id', '?') if isinstance(s, dict) else '?'}  （这一条写得不完整）")
    if rows:
        rows.append(f"合计：每条生成一次预计计费约 {total:g} 秒，预算 {story.get('budget_billed_seconds', '未设')} 秒")
    return "\n".join(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path", help="project folder, or a story.json file")
    ap.add_argument("--stage", choices=STAGES, default="board")
    ap.add_argument("--no-files", action="store_true", help="do not check that anchor files exist")
    a = ap.parse_args()
    p = Path(a.path).resolve()
    f = p if p.is_file() else p / "story.json"
    try:
        story = json.loads(f.read_text())
    except (OSError, ValueError) as e:
        sys.exit(f"读不出 {f}：{e}")
    errors, warnings = check(f.parent, a.stage, files=not a.no_files, story=story)
    if a.stage == "board" and isinstance(story, dict):
        print(table(story))
    for w in warnings:
        print("提醒：" + w)
    for e in errors:
        print("错误：" + e)
    if errors:
        sys.exit(1)
    print(f"story.json 检查通过（{a.stage}）" + (f"，有 {len(warnings)} 条提醒" if warnings else ""))


if __name__ == "__main__":
    main()
