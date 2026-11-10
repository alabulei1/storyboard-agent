# storyboard-agent: novel → AI short drama, with Claude as the brain

[中文](README.md) | English

A workflow that turns a piece of fiction into an AI short drama. It covers:
- **Anchors** for characters, scenes, props, voices and continuation, so they stay consistent across shots.
- **Shot planning** based on how long the dialogue takes to speak.
- **Shot-by-shot generation**, using either video extension or last frame → first frame.
- **Automatic QA**, a cost ledger, and **a human approval gate at every step**.

- **Brain**: Claude Code. It reads `SPEC.md`, plans shots, writes prompts and reviews the results.
- **Executors**: the scripts in `tools/` call the models and handle trimming, QA and assembly.
- **Default models**:
  - images: openai/gpt-image-2 via Token Station;
  - video: Wan 3.0 via Alibaba Cloud Model Studio;
  - voice samples: xiaomi/mimo-v2.5-tts-voicedesign via Token Station.

> The operating manual (`SPEC.md`), the example story and the prompts sent to Wan are in Chinese. You can still talk to Claude in English, and it will reply in your language.

## 1. Install
**Requirements**: macOS (recommended) or Linux, Python 3.10+, ffmpeg, whisper.cpp (`whisper-cli`) with the `ggml-large-v3-turbo` model, and [Claude Code](https://claude.com/claude-code).

```bash
git clone https://github.com/alabulei1/storyboard-agent
cd storyboard-agent
./setup.sh             # checks dependencies; on macOS builds the subtitle detector tools/ocr
cp .env.example .env   # then fill in your own keys
```

**macOS:**
1. Run `brew install ffmpeg whisper-cpp`.
2. Download `ggml-large-v3-turbo.bin` from https://huggingface.co/ggerganov/whisper.cpp.
3. Put it anywhere under your home folder, or set `WHISPER_MODEL=<path>` in `.env`.

**Linux:** subtitle detection uses tesseract instead, so install the Chinese language pack (`tesseract-ocr-chi-sim`).

## 2. Keys (put them in `.env`; never commit or share it)
| Variable | Used for | Where to get it |
|---|---|---|
| `TOKEN_STATION_API_KEY` | gpt-image-2 anchor images, voice samples | Token Station (or point `TOKEN_STATION_BASE_URL` at another OpenAI-compatible gateway) |
| `WAN_API_KEY` | Wan 3.0 video | Alibaba Cloud Model Studio (Bailian) → API Key |
| `WAN_BASE_URL` | same | Your workspace endpoint, e.g. `https://<workspace>.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1`. Paste it exactly as the console shows it; the script strips `/compatible-mode/v1` itself. |
| `WHISPER_MODEL` | dialogue check (optional) | Path to the whisper.cpp ggml model. If empty, the script searches your home folder. |

## 3. Start a project
1. Open Claude Code in this folder by running `claude`.
2. Give it the story. You can paste it into the chat, or save it as a text file and give Claude the path.
3. In one message, tell it:
   - **project name**: letters, digits, `-` or `_`, e.g. `demo`;
   - **target length**, e.g. about 60 seconds. If you leave it out, the whole text is covered word for word;
   - **landscape or portrait** (16:9 or 9:16);
   - **budget**: the maximum billed video seconds. The default is 200; see section 6.

   For example:
   > Following SPEC.md, turn examples/novel.md into a ~60-second landscape short drama. Project name demo, budget 200 seconds.

Claude runs `tools/new_project.py` to create `projects/demo/`. It then goes through 8 gates and **stops at each one for your approval**:
1. source (length, aspect ratio, model)
2. anchor plan
3. anchor images
4. voices
5. shot board
6. before each shot: the full prompt and estimated cost. Nothing is spent until you approve.
7. after each shot: the video, the QA results and the actual end state
8. final assembly

At each gate, reply "approve", or "reject" and say what to change. Every decision is recorded in `projects/<name>/workflow.json`, and every cost in `ledger.json`.

**Where the output goes**:
- the finished film: `projects/<name>/exports/`;
- individual shots: `clips/`;
- QA contact sheets: `qa/`.

## 4. Resuming, and several projects at once
- **Resume**: all progress is saved in the project folder, so you can close Claude Code and come back later. Open it again and say "continue demo, which gate are we at?".
- **Several projects at once**: each project keeps its own gates, clips, budget and ledger, so projects never overwrite each other.
  - To run in parallel, open two terminals and run `claude` in each. Say "work on demo1" in one and "work on demo2" in the other.
  - To work in one window, switch by name: "switch to demo2".
  - Always name the project in your instructions ("demo1 S3 approved"), so an approval never lands on the wrong project.
  - All projects share the keys in `.env` and bill the same account. Budgets are counted per project. The cloud service may limit how many video jobs run at once; if you hit a rate limit, stagger your submissions.

## 5. Two ways to continue from the previous shot
Each shot in `story.json` has a `link` field that sets how it connects to the shot before it:

| | Video extension (`extend`, default) | Last frame → first frame (`lastframe`) |
|---|---|---|
| How | The last 4 s of the previous shot go to the model, which keeps filming from there | The previous shot's last frame becomes the new shot's first frame |
| Character images, voices | Can be attached | Cannot be attached (Wan's first-frame mode can't be combined with reference media) |
| Result | Motion, camera and sound flow continuously; characters stay the most stable | The picture connects, but motion may pause; faces and voices drift more over time |
| Cost | You also pay for the 4 s reference | You pay only for the generated seconds |
| Best for | The same scene, with dialogue | Continuing an action, empty shots, tight budgets |

Use `none` for the first shot and for scene changes; those shots are generated independently. Claude suggests a link type for each shot while planning, and you confirm at the shot-board gate.

## 6. Cost reference
- Only video generation is billed per second. Anchor images and voice samples are billed per call, and you need only a few.
- With video extension, the continuation costs extra. For example, to get 9 s of new content:
  - the model outputs 13 s, because the first 4 s re-film the hand-over and are trimmed at assembly;
  - the 4 s reference video you send is billed as input;
  - so the total billed is about 17 s. The example's S6 measured 16.97 s.
- **Measured**: the example story 《修表铺》 produced a 72-second film for about 215 billed Wan seconds, including re-rolls and debugging.
- Before each shot you see the estimated billed seconds, and nothing runs until you approve. Generation stops automatically when the project budget is reached. Prices depend on your Alibaba Cloud account.

## 7. Layout
```
SPEC.md               operating manual: rules, prompt template, gates, lessons learned (Claude's handbook)
CLAUDE.md             startup instructions for Claude Code
tools/new_project.py  create a project (standard folders, story.json draft, first gate)
tools/lt.py           low level: images, video (Wan / MiniMax H3 / Grok), tail clips, last frames, concat
tools/build_prompt.py builds the Wan prompt (official formula) from story.json
tools/run_shot.py     per shot: --plan (await approval) → generate → QA → await review → --accept (record end state)
tools/gate.py         human approval gates
tools/assemble.py     final assembly (--crop-subs crops burned-in subtitles at the bottom)
examples/             example story and story.json
projects/             your projects (not shared with the code)
```

## 8. Known limitations
- **Garbled subtitles.** Wan 3.0 sometimes burns garbled subtitles into dialogue close-ups. Prompts only lower the odds. QA catches it; you can re-roll the shot or use `--crop-subs` at assembly, which crops the bottom 17%.
- **States in reference images.** Whatever state appears in a reference image gets carried into every shot, such as a helmet in hand or a loupe on the eye. Reference images should show only face and clothing.
- **Drift with extension.** Video extension accumulates small drifts. For characters with many close-ups, add a face close-up reference image.
- **`lastframe` is untested.** The last frame → first frame mode is wired into the pipeline but has not yet been tested with real generations. Only video extension has been tested end to end.
- **Costs.** Generation is billed to your own Token Station and Alibaba Cloud accounts. Failed QA does not re-roll automatically by default; you decide.

## License
MIT
