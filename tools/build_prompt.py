"""Build a Wan 3.0 prompt (official formula) for one shot from story.json.

  build_prompt.py <project_dir> <shot_id>   -> prints the prompt and the media lists as JSON

link: none（独立镜头）| extend（视频续写，上一条结尾作为视频1）| lastframe（上一条最后一帧作首帧，
不能再带参考图和音色，角色用名字称呼）。
Formula: 总体描述 → 参考素材（视频/图/音频 编号按上传顺序、分类计数）→ 当前状态 → 分镜（起止秒）
→ 台词（图N说："…"，逐句、第一句一字不差）→ 音色参考 + 口型同步 → 音效 → 风格 → 负向清单
"""
import json
import sys
from pathlib import Path


def build(proj: Path, sid: str):
    story = json.loads((proj / "story.json").read_text())
    shot = next(s for s in story["shots"] if s["id"] == sid)
    anchors = story["anchors"]
    overlap = story.get("continuation_seconds", 4) if shot.get("link") == "extend" else 0
    lastframe = shot.get("link") == "lastframe"      # Wan: first_frame cannot be mixed with reference_*

    images = [] if lastframe else list(shot["refs"])  # anchor keys, upload order
    img_no = {k: i + 1 for i, k in enumerate(images)}
    speakers = []
    for line in shot.get("dialogue", []):
        if line["who"] not in speakers:
            speakers.append(line["who"])
    voice_no = {} if lastframe else {w: i + 1 for i, w in enumerate(w for w in speakers if anchors[w].get("voice"))}

    def who(key):  # how a character is referred to in the prompt
        return f"图{img_no[key]}" if key in img_no else anchors[key]["name"]

    lines = []
    head = shot["summary"]
    lines.append(("将视频1向后延长，续写：" if overlap else "从首帧画面直接开始，接着拍：" if lastframe else "") + head)

    refs = []
    if overlap:
        refs.append("视频1 是上一段的结尾，从视频1的最后画面无缝接着拍")
    if lastframe:
        refs.append("首帧是上一段的最后一帧，人物长相、服装、场景、光线与首帧保持一致")
    for k in images:
        refs.append(f"图{img_no[k]} 是{anchors[k]['name']}（{anchors[k]['use']}）")
    for w, n in voice_no.items():
        refs.append(f"音频{n} 是{who(w)}的音色参考")
    if voice_no:
        refs.append("音频只用来确定嗓音，绝对不要说出任何音频里的内容")
    lines.append("参考素材：" + "；".join(refs) + "。")

    state = shot.get("start_state_actual") or shot.get("start_state")
    if state:
        lines.append("当前状态：" + state + "全程保持这些状态，除非分镜里写明变化。")

    t = 0.0
    total = overlap + shot["duration"]
    n = 0
    if overlap:
        n += 1
        lines.append(f"分镜{n}（0-{overlap:g}秒）：承接视频1的最后画面，人物姿态与位置不变。")
        t = overlap
    for sc in shot["scenes"]:
        n += 1
        t0, t1 = t, min(t + sc["seconds"], total)
        t = t1
        desc = sc["desc"]
        for k in anchors:
            desc = desc.replace("{" + k + "}", who(k))
        lines.append(f"分镜{n}（{t0:g}-{t1:g}秒）：{desc}")

    dia = shot.get("dialogue", [])
    if dia:
        first = dia[0]
        lines.append(f"台词（只有以下{len(dia)}句，按顺序说；第一句必须一字不差是\"{first['text']}\"）：")
        for d in dia:
            lines.append(f"{who(d['who'])}{d.get('verb', '说')}：\"{d['text']}\"")
        if voice_no:
            lines.append("，".join(f"{who(w)}音色参考音频{n}" for w, n in voice_no.items()) + "。口型同步。")
        else:
            lines.append("口型同步。")
    else:
        lines.append("无台词。")

    lines.append("音效：" + shot.get("sound", story["sound"]))
    lines.append("风格：" + story["style"] + "人物身份、数量、服装、道具归属和空间方向保持稳定。")
    negs = story["negative"] + shot.get("negative", [])
    lines.append("负向清单：" + "，".join(negs) + "。")

    media = {"ref_images": [str(proj / anchors[k]["image"]) for k in images],
             "ref_audios": [str(proj / anchors[w]["voice"]) for w in voice_no],
             "duration": int(round(total))}
    return "\n".join(lines), media


if __name__ == "__main__":
    prompt, media = build(Path(sys.argv[1]).resolve(), sys.argv[2])
    print(prompt)
    print(json.dumps(media, ensure_ascii=False))
