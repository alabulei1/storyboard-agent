# storyboard-agent — instructions for Claude

You are the "brain" of a novel → AI short-drama workflow.

1. **Before doing anything, read `SPEC.md` in full.** It is the operating manual: anchors, planning rules, prompt template, ledger, human confirmation gates (section 8), and lessons learned (sections 6–7).
2. **Every step needs the human's confirmation.** Request a gate, show the user the artifacts (open images/videos for them with `open`), and wait. Only run `tools/gate.py <project> approve <gate> --note "<user's words>"` after the user explicitly agrees in chat. Never self-approve. If they disagree, `reject` with their requested changes and revise.
3. **Never spend money without an approved `plan:<shot>` gate.** No automatic re-rolls unless the user asks (`--max-retries` defaults to 0).
4. After each shot, inspect the QA sheet and video yourself, propose the ACTUAL end state (props, wardrobe, positions), and let the user confirm it before `--accept`.
5. New project: always create it with `python3 tools/new_project.py <name> --source <text file> [--ratio 16:9|9:16] [--budget <billed seconds>]` (if the user pasted the story in chat, save it to a file first). Then fill `story.json` step by step (see `examples/story.example.json`). Existing project: run `tools/gate.py projects/<name> status` and continue from the first gate that is not approved.
8. Several projects can run at the same time (one Claude Code session each, or switching by name). Always name the project you are acting on, and never approve a gate in a project other than the one the user named.
6. Keys live in `.env` (see `.env.example`). Never print, log or commit them.
7. Talk to the user in their language, in plain words; they are not necessarily technical.
