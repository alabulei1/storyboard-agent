#!/bin/bash
# Check dependencies and build the macOS subtitle detector.
set -e
cd "$(dirname "$0")"
ok=1
for t in python3 ffmpeg ffprobe whisper-cli; do
  if command -v $t >/dev/null; then echo "✓ $t"; else echo "✗ $t (install it)"; ok=0; fi
done
if [ "$(uname)" = "Darwin" ]; then
  swiftc -O tools/ocr.swift -o tools/ocr && echo "✓ built tools/ocr (macOS Vision OCR)"
else
  command -v tesseract >/dev/null && echo "✓ tesseract (install the chi_sim language pack)" || { echo "✗ tesseract"; ok=0; }
fi
M=$(find ~ -name 'ggml-large-v3-turbo*.bin' -maxdepth 6 2>/dev/null | head -1)
[ -n "$M" ] && echo "✓ whisper model: $M" || echo "! whisper model not found — download ggml-large-v3-turbo.bin from https://huggingface.co/ggerganov/whisper.cpp and set WHISPER_MODEL in .env"
[ -f .env ] && echo "✓ .env exists" || echo "! copy .env.example to .env and fill in your keys"
[ $ok = 1 ] && echo "Setup check done." || echo "Some dependencies are missing."
