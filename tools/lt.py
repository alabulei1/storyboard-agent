"""Short-drama workflow tools (Claude is the planner; this file only executes).

  lt.py image     --out a.png --prompt-file p.txt [--ref x.png ...] [--size 1536x1024]
  lt.py video     --out s1.mp4 --prompt-file p.txt --model h3|grok15
                  [--first-frame f.jpg] [--ref-video tail.mp4] [--ref-image a.png ...]
                  [--duration 10] [--resolution 720p]
  lt.py wan       --out s1.mp4 --prompt-file p.txt [--first-frame f.png | --ref-video tail.mp4]
                  [--ref-image a.png ...] [--ref-audio v.mp3 ...] [--duration 10]
  lt.py wan       --out s1.mp4 --resume <task_id>           (fetch a task that was already submitted and paid for)
  lt.py tail      --in s1.mp4 --sec 4 --out s1_tail.mp4     (continuation anchor for video extension)
  lt.py lastframe --in s1.mp4 --out s1_last.png             (continuation anchor for last-frame mode)
  lt.py concat    --out final.mp4 clip1.mp4 clip2.mp4 ...   (16:9 1280x720, keeps audio)
  lt.py probe     --in s1.mp4

Keys are read from <repo>/.env (see .env.example); never printed.
`wan` exits with code 75 when a submitted task could not be fetched (network trouble, still running):
the task is paid for, so fetch it with --resume instead of submitting again.
"""
import argparse
import base64
import json
import mimetypes
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

BASE = "https://bec.bytefuture.ai"  # Token Station gateway (override with TOKEN_STATION_BASE_URL)
MODELS = {"h3": "minimax/MiniMax-H3", "grok15": "xai/grok-imagine-video-1.5"}


ROOT = Path(__file__).resolve().parent.parent
RESUMABLE = 75  # exit code: the task was submitted (and is billed) but its result was not fetched


def env():
    """Settings from <repo>/.env; real environment variables take precedence."""
    import os
    vals = {}
    f = ROOT / ".env"
    if f.exists():
        for line in f.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                vals[k.strip()] = v.strip().strip("\"'")
    vals.update({k: v for k, v in os.environ.items() if k in vals or k.startswith(("TOKEN_STATION", "WAN_", "ARK_", "WHISPER"))})
    return vals


def key():
    k = env().get("TOKEN_STATION_API_KEY", "")
    if not k:
        sys.exit("TOKEN_STATION_API_KEY missing: set it in .env (see .env.example)")
    return k


def data_uri(path):
    mime = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    return f"data:{mime};base64," + base64.b64encode(Path(path).read_bytes()).decode()


def request(path, body=None, files=None, timeout=900):
    headers = {"Authorization": f"Bearer {key()}"}
    if files is not None:
        boundary = uuid.uuid4().hex
        parts = []
        for name, value in body.items():
            parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode())
        for name, fpath in files:
            mime = mimetypes.guess_type(str(fpath))[0] or "image/png"
            parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"; filename=\"{Path(fpath).name}\"\r\n"
                         f"Content-Type: {mime}\r\n\r\n".encode() + Path(fpath).read_bytes() + b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode())
        data = b"".join(parts)
        headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
    else:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(env().get("TOKEN_STATION_BASE_URL", BASE).rstrip("/") + path, data, headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        sys.exit(f"HTTP {e.code}: {e.read().decode()[:600]}")


def save_item(item, out):
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    if item.get("b64_json"):
        out.write_bytes(base64.b64decode(item["b64_json"]))
    else:
        urllib.request.urlretrieve(item["url"], out)


def project_of(path):
    """The project folder (<repo>/projects/<name>) a file belongs to, or None."""
    try:
        rel = Path(path).resolve().relative_to(ROOT / "projects")
    except ValueError:
        return None
    return ROOT / "projects" / rel.parts[0] if len(rel.parts) > 1 else None


def cmd_image(a):
    proj = project_of(a.out)
    if proj and (proj / "workflow.json").exists():  # anchor images cost money: the anchor plan must be approved first
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import gate
        gate.require(str(proj), "anchors_plan")
    prompt = Path(a.prompt_file).read_text()
    t = time.time()
    if a.ref:
        field = "image[]" if len(a.ref) > 1 else "image"
        res = request("/v1/images/edits", {"model": "openai/gpt-image-2", "prompt": prompt, "n": "1", "size": a.size},
                      files=[(field, r) for r in a.ref], timeout=300)
    else:
        res = request("/v1/images/generations", {"model": "openai/gpt-image-2", "prompt": prompt, "n": 1, "size": a.size}, timeout=300)
    save_item(res["data"][0], a.out)
    print(json.dumps({"out": a.out, "seconds": round(time.time() - t), "usage": res.get("usage", {}).get("output_tokens")}))


def cmd_video(a):
    body = {"model": MODELS[a.model], "prompt": Path(a.prompt_file).read_text(), "duration": a.duration}
    if a.model == "h3":
        body["aspect_ratio"] = a.aspect or ("adaptive" if (a.first_frame or a.ref_video or a.ref_image) else "16:9")
        if a.first_frame:
            body["image"] = data_uri(a.first_frame)
        if a.ref_video:
            body["reference_videos"] = [data_uri(v) for v in a.ref_video]
        if a.ref_image:
            body["reference_images"] = [data_uri(i) for i in a.ref_image]
    else:
        body["resolution"] = a.resolution
        if a.first_frame:
            body["image"] = data_uri(a.first_frame)
        else:
            body["aspect_ratio"] = "16:9"
        if a.ref_video or a.ref_image:
            sys.exit("grok15 accepts only --first-frame")
    t = time.time()
    res = request("/v1/video/generations", body)
    save_item(res["data"][0], a.out)
    print(json.dumps({"out": a.out, "model": body["model"], "seconds": round(time.time() - t), **probe(a.out)}))


def wan_env():
    e = env()
    if not e.get("WAN_API_KEY") or not e.get("WAN_BASE_URL"):
        sys.exit("WAN_API_KEY / WAN_BASE_URL missing: set them in .env (see .env.example)")
    host = e["WAN_BASE_URL"].split("/compatible-mode")[0].rstrip("/")
    return host, e["WAN_API_KEY"]


class Transient(Exception):
    """A network or server hiccup: the request may be retried."""


def wan_call(method, url, key_, body=None):
    headers = {"Authorization": f"Bearer {key_}", "Content-Type": "application/json"}
    if body is not None:
        headers["X-DashScope-Async"] = "enable"
    req = urllib.request.Request(url, json.dumps(body).encode() if body is not None else None, headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        msg = f"HTTP {e.code}: {e.read().decode()[:600]}"
        if method == "GET" and (e.code == 429 or e.code >= 500):
            raise Transient(msg)
        sys.exit(msg)
    except (urllib.error.URLError, OSError, ValueError) as e:  # connection reset, timeout, truncated body
        if method == "GET":
            raise Transient(str(e))
        sys.exit(f"network error: {e}")  # submissions are never retried automatically (could bill twice)


def wan_give_up(tid, why):
    print(f"Wan task {tid} was submitted but not fetched ({why}). It is billed; fetch it with: "
          f"lt.py wan --resume {tid} --out <file>", file=sys.stderr)
    sys.exit(RESUMABLE)


def cmd_wan(a):
    host, key_ = wan_env()
    e = env()
    interval, max_fail, max_wait = float(e.get("WAN_POLL_SECONDS", 15)), int(e.get("WAN_POLL_RETRIES", 20)), float(e.get("WAN_MAX_WAIT", 3600))
    t = time.time()
    tid = a.resume or wan_submit(a, host, key_)
    print(json.dumps({"task_id": tid}), flush=True)
    fails = 0
    while True:
        time.sleep(interval)
        try:
            st = wan_call("GET", f"{host}/api/v1/tasks/{tid}", key_)
            fails = 0
        except Transient as err:
            fails += 1
            if fails >= max_fail:
                wan_give_up(tid, f"{fails} failed status checks, last: {err}")
            continue
        status = st["output"].get("task_status")
        if status == "SUCCEEDED":
            for attempt in range(3):
                try:
                    urllib.request.urlretrieve(st["output"]["video_url"], a.out)
                    break
                except (urllib.error.URLError, OSError) as err:
                    if attempt == 2:
                        wan_give_up(tid, f"download failed: {err}")
                    time.sleep(5)
            print(json.dumps({"out": a.out, "model": a.wan_model, "seconds": round(time.time() - t), "task_id": tid,
                              "usage": st.get("usage"), **probe(a.out)}))
            return
        if status in ("FAILED", "CANCELED", "UNKNOWN"):
            sys.exit("Wan task " + status + ": " + json.dumps(st["output"], ensure_ascii=False)[:600])
        if time.time() - t > max_wait:
            wan_give_up(tid, f"still {status} after {max_wait:g} s")


def wan_submit(a, host, key_):
    if not a.prompt_file:
        sys.exit("--prompt-file is required (or --resume <task_id>)")
    media = []
    if a.first_frame:
        media.append({"type": "first_frame", "url": data_uri(a.first_frame)})
    for v in a.ref_video or []:
        media.append({"type": "reference_video", "url": data_uri(v)})
    for i in a.ref_image or []:
        media.append({"type": "reference_image", "url": data_uri(i)})
    for au in a.ref_audio or []:
        media.append({"type": "reference_audio", "url": data_uri(au)})
    body = {"model": a.wan_model, "input": {"prompt": Path(a.prompt_file).read_text()},
            "parameters": {"resolution": a.resolution.upper(), "ratio": a.aspect or ("adaptive" if (a.first_frame or a.ref_video) else "16:9"),
                           "duration": a.duration, "prompt_extend": not a.no_prompt_extend}}
    if media:
        body["input"]["media"] = media
    if a.negative:
        body["input"]["negative_prompt"] = a.negative
    task = wan_call("POST", f"{host}/api/v1/services/aigc/video-generation/video-synthesis", key_, body)
    return task["output"]["task_id"]


def ff(*args):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


def probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height:format=duration", "-of", "json", str(path)],
                         capture_output=True, text=True).stdout
    d = json.loads(out)
    s = d.get("streams", [{}])[0]
    return {"w": s.get("width"), "h": s.get("height"), "duration": round(float(d["format"]["duration"]), 2)}


def cmd_tail(a):
    # Blur the subtitle band (bottom 8%–18%, centre 25%–75%) so burned-in captions
    # in the previous shot are not carried into the continuation.
    scale = ",scale=640:-2" if a.compress else ""
    vf = ("split[base][band];[band]crop=iw*0.5:ih*0.11:iw*0.25:ih*0.81,boxblur=luma_radius=18:luma_power=2:chroma_radius=9:chroma_power=2[blur];"
          "[base][blur]overlay=W*0.25:H*0.81" + scale) if a.clean_subs else (scale.lstrip(",") or "null")
    quality = ["-crf", "24", "-c:a", "aac", "-b:a", "96k"] if a.compress else ["-crf", "10", "-preset", "slow", "-c:a", "aac", "-b:a", "256k"]
    ff("-sseof", f"-{a.sec}", "-i", a.inp, "-map", "0:v:0", "-map", "0:a:0?", "-vf", vf,
       "-c:v", "libx264", *quality, a.out)
    print(json.dumps({"out": a.out, **probe(a.out)}))


def cmd_lastframe(a):
    out = a.out if a.out.endswith(".png") else a.out.rsplit(".", 1)[0] + ".png"
    # Decode the last ~0.3 s and keep overwriting, so the file ends up as the true last frame.
    ff("-sseof", "-0.3", "-i", a.inp, "-map", "0:v:0", "-update", "1", out)
    if not Path(out).exists():
        sys.exit(f"could not extract last frame from {a.inp}")
    a.out = out
    print(json.dumps({"out": a.out}))


def cmd_concat(a):
    inputs, chains = [], []
    for i, clip in enumerate(a.clips):
        inputs += ["-i", clip]
        chains.append(f"[{i}:v:0]scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720,fps=30,setsar=1[v{i}];"
                      f"[{i}:a:0]aresample=48000,aformat=channel_layouts=stereo[a{i}];")
    graph = "".join(chains) + "".join(f"[v{i}][a{i}]" for i in range(len(a.clips))) + f"concat=n={len(a.clips)}:v=1:a=1[v][a]"
    ff(*inputs, "-filter_complex", graph, "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-crf", "14", "-preset", "slow",
       "-c:a", "aac", "-b:a", "256k", a.out)
    print(json.dumps({"out": a.out, **probe(a.out)}))


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("image"); s.add_argument("--out", required=True); s.add_argument("--prompt-file", required=True)
    s.add_argument("--ref", nargs="*"); s.add_argument("--size", default="1536x1024"); s.set_defaults(fn=cmd_image)
    s = sub.add_parser("video"); s.add_argument("--out", required=True); s.add_argument("--prompt-file", required=True)
    s.add_argument("--model", choices=list(MODELS), required=True); s.add_argument("--first-frame")
    s.add_argument("--ref-video", nargs="*"); s.add_argument("--ref-image", nargs="*")
    s.add_argument("--duration", type=int, default=10); s.add_argument("--resolution", default="720p")
    s.add_argument("--aspect", help="override aspect_ratio, e.g. 16:9 or adaptive"); s.set_defaults(fn=cmd_video)
    s = sub.add_parser("wan"); s.add_argument("--out", required=True); s.add_argument("--prompt-file")
    s.add_argument("--resume", metavar="TASK_ID", help="fetch an already submitted task instead of submitting a new one")
    s.add_argument("--wan-model", default="wan3.0-video"); s.add_argument("--first-frame"); s.add_argument("--ref-video", nargs="*")
    s.add_argument("--ref-image", nargs="*"); s.add_argument("--ref-audio", nargs="*"); s.add_argument("--duration", type=int, default=10)
    s.add_argument("--resolution", default="720P"); s.add_argument("--aspect"); s.add_argument("--negative")
    s.add_argument("--no-prompt-extend", action="store_true", help="prompt_extend is on by default; off causes burned-in subtitles on dialogue shots"); s.set_defaults(fn=cmd_wan)
    s = sub.add_parser("tail"); s.add_argument("--in", dest="inp", required=True); s.add_argument("--sec", type=float, default=4)
    s.add_argument("--out", required=True); s.add_argument("--clean-subs", action="store_true")
    s.add_argument("--compress", action="store_true", help="only for size-limited sync gateways (Token Station)"); s.set_defaults(fn=cmd_tail)
    s = sub.add_parser("lastframe"); s.add_argument("--in", dest="inp", required=True); s.add_argument("--out", required=True)
    s.set_defaults(fn=cmd_lastframe)
    s = sub.add_parser("concat"); s.add_argument("--out", required=True); s.add_argument("clips", nargs="+"); s.set_defaults(fn=cmd_concat)
    s = sub.add_parser("probe"); s.add_argument("--in", dest="inp", required=True); s.set_defaults(fn=lambda a: print(json.dumps(probe(a.inp))))
    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
