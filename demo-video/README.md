# Jot demo video

A narrated, animated 1920×1080 video about Jot for presentations. It is separate from the website and not part of its build.

The finished video is `out/jot-demo.mp4`. Captions are in `out/jot-demo.srt`.

## Rebuild it

```sh
npm install        # once: playwright-core (drives your installed Chrome) and ffmpeg-static
npm run narration  # Jot's voice: Gemini text-to-speech, one clip per scene in script.json
npm run music      # an original upbeat backing track from Lyria (music/track.mp3)
npm run render     # mix voice + music and render out/jot-demo.mp4 (a few minutes)
```

- **Edit the words:** change `script.json`, then run `npm run narration` and `npm run render`. The scene timing follows the length of each spoken line. To redo only some lines, run `..\backend\.venv\Scripts\python -I make_narration_gemini.py --only intro,outro`.
- **Change the voice:** add `--voice Leda` (or another Gemini voice) to the narration command. Puck is the default.
- **No API key:** `npm run narration:offline` uses a built-in Windows voice instead. It sounds robotic.
- **Music:** `npm run music` generates a new track each time. Set `MUSIC_LEVEL` in `render.mjs` to change how loud it sits under the voice. With no `music/track.*` file, the video is voice only.
- **Present live:** `npm run preview` prints a local link. Open it in Chrome and click **Play demo** to play it in the browser with sound.
- **Check frames:** `node render.mjs --stills 12,40` saves PNG stills at those times to `out/stills/`.

The narration and music scripts read `GEMINI_API_KEY` from the environment or the repo's `.env`, and run with the backend's virtualenv, which already has `google-genai`.
