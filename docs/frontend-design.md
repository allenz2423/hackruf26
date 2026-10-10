# Frontend Design

**Project:** Screen Tutor  
**Status:** Design draft  
**Client:** HTML, CSS, and JavaScript

## Purpose and scope

The frontend is a browser tutor workspace. A student shares a display, window, or browser tab; asks a question by voice or text; and sees the tutor's reply beside a live preview of the shared content. The tutor can place temporary marks over that preview.

The annotations live inside the Screen Tutor page. A normal web page cannot draw over the student's other desktop applications. The frontend does not click, type, or otherwise control the shared application.

## Proposed client stack

- Semantic HTML for the page and controls.
- CSS for the responsive two-column workspace, preview overlays, and status indicators.
- Vanilla JavaScript ES modules for capture, Gemini session, audio, and annotation behavior.
- Vite for local development and static asset bundling; no UI framework.
- Google's `@google/genai` JavaScript SDK for the browser's Live API WebSocket session. The client receives an ephemeral token from the Python backend; it never receives the long-lived API key.
- Browser APIs: `getDisplayMedia()` for screen capture, `getUserMedia()` and Web Audio for microphone capture/playback, Canvas for frame sampling, and an SVG/canvas layer for marks.

The browser must be served from HTTPS in deployed environments. Screen capture must start from a user gesture and uses the browser's own source picker. Support should be verified on the selected desktop browsers before promising cross-browser compatibility.

## Workspace layout

```text
┌───────────────────────────────────────────────────────────────────┐
│ Screen Tutor     Screen: Sharing / Paused     Mic     End session │
├───────────────────────────────────────┬───────────────────────────┤
│                                       │ Tutor conversation        │
│   Shared screen preview               │                           │
│   + annotation layer                  │ Tutor transcript          │
│                                       │ Student questions         │
│   Pause / choose another source       │                           │
│                                       ├───────────────────────────┤
│                                       │ Ask a question…   Send 🎙 │
└───────────────────────────────────────┴───────────────────────────┘
```

On narrow viewports, stack the preview and conversation vertically. Keep the preview and active annotations visible together. Do not make color the only way to distinguish sharing, microphone, connection, or error states.

### Main UI elements

- **Header:** connection status, screen-sharing status, microphone status, and End session.
- **Screen stage:** video preview, annotation layer, source/re-share controls, and an explicit pause action.
- **Conversation:** user questions, tutor text transcript, connection/retry notices, and a Clear conversation control.
- **Composer:** text question input, Send, and microphone toggle.
- **Annotation controls:** dismiss one mark and Clear marks. Marks expire automatically after a short interval.

## Frontend modules

- `app.js` — page initialization, event wiring, top-level session state, and user-facing errors.
- `screen-capture.js` — browser permission flow, `MediaStream` lifecycle, preview sizing, and sampled JPEG frames.
- `gemini-live.js` — token request, `@google/genai` Live connection, input forwarding, response events, and function-call responses.
- `audio.js` — optional microphone input, tutor audio playback, interruption, and mute/pause behavior.
- `annotations.js` — tool argument validation, frame-to-preview coordinate mapping, SVG/canvas drawing, expiration, and dismissal.
- `api.js` — same-origin calls to the Python backend.

Keep the model session in one controller so capture, audio, transcript, and annotation code do not create separate Live connections.

## Session flow

1. The student presses **Share screen**. In that click handler, call `navigator.mediaDevices.getDisplayMedia({ video: true, audio: false })`. Let the browser choose the source; do not attempt to select one from JavaScript or reuse permission silently.
2. Show the returned stream in a `<video>` element using `srcObject`. The local preview does not pass through the backend.
3. Request a Live token from `POST /api/live-token`. Open a Live API session with model and settings matching the token constraints. If token creation or connection fails, stop the capture tracks and show a retry action.
4. While screen sharing is active, sample the preview to JPEG at no more than the Live API's documented 1 frame per second. Send each frame with a client-generated `frame_id` and keep a short in-memory map of frame IDs to capture dimensions and timestamps.
5. Send typed questions as text. When the student enables the microphone, request microphone permission separately and stream audio to the same Live session. Screen-only plus typed input remains available when microphone permission is denied.
6. Play tutor audio and show output transcription as captions/conversation text. A response may include audio, transcript parts, and a function call; handle every part of a Live event rather than assuming one event contains only one kind of content.
7. When the student ends screen sharing, stops the tracks, closes the Live session, clears frame buffers, and removes annotations. When only the microphone is paused, stop sending audio while keeping text/screen tutoring available.

The Live API supports audio and video in a persistent session. Its current audio model uses audio responses; enable output transcription for readable captions. See the [Live API guide](https://ai.google.dev/gemini-api/docs/live-api/get-started-sdk), [capabilities guide](https://ai.google.dev/gemini-api/docs/live-api/capabilities), [session management guide](https://ai.google.dev/gemini-api/docs/live-api/session-management), and the [official JavaScript SDK](https://github.com/googleapis/js-genai).

## Annotation rendering

The `add_annotations` function is the only model-requested visual action. Each item contains a shape, a short label, a normalized rectangle, and the `frame_id` it references. The frontend:

- Accepts only known shapes (`pointer`, `rectangle`, `underline`, `note`) and finite coordinates in the `0..1` range.
- Rejects unknown frames, old frame IDs, out-of-bounds regions, long labels, and excessive mark counts.
- Maps normalized coordinates to the displayed video content area, accounting for `object-fit`, letterboxing, and resize. Prefer an SVG `viewBox` tied to the source frame dimensions so marks stay aligned.
- Renders marks as pointer-events-disabled UI elements; they must not intercept input to the video or conversation controls.
- Gives each mark a dismiss button in an accessible list and expires marks automatically. If an annotation references a stale frame, return an error tool response and display the spoken explanation without a mark.

The example schema and coordinate convention live in [the product design](design.md#annotation-contract). All validation happens in the browser even though the model provided the arguments.

## Client state model

Represent the session with explicit states instead of inferring status from whether a stream or socket happens to exist:

| State | Meaning | Main actions |
| --- | --- | --- |
| `idle` | No capture or Live session | Share screen |
| `selecting_screen` | Browser picker is open | Cancel |
| `connecting` | Capture selected; token/session setup in progress | Cancel / end |
| `active` | Live session and selected inputs are available | Ask, mute, pause screen, end |
| `screen_paused` | Screen track ended; text/audio may continue | Share screen again, end |
| `reconnecting` | Live connection is recovering | End, retry if recovery fails |
| `ended` | Tracks and Live session are closed | Start another session |
| `error` | Permission, network, or setup failed | Retry, change browser/source |

The browser may stop a screen track when the user ends sharing from its own control. Listen for the track's `ended` event and immediately stop frame sending and update the UI.

## Privacy, accessibility, and failure behavior

- Explain that screen frames and optional microphone audio go to Google's Gemini API before the student starts.
- Keep a visible sharing indicator and immediate End/Stop controls. A Screen pause action should stop the capture track; resuming invokes the browser picker again.
- Keep captured frames, transcript, and marks in memory only by default. Clear them when the session ends.
- Never log the ephemeral token or include it in app analytics.
- Use semantic buttons, visible focus, keyboard operation, captions, sufficient contrast, and reduced-motion support.
- Show distinct errors for denied permissions, unsupported capture, failed token service, Live disconnect, and expired session. Do not continue sending a stream after an error or user stop.

## Frontend acceptance criteria

- A student can share a target through the browser picker and see a synchronized preview in the page.
- Typed questions work without microphone permission; voice can be enabled separately.
- Tutor speech plays and its transcript is visible.
- A valid annotation lands over the correct region of the preview; invalid or stale annotations are dropped safely.
- Pause and End stop the corresponding media immediately and visibly.
- The browser bundle contains no long-lived Gemini API key.
