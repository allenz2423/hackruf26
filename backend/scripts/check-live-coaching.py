#!/usr/bin/env python3
"""Opt-in Gemini checks using synthetic frames/audio kept only in memory.

Run from the repository root:
  backend/.venv/bin/python backend/scripts/check-live-coaching.py --run-live

This exercises the actual backend gateway service without creating classes,
uploads, or saved conversations. It reports counts/timings, never media or
transcripts. The barge-in scenario generates a spoken test sentence in a
separate Live connection and requires ffmpeg on PATH to resample it.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import io
import json
import re
import shutil
import subprocess
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from google.genai import types
from PIL import Image, ImageDraw, ImageFont
from starlette.websockets import WebSocketDisconnect

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402
from app.live_modes import TutorMode  # noqa: E402
from app.security.rate_limit import InMemoryRateLimiter  # noqa: E402
from app.services.gemini_client import LIVE_API_VERSION, create_genai_client  # noqa: E402
from app.services.live_config import AUTOMATIC_SCREEN_REVIEW  # noqa: E402
from app.services.live_sessions import GeminiLiveSessionService  # noqa: E402

SCENARIOS = ("error", "correct", "incomplete", "typing", "barge")


def picture(scenario: str, tick: int) -> bytes:
    font = ImageFont.truetype("DejaVuSans.ttf", 30)
    with Image.new("RGB", (1024, 640), "white") as image:
        draw = ImageDraw.Draw(image)
        draw.text((50, 50), "Algebra practice: expand 2(x + 3)", fill="black", font=font)
        work = "2(x + 3) = 2x + 3"
        if scenario == "correct":
            work = "2(x + 3) = 2x + 6"
        elif scenario == "incomplete":
            work = "2(x + 3) = 2x +"
        draw.text((50, 160), work, fill="black", font=font)
        if scenario == "typing":
            draw.text((50, 240), "Next line: " + "x + " * (tick % 8), fill="black", font=font)
            if tick % 2:
                draw.line((700, 242, 700, 276), fill="black", width=2)
        stream = io.BytesIO()
        image.save(stream, "JPEG", quality=85)
        return stream.getvalue()


async def synthetic_speech(client, model: str) -> bytes:
    if not shutil.which("ffmpeg"):
        raise RuntimeError("Synthetic speech tools unavailable")
    pcm = bytearray()
    async with asyncio.timeout(20):
        async with client.aio.live.connect(
            model=model,
            config=types.LiveConnectConfig(
                response_modalities=[types.Modality.AUDIO],
                system_instruction=(
                    "Read the user's sentence exactly. Generate only that spoken sentence."
                ),
            ),
        ) as session:
            await session.send_client_content(
                turns=types.Content(
                    role="user", parts=[types.Part(text="Hold on. I fixed that step. Please stop.")]
                ),
                turn_complete=True,
            )
            async for message in session.receive():
                content = message.server_content
                for part in getattr(content.model_turn, "parts", None) or [] if content else []:
                    if part.inline_data and (part.inline_data.mime_type or "").startswith("audio/"):
                        pcm.extend(part.inline_data.data)
                        if len(pcm) > 1_000_000:
                            raise RuntimeError("Synthetic speech exceeded test bounds")
    if not pcm:
        raise RuntimeError("Synthetic speech generation was empty")
    result = await asyncio.to_thread(
        subprocess.run,
        [
            "ffmpeg",
            "-loglevel",
            "error",
            "-f",
            "s16le",
            "-ar",
            "24000",
            "-ac",
            "1",
            "-i",
            "pipe:0",
            "-ar",
            "16000",
            "-ac",
            "1",
            "-f",
            "s16le",
            "pipe:1",
        ],
        input=bytes(pcm),
        check=True,
        capture_output=True,
    )
    return result.stdout


class Browser:
    client = SimpleNamespace(host="synthetic-live-check")

    def __init__(self):
        self.input = asyncio.Queue()
        self.ready = asyncio.Event()
        self.speech = asyncio.Event()
        self.interruption = asyncio.Event()
        self.started = time.monotonic()
        self.first_audio_at = None
        self.interrupted_at = None
        self.completed = 0
        self.spoken_completed = 0
        self.audio_pending = False
        self.audio_chunks = 0
        self.has_text = False
        self.transcript = ""  # bounded, ephemeral; never printed or saved
        self.error = None

    async def receive_text(self):
        event = await self.input.get()
        if event is None:
            raise WebSocketDisconnect()
        return json.dumps(event)

    async def send_json(self, payload):
        if payload["type"] == "ready":
            self.ready.set()
        elif payload["type"] == "error":
            self.error = payload["code"]
        elif payload["type"] == "assistant":
            if payload.get("audio"):
                self.audio_chunks += len(payload["audio"])
                self.audio_pending = True
                if self.first_audio_at is None:
                    self.first_audio_at = time.monotonic()
                self.speech.set()
            self.has_text |= bool(payload.get("text"))
            self.transcript = (self.transcript + (payload.get("text") or ""))[-1000:]
            self.completed += bool(payload.get("turn_complete"))
            if payload.get("turn_complete") and self.audio_pending:
                self.spoken_completed += 1
                self.audio_pending = False
            if payload.get("interrupted"):
                self.interrupted_at = time.monotonic()
                self.interruption.set()
        elif payload["type"] == "annotation_request":
            await self.input.put(
                {
                    "type": "annotation_result",
                    "call_id": payload["call_id"],
                    "status": "rejected",
                    "accepted": 0,
                    "errors": ["This synthetic transport check has no visual preview."],
                }
            )

    async def close(self, **kwargs):
        await self.input.put(None)


class TrackingLive:
    def __init__(self, live):
        self.live = live
        self.reviews = 0
        self.interruptions = 0
        self.input_transcripts = 0
        self.flags = {}
        self.tool_calls = {}
        self.decisions = []

    @asynccontextmanager
    async def connect(self, **kwargs):
        async with self.live.connect(**kwargs) as session:

            async def content(**values):
                turns = values.get("turns", [])
                if turns and turns[0].parts[0].text.startswith(AUTOMATIC_SCREEN_REVIEW):
                    self.reviews += 1
                await session.send_client_content(**values)

            async def receive():
                async for message in session.receive():
                    for call in getattr(message.tool_call, "function_calls", None) or []:
                        self.tool_calls[call.name] = self.tool_calls.get(call.name, 0) + 1
                    if message.server_content and message.server_content.interrupted:
                        self.interruptions += 1
                    content = message.server_content
                    if content is not None:
                        if getattr(content.input_transcription, "text", None):
                            self.input_transcripts += 1
                        for flag in ("turn_complete", "generation_complete", "waiting_for_input"):
                            if getattr(content, flag, False):
                                self.flags[flag] = self.flags.get(flag, 0) + 1
                        status = getattr(content, "interaction_status", None)
                        if status is not None:
                            self.flags[str(status)] = self.flags.get(str(status), 0) + 1
                    yield message

            async def tool_response(**values):
                for response in values.get("function_responses", []):
                    if response.name == "report_screen_review":
                        self.decisions.append(response.response.get("decision", "rejected"))
                await session.send_tool_response(**values)

            yield SimpleNamespace(
                send_client_content=content,
                send_realtime_input=session.send_realtime_input,
                send_tool_response=tool_response,
                receive=receive,
            )


async def run_scenario(scenario: str) -> dict:
    settings = get_settings().model_copy(update={"lean_tool_enabled": False})
    client = create_genai_client(settings, api_version=LIVE_API_VERSION)
    pcm_fixture = None
    if scenario == "barge":
        # Generated fixture only, held in memory. This separate connection is
        # test setup and is never part of the application's coaching feature.
        try:
            pcm_fixture = await synthetic_speech(client, settings.gemini_model)
        except Exception as exc:
            client.close()
            return {"scenario": scenario, "passed": False, "failure": type(exc).__name__}
    tracking = TrackingLive(client.aio.live)
    browser = Browser()
    service = GeminiLiveSessionService(
        SimpleNamespace(aio=SimpleNamespace(live=tracking)),
        settings,
        calculator_limiter=InMemoryRateLimiter(30, 60),
        lean_limiter=InMemoryRateLimiter(3, 60),
        lean_service=None,
    )
    task = asyncio.create_task(service.run(browser, None, mode=TutorMode.CHECK_STEPS))
    driver = None
    result = {"scenario": scenario, "passed": False}
    try:
        async with asyncio.timeout(35):
            while not browser.ready.is_set():
                if task.done():
                    task.result()
                    raise RuntimeError("Provider setup failed")
                await asyncio.sleep(0.05)

            async def frames():
                tick = 0
                while True:
                    data = await asyncio.to_thread(picture, scenario, tick)
                    await browser.input.put(
                        {
                            "type": "frame",
                            "frame_id": str(uuid4()),
                            "data": base64.b64encode(data).decode("ascii"),
                        }
                    )
                    tick += 1
                    await asyncio.sleep(1.05)

            driver = asyncio.create_task(frames())
            if scenario == "barge":
                await browser.speech.wait()
                pcm = pcm_fixture
                await asyncio.sleep(0.15)
                speech_started = time.monotonic()
                for position in range(0, len(pcm), 1280):
                    await browser.input.put(
                        {
                            "type": "audio",
                            "data": base64.b64encode(pcm[position : position + 1280]).decode(
                                "ascii"
                            ),
                        }
                    )
                    await asyncio.sleep(0.04)
                await browser.input.put({"type": "audio_end"})
                await browser.interruption.wait()
                result["speech_to_interrupt_ms"] = round(
                    (browser.interrupted_at - speech_started) * 1000
                )
                result["passed"] = browser.error is None and (
                    tracking.interruptions > 0 or tracking.input_transcripts > 0
                )
            else:
                while (
                    not tracking.decisions
                    or (
                        scenario in {"error", "typing"}
                        and browser.spoken_completed == 0
                        and tracking.decisions[-1] == "mistake"
                    )
                    or browser.completed == 0
                ):
                    if task.done() or browser.error:
                        raise RuntimeError("Review did not complete")
                    await asyncio.sleep(0.05)
                await asyncio.sleep(1)
                result["passed"] = (
                    bool(browser.audio_chunks)
                    if scenario in {"error", "typing"}
                    else not browser.audio_chunks and not browser.has_text
                ) and browser.error is None
                if scenario in {"error", "typing"}:
                    grounded = bool(
                        re.search(
                            r"distribut|constant|parenthes|both terms|every term|multiply|six"
                            r"|\b6\b",
                            browser.transcript,
                            re.IGNORECASE,
                        )
                    )
                    result["hint_about_expected_step"] = grounded
                    result["passed"] &= grounded
    except Exception as exc:
        result["failure"] = browser.error or type(exc).__name__
    finally:
        if driver is not None:
            driver.cancel()
            await asyncio.gather(driver, return_exceptions=True)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        client.close()
        browser.transcript = ""
    result["review_requests"] = tracking.reviews
    result["completed_turns"] = browser.completed
    result["audio_chunks"] = browser.audio_chunks
    result["provider_interruptions"] = tracking.interruptions
    result["input_transcription_events"] = tracking.input_transcripts
    result["provider_flags"] = tracking.flags
    result["tool_calls"] = tracking.tool_calls
    result["review_decisions"] = tracking.decisions
    if browser.first_audio_at is not None:
        result["first_audio_ms"] = round((browser.first_audio_at - browser.started) * 1000)
    return result


async def main(args):
    results = []
    for scenario in SCENARIOS if args.scenario == "all" else [args.scenario]:
        result = await run_scenario(scenario)
        results.append(result)
        print(json.dumps(result), flush=True)
    return 0 if all(result["passed"] for result in results) else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-live", action="store_true", help="Use the configured Gemini API key")
    parser.add_argument("--scenario", choices=("all", *SCENARIOS), default="all")
    args = parser.parse_args()
    if not args.run_live:
        parser.error("Live checks require --run-live and make billed provider requests.")
    raise SystemExit(asyncio.run(main(args)))
