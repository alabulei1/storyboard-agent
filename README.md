# storyboard-agent：小说 → AI 短剧的工作流（Claude 当大脑）

把一段小说改编成 AI 短剧的工作流：锚点体系（角色 / 场景 / 道具 / 音色 / 续镜）、按语速规划分镜、视频续写串行生成、质检、账本，以及**每一步都要人确认的关卡**。

- **大脑**：Claude Code（读 `SPEC.md`，规划、写提示词、审片）
- **执行**：`tools/` 下的脚本（调用模型、剪辑、质检、合成）
- **默认模型**：图片 openai/gpt-image-2（经 Token Station），视频 Wan 3.0（阿里云百炼），音色样音 xiaomi/mimo-v2.5-tts-voicedesign（经 Token Station）

## 1. 安装
需要：macOS（推荐）或 Linux、Python 3.10+、ffmpeg、whisper.cpp（`whisper-cli`）及 `ggml-large-v3-turbo` 模型、[Claude Code](https://claude.com/claude-code)。

```bash
./setup.sh          # 检查依赖；macOS 上编译字幕检测工具 tools/ocr
cp .env.example .env   # 然后填入你自己的 key
```
- macOS：`brew install ffmpeg whisper-cpp`，再下载 whisper 模型（见 setup.sh 的提示）。
- Linux：字幕检测改用 tesseract，需要安装中文语言包（`tesseract-ocr-chi-sim`）。

## 2. 准备 key（填进 `.env`，不要提交、不要发给别人）
| 变量 | 用途 | 从哪里来 |
|---|---|---|
| `TOKEN_STATION_API_KEY` | gpt-image-2 锚点图、音色样音 | Token Station（或改 `TOKEN_STATION_BASE_URL` 指向其他 OpenAI 兼容网关） |
| `WAN_API_KEY`、`WAN_BASE_URL` | Wan 3.0 视频生成 | 阿里云百炼（Model Studio）工作空间的 API Key 和地址 |
| `WHISPER_MODEL` | 台词核对（可选，不填会自动在家目录里找） | whisper.cpp 的 ggml 模型路径 |

## 3. 使用
在本目录打开 Claude Code，然后说，例如：

> 按 SPEC.md，把 examples/novel.md 做成约 60 秒的横屏短剧，项目名 demo。

Claude 会新建 `projects/demo/`，然后按 8 道关卡逐步进行，**每一关都会停下来请你确认**：
1. 定稿（时长、画幅、模型）→ 2. 锚点计划 → 3. 锚点图验收 → 4. 音色验收 → 5. 分镜规划
→ 6. 每条生成前（提示词 + 预计费用，批准才花钱）→ 7. 每条生成后（视频 + 质检 + 结束状态）→ 8. 合成导出。

你只需要回复"通过"，或者"不通过 + 修改意见"。所有确认都记在 `projects/<名>/workflow.json`，所有花费记在 `ledger.json`。

## 4. 目录
```
SPEC.md               编排规范（规则、提示词模板、关卡、实测经验）——Claude 的工作手册
CLAUDE.md             给 Claude Code 的启动说明
tools/lt.py           底层：生图、生视频（Wan / MiniMax H3 / Grok）、剪尾段、截尾帧、合成
tools/build_prompt.py 按 story.json 自动拼 Wan 官方格式提示词
tools/run_shot.py     单条：--plan（计划，待批）→ 生成 → 质检 → 待审 → --accept（写入结束状态）
tools/gate.py         人工确认关卡
tools/assemble.py     合成（--crop-subs 可裁掉底部烧录字幕）
examples/             示例小说与 story.json
projects/             你的项目（不随代码分享）
```

## 5. 已知限制
- Wan 3.0 在大段对白的说话近景里会随机烧录乱码字幕，提示词只能降低概率；质检会拦下，可选择重抽或合成时 `--crop-subs`（裁掉底部 17%）。
- 参考图里的状态（例如头盔拿在手上、戴着放大镜）会被带进每个镜头；参考图只放长相与服装。
- 视频续写会累积细节偏差，近景多的角色建议额外做一张脸部特写参考图。
- 生成费用由你自己的 Token Station / 阿里云账户承担；质检不过默认不自动重抽，由你决定。
