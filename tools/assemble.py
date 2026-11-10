"""Assemble accepted shots in story order.

  assemble.py <project_dir> <out.mp4> [--upto S4]
Rule for video extension: keep each shot's full raw output and cut the previous shot's
last `continuation_seconds` (the next shot re-renders that overlap, including any new dialogue).
"""
import argparse
import json
import subprocess
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("project"); ap.add_argument("out"); ap.add_argument("--upto")
ap.add_argument("--crop-subs", action="store_true", help="crop bottom 17%% and scale back (removes burned-in subtitles)")
a = ap.parse_args()
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
import gate
gate.require(a.project, "assembly")
proj = Path(a.project).resolve()
story = json.loads((proj / "story.json").read_text())
state = json.loads((proj / "state.json").read_text())
shots = [s for s in story["shots"] if state.get(s["id"], {}).get("status") == "accepted"]
if a.upto:
    ids = [s["id"] for s in story["shots"]]
    shots = [s for s in shots if ids.index(s["id"]) <= ids.index(a.upto)]
ov = story.get("continuation_seconds", 4)
crop = "crop=iw*0.83:ih*0.83:iw*0.085:0," if a.crop_subs else ""
inputs, chains = [], []
for i, s in enumerate(shots):
    clip = proj / state[s["id"]]["clip"]
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(clip)],
                               capture_output=True, text=True).stdout)
    nxt = shots[i + 1] if i + 1 < len(shots) else None
    trim_tail = nxt and nxt.get("link") == "extend" and not state[nxt["id"]].get("head_trimmed")
    end = dur - ov if trim_tail else dur
    inputs += ["-i", str(clip)]
    chains.append(f"[{i}:v:0]trim=0:{end:.3f},setpts=PTS-STARTPTS,{crop}scale=1280:720:force_original_aspect_ratio=increase,"
                  f"crop=1280:720,fps=30,setsar=1[v{i}];[{i}:a:0]atrim=0:{end:.3f},asetpts=PTS-STARTPTS,"
                  f"aresample=48000,aformat=channel_layouts=stereo[a{i}];")
graph = "".join(chains) + "".join(f"[v{i}][a{i}]" for i in range(len(shots))) + f"concat=n={len(shots)}:v=1:a=1[v][a]"
subprocess.run(["ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex", graph, "-map", "[v]", "-map", "[a]",
                "-c:v", "libx264", "-crf", "14", "-preset", "slow", "-c:a", "aac", "-b:a", "256k", a.out], check=True)
print(json.dumps({"out": a.out, "shots": [s["id"] for s in shots]}))
