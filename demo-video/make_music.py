"""Generate an original upbeat backing track with Lyria into music/track.<ext>.

    ../backend/.venv/Scripts/python -I make_music.py [--model models/lyria-3.5]

The track is generated for this video, so there is no third-party music to license.
render.mjs loops it under the narration and lowers it while Jot talks.
"""

from __future__ import annotations

import argparse
import mimetypes
import sys
from pathlib import Path

from google import genai
from google.genai import types

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))  # `python -I` leaves the script folder off the path.
from make_narration_gemini import api_key  # noqa: E402
PROMPT = (
    "Upbeat, friendly, playful instrumental background music for a cheerful student app "
    "demo video. Bouncy plucked synth and marimba melody, warm ukulele strums, light "
    "hand claps and a soft four-on-the-floor kick, bright and optimistic, about 112 BPM, "
    "major key. No vocals, no singing. Steady energy throughout so it can sit under a "
    "voice-over, about two minutes long, with a clean ending."
)
EXTENSIONS = {"audio/mpeg": ".mp3", "audio/mp3": ".mp3", "audio/wav": ".wav", "audio/x-wav": ".wav", "audio/L16": ".pcm"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", default="models/lyria-3.5")
    args = parser.parse_args()

    client = genai.Client(api_key=api_key())
    response = client.models.generate_content(
        model=args.model,
        contents=PROMPT,
        config=types.GenerateContentConfig(response_modalities=["AUDIO"]),
    )
    out = HERE / "music"
    out.mkdir(exist_ok=True)
    for part in response.candidates[0].content.parts:
        if part.inline_data and part.inline_data.data:
            mime = (part.inline_data.mime_type or "").split(";")[0]
            ext = EXTENSIONS.get(mime) or mimetypes.guess_extension(mime) or ".bin"
            path = out / f"track{ext}"
            path.write_bytes(part.inline_data.data)
            print(f"wrote {path} ({part.inline_data.mime_type}, {len(part.inline_data.data) / 1e6:.1f} MB)")
        elif part.text:
            print("note:", part.text.strip()[:300])


if __name__ == "__main__":
    main()
