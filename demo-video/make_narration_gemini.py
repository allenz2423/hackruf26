"""Speak each scene of script.json into narration/<id>.wav with Gemini text-to-speech.

Run with the backend's virtualenv (it has google-genai):
    ../backend/.venv/Scripts/python -I make_narration_gemini.py [--voice Puck] [--only intro,outro]

The key is read from GEMINI_API_KEY or the repo's .env. Output is 24 kHz 16-bit mono,
which render.mjs expects.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import wave
from pathlib import Path

from google import genai
from google.genai import types

HERE = Path(__file__).resolve().parent
# This model treats a short "Say cheerfully:" cue as direction. Newer TTS models read
# any cue aloud and reject system instructions, so the cue only works here.
MODEL = "gemini-2.5-flash-preview-tts"
STYLE = "Say cheerfully: "


def api_key() -> str:
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    env = HERE.parent / ".env"
    if not key and env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            match = re.match(r"\s*GEMINI_API_KEY\s*=\s*(.+)", line)
            if match:
                key = match.group(1).strip().strip("\"'")
    if not key:
        sys.exit("Set GEMINI_API_KEY or add it to the repo's .env.")
    return key


def pcm_rate(mime: str) -> int:
    match = re.search(r"rate=(\d+)", mime or "")
    return int(match.group(1)) if match else 24000


def speak(client: genai.Client, text: str, voice: str) -> tuple[bytes, int]:
    for attempt in range(4):
        try:
            response = client.models.generate_content(
                model=MODEL,
                contents=STYLE + text,
                config=types.GenerateContentConfig(
                    response_modalities=["AUDIO"],
                    speech_config=types.SpeechConfig(
                        voice_config=types.VoiceConfig(
                            prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice)
                        )
                    ),
                ),
            )
            part = response.candidates[0].content.parts[0]
            return part.inline_data.data, pcm_rate(part.inline_data.mime_type)
        except Exception as error:  # noqa: BLE001 - retry transient API errors, then report
            if attempt == 3:
                raise
            # The free tier allows a few requests a minute; honor the server's retry hint.
            hint = re.search(r"retry in ([\d.]+)s", str(error))
            delay = float(hint.group(1)) + 2 if hint else 8 * (attempt + 1)
            print(f"  waiting {delay:.0f} s ({type(error).__name__})")
            time.sleep(delay)
    raise RuntimeError("unreachable")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--voice", default="Puck", help="Prebuilt Gemini voice (Puck is upbeat).")
    parser.add_argument("--only", default="", help="Comma-separated scene ids to redo.")
    args = parser.parse_args()

    only = {item for item in args.only.split(",") if item}
    scenes = json.loads((HERE / "script.json").read_text(encoding="utf-8"))
    out = HERE / "narration"
    out.mkdir(exist_ok=True)
    client = genai.Client(api_key=api_key())
    for scene in scenes:
        if only and scene["id"] not in only:
            continue
        pcm, rate = speak(client, scene["text"], args.voice)
        with wave.open(str(out / f"{scene['id']}.wav"), "wb") as file:
            file.setnchannels(1)
            file.setsampwidth(2)
            file.setframerate(rate)
            file.writeframes(pcm)
        print(f"spoke {scene['id']}: {len(pcm) / 2 / rate:.1f} s at {rate} Hz")


if __name__ == "__main__":
    main()
