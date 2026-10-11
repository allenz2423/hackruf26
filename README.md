# Jot

**The study buddy that sees your screen and teaches instead of telling.**

Jot (working name: Screen Tutor) is a HackRU Fall 2026 project for the **Education track**. It is an AI tutor that runs in the browser. A student shares a tab, a window, or their screen, or opens one of their own class PDFs. They ask out loud or by typing, and Jot answers about exactly what it sees. It starts with hints, not answers, and can point at the exact step that needs attention with a temporary mark on the preview.

- **Presentation:** [Jot-ScreenTutor-HackRU-Presentation.pptx](Jot-ScreenTutor-HackRU-Presentation.pptx), 12 slides with speaker notes. The narrated demo video is embedded on slide 5.
- **Demo video:** a 1:40 narrated walkthrough, built from [demo-video/](demo-video/README.md).

## What it does

**Tutoring on the student's own work**

- **Share, ask, understand.** The browser's own picker chooses what to share. Students talk or type and can interrupt Jot anytime.
- **Marks on the exact spot.** Jot can box, underline, point at, or add a note to a region of the shared preview, such as the step where a sign flipped. Marks are drawn by the page and fade after a few seconds.
- **Hints before answers.** The tutor nudges with questions first and explains a prerequisite or a small worked example only when hints aren't working.
- **Check my steps.** Turn it on before sharing, and Jot follows along as the student works. It speaks a short hint only when it sees a clear mistake, and stays quiet for correct, unfinished, or unclear work.
- **Language lock.** Keeps the tutor in one of 11 languages, even when the materials are in another.
- **Quick tutor for guests.** Anyone can try tutoring without an account.

**Built around the student's course** (requires an account)

- **Classes.** Each class has its own color, icon, and library of uploads: notes, documents, syllabi, lecture slides, past tests, and lecture recordings. Jot extracts the text and transcribes lectures.
- **Class tutor.** In a class, Jot reads that class's materials, or only the uploads the student picks, and says when they don't cover something.
- **Open material.** Show a class PDF, image, or document on the tutor stage instead of a screen. The student turns pages and Jot can mark them.
- **Study sets.** Generate quizzes, flashcards, practice exams, and study guides from the student's own uploads, with each question linked back to its source. Study guides download as PDF or LaTeX.
- **Saved recaps** (opt-in, per class). When a session ends, Jot can save a short recap of topics and next steps, to pick up where the student left off. Students can view and clear them.
- **Your own Jot.** Pick a color, face, and accessory for the mascot that talks with you.

## Safe by design

- Jot sees only what the student shares, through the browser's picker.
- Live screen frames and microphone audio are relayed in memory and never recorded or stored. Only opt-in recaps are saved.
- The model's only visual action is a temporary mark, which the browser validates before drawing. It cannot click, type, or control any app.
- The Gemini API key stays on the server. The browser gets a short-lived, one-use session ticket.

## Run it locally

You need Python 3.12, Node.js 18 or newer, and a [Gemini API key](https://aistudio.google.com/apikey).

1. **Configure.** Copy `.env.example` to `.env`. Set `GEMINI_API_KEY`, and set `FILE_URL_SIGNING_SECRET` to a random string of at least 32 characters.
2. **Start the backend** (port 8000):

   ```bash
   cd backend
   python -m venv .venv
   .venv/Scripts/pip install -r requirements.txt   # macOS/Linux: .venv/bin/pip
   cd ..
   backend/.venv/Scripts/python -m uvicorn app.main:app --app-dir backend --reload
   ```

3. **Start the frontend** in a second terminal:

   ```bash
   cd frontend
   npm install
   npm run dev
   ```

4. **Open** http://127.0.0.1:5173. Use **Quick tutor** as a guest, or create an account to make classes.

The SQLite database and uploaded files live under `backend/.local/`. Optional extras are covered in the [backend README](backend/README.md): Lean proof checking, and Tectonic for saving study guides as PDF.

### Tests

```bash
cd frontend && npm test
PYTHONPATH=backend backend/.venv/Scripts/python -m unittest discover -s backend/tests   # from the repo root
```

The backend tests use in-memory SQLite and fake Live and Lean runners, so they don't call Gemini.

## How it works

The browser previews the shared screen, samples about one frame a second, and relays frames and microphone audio over a same-origin WebSocket. The FastAPI server holds the API key, issues one-use session tickets, builds class context, and runs the tutor's tools. These are `add_annotations` for marks, a bounded calculator, a Lean proof check, and Google Search. Gemini Live (`gemini-3.8-live`) sees the frames, hears the student, speaks back, and asks for marks, which the server converts and the browser validates against the exact frame they refer to.

| Part | Stack |
| --- | --- |
| [frontend/](frontend/) | HTML, CSS, vanilla JavaScript modules, Vite, pdf.js |
| [backend/](backend/) | Python, FastAPI, Pydantic v2, Google's `google-genai` SDK, SQLite, Uvicorn |
| [demo-video/](demo-video/README.md) | The narrated demo film: Gemini text-to-speech, Lyria music, rendered with Chrome and ffmpeg |
| [build_presentation.py](build_presentation.py) | Builds the presentation with python-pptx |

The page draws marks over its own preview. It cannot draw over other desktop apps.

## Design docs

- [Product and system design](docs/design.md)
- [Frontend design](docs/frontend-design.md)
- [Backend design](docs/backend-design.md) and [backend setup](backend/README.md)
- [Proactive coaching and barge-in](docs/proactive-coaching-design.md)
- [Sharing classes (proposal)](docs/sharing-design.md)
