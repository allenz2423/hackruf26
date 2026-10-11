"""Build Jot-ScreenTutor-HackRU-Presentation.pptx in the Jot app's look.

Warm paper with notebook rules and a red margin line, ink type, the yellow
highlighter under titles, tilted index cards, and the Jot mascot. The demo video
is embedded on the "See it in action" slide.

Images come from the demo film. Generate them first:
    cd demo-video && npm run render && node export-assets.mjs
Then, from the repo root:
    backend/.venv/Scripts/python -I build_presentation.py [--video path/to/jot-demo.mp4]
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from lxml import etree
from PIL import ImageFont
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Emu, Inches, Pt

ROOT = os.path.dirname(os.path.abspath(__file__))
DECK = os.path.join(ROOT, "demo-video", "out", "deck")
VIDEO = os.path.join(ROOT, "demo-video", "out", "jot-demo.mp4")
TIMELINE = os.path.join(ROOT, "demo-video", "out", "timeline.json")
OUTPUT = os.path.join(ROOT, "Jot-ScreenTutor-HackRU-Presentation.pptx")

# The app's palette (frontend/src/styles.css).
PAPER = RGBColor(0xF4, 0xEF, 0xE3)
SURFACE = RGBColor(0xFF, 0xFD, 0xF7)
INK = RGBColor(0x1C, 0x2E, 0x37)
MUTED = RGBColor(0x4D, 0x5F, 0x68)
PRIMARY = RGBColor(0x3F, 0x65, 0x77)
HIGHLIGHT = RGBColor(0xF6, 0xD9, 0x4A)
EDGE = RGBColor(0xD3, 0xCA, 0xB4)
RULE = RGBColor(0xE6, 0xE2, 0xD6)
MARGIN = RGBColor(0xE2, 0xAC, 0xA2)
STAGE = RGBColor(0x17, 0x24, 0x2B)
ON_DARK = RGBColor(0xEF, 0xEA, 0xDC)
CLASS_COLORS = [RGBColor(0x3F, 0x65, 0x77), RGBColor(0x4F, 0x73, 0x40), RGBColor(0xA3, 0x3A, 0x55), RGBColor(0x8A, 0x64, 0x14)]

FONT = "Calibri"
TITLE_SIZE = 40
W, H = Inches(13.333), Inches(7.5)
LEFT = Inches(1.3)  # content starts right of the margin line
MARGIN_X = Inches(0.95)
_bold = ImageFont.truetype("C:/Windows/Fonts/calibrib.ttf", 1000)


def asset(name: str) -> str:
    path = os.path.join(DECK, name)
    if not os.path.exists(path):
        sys.exit(f"Missing {path}. Run `node export-assets.mjs` in demo-video first.")
    return path


def text_width(text: str, size_pt: float) -> Emu:
    """Width of `text` in Calibri Bold at `size_pt`, for fitting the highlighter."""
    return Emu(int(_bold.getlength(text) / 1000 * size_pt * 12700))


def soft_shadow(shape, blur=10, dist=4, alpha=14) -> None:
    """A soft, slightly offset drop shadow, like the app's cards."""
    sp_pr = shape._element.spPr
    effects = sp_pr.find(qn("a:effectLst"))
    if effects is None:
        effects = etree.SubElement(sp_pr, qn("a:effectLst"))
    shadow = etree.SubElement(effects, qn("a:outerShdw"), blurRad=str(Pt(blur)), dist=str(Pt(dist)), dir="5400000", algn="t", rotWithShape="0")
    color = etree.SubElement(shadow, qn("a:srgbClr"), val="1C2E37")
    etree.SubElement(color, qn("a:alpha"), val=str(alpha * 1000))


def no_line(shape) -> None:
    shape.line.fill.background()


def fill(shape, color: RGBColor) -> None:
    shape.fill.solid()
    shape.fill.fore_color.rgb = color


def paper(slide, dark: bool = False) -> None:
    """Notebook paper: faint rules and the red margin line (or the dark stage)."""
    bg = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, W, H)
    fill(bg, STAGE if dark else PAPER)
    no_line(bg)
    bg.name = "Paper"
    if dark:
        return
    y = Inches(0.5)
    while y < H:
        line = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, 0, y, W, y)
        line.line.color.rgb = RULE
        line.line.width = Pt(0.75)
        line.name = "Rule"
        y += Inches(0.5)
    margin = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, MARGIN_X, 0, MARGIN_X, H)
    margin.line.color.rgb = MARGIN
    margin.line.width = Pt(1.5)
    margin.name = "Margin rule"


def text_box(slide, x, y, w, h, runs, *, size=16, color=INK, bold=False, align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP, name="Text", spacing=1.1):
    """A text box. `runs` is a string, or a list of paragraphs, each a string or a list of (text, options)."""
    box = slide.shapes.add_textbox(x, y, w, h)
    box.name = name
    frame = box.text_frame
    frame.word_wrap = True
    frame.margin_left = frame.margin_right = frame.margin_top = frame.margin_bottom = 0
    frame.vertical_anchor = anchor
    paragraphs = runs if isinstance(runs, list) else [runs]
    for index, para in enumerate(paragraphs):
        p = frame.paragraphs[0] if index == 0 else frame.add_paragraph()
        p.alignment = align
        p.line_spacing = spacing
        parts = para if isinstance(para, list) else [(para, {})]
        for text, options in parts:
            run = p.add_run()
            run.text = text
            run.font.name = FONT
            run.font.size = Pt(options.get("size", size))
            run.font.bold = options.get("bold", bold)
            run.font.italic = options.get("italic", False)
            run.font.color.rgb = options.get("color", color)
        if index:
            p.space_before = Pt(options.get("space", 8) if parts else 8)
    return box


def title(slide, text: str, y=Inches(0.55), size=TITLE_SIZE, color=INK, highlight=True) -> None:
    """A slide title with the app's highlighter pass under its lower half."""
    width = text_width(text, size)
    if highlight:
        mark = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, LEFT - Inches(0.08), y + Pt(size * 0.62), width + Inches(0.16), Pt(size * 0.42))
        fill(mark, HIGHLIGHT)
        no_line(mark)
        mark.name = "Highlighter"
    text_box(slide, LEFT, y, width + Inches(0.6), Pt(size * 1.35), text, size=size, bold=True, color=color, name="Title")


def subtitle(slide, text: str, y=Inches(1.35)) -> None:
    text_box(slide, LEFT, y, Inches(11), Inches(0.5), text, size=20, color=MUTED, name="Subtitle")


def card(slide, x, y, w, h, heading, body, *, tilt=0.0, accent=None, heading_size=21, body_size=15, icon=None):
    """A tilted index card with an optional colored divider tab and an icon."""
    shape = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, x, y, w, h)
    shape.adjustments[0] = 0.07
    fill(shape, SURFACE)
    shape.line.color.rgb = EDGE
    shape.line.width = Pt(0.75)
    shape.rotation = tilt
    shape.name = f"Card: {heading}"
    soft_shadow(shape)
    frame = shape.text_frame
    frame.word_wrap = True
    pad_top = Inches(0.95) if icon else Inches(0.3)
    frame.margin_left = frame.margin_right = Inches(0.3)
    frame.margin_top, frame.margin_bottom = pad_top, Inches(0.25)
    frame.vertical_anchor = MSO_ANCHOR.TOP
    p = frame.paragraphs[0]
    p.alignment = PP_ALIGN.LEFT
    run = p.add_run()
    run.text = heading
    run.font.name, run.font.size, run.font.bold, run.font.color.rgb = FONT, Pt(heading_size), True, INK
    p2 = frame.add_paragraph()
    p2.alignment = PP_ALIGN.LEFT
    p2.space_before = Pt(6)
    p2.line_spacing = 1.1
    run = p2.add_run()
    run.text = body
    run.font.name, run.font.size, run.font.color.rgb = FONT, Pt(body_size), MUTED
    if accent is not None:
        tab = slide.shapes.add_shape(MSO_SHAPE.ROUND_2_SAME_RECTANGLE, x + Inches(0.35), y - Inches(0.11), Inches(0.75), Inches(0.13))
        fill(tab, accent)
        no_line(tab)
        tab.rotation = tilt
        tab.name = "Divider tab"
    if icon:
        badge = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, x + Inches(0.3), y + Inches(0.28), Inches(0.56), Inches(0.56))
        badge.adjustments[0] = 0.34
        fill(badge, RGBColor(0xE3, 0xEC, 0xEE))
        no_line(badge)
        badge.rotation = tilt - 6
        badge.name = "Icon badge"
        slide.shapes.add_picture(asset(f"icon-{icon}.png"), x + Inches(0.4), y + Inches(0.38), Inches(0.36), Inches(0.36))
    return shape


def pill(slide, x, y, text, *, color=PRIMARY, text_color=SURFACE, size=14, pad=0.22):
    width = Emu(int(text_width(text, size) * 1.12)) + Inches(pad * 2)
    shape = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, x, y, width, Pt(size * 2.1))
    shape.adjustments[0] = 0.5
    fill(shape, color)
    no_line(shape)
    shape.name = f"Pill: {text}"
    frame = shape.text_frame
    frame.word_wrap = False
    frame.margin_left = frame.margin_right = Inches(pad)
    frame.margin_top = frame.margin_bottom = 0
    frame.vertical_anchor = MSO_ANCHOR.MIDDLE
    frame.paragraphs[0].alignment = PP_ALIGN.CENTER
    run = frame.paragraphs[0].add_run()
    run.text = text
    run.font.name, run.font.size, run.font.bold, run.font.color.rgb = FONT, Pt(size), True, text_color
    return shape


def picture(slide, name, x, y, w=None, h=None, *, alt=""):
    pic = slide.shapes.add_picture(asset(name), x, y, w, h)
    pic.name = alt or name
    pic._element.nvPicPr.cNvPr.set("descr", alt or name)
    return pic


def icon_row(slide, x, y, icon, heading, body, width=Inches(5.4)):
    """An icon in a soft badge, a bold label, and one line of explanation."""
    badge = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, x, y, Inches(0.62), Inches(0.62))
    badge.adjustments[0] = 0.34
    fill(badge, RGBColor(0xE3, 0xEC, 0xEE))
    no_line(badge)
    badge.rotation = -6
    badge.name = "Icon badge"
    slide.shapes.add_picture(asset(f"icon-{icon}.png"), x + Inches(0.12), y + Inches(0.12), Inches(0.38), Inches(0.38))
    text_box(slide, x + Inches(0.85), y - Inches(0.04), width, Inches(0.9), [[(heading, {"bold": True, "size": 19})], [(body, {"size": 15, "color": MUTED, "space": 2})]], name=heading)


def notes(slide, text: str) -> None:
    slide.notes_slide.notes_text_frame.text = text


def chapters() -> list[tuple[str, str]]:
    labels = {
        "intro": "Meet Jot", "problem": "The problem", "share": "Share and ask", "marks": "Marks on your work",
        "hints": "Hints before answers", "classes": "Classes and uploads", "study": "Study sets", "material": "PDFs on the stage",
        "coach": "Check my steps", "privacy": "Privacy", "tech": "How it works", "outro": "Wrap-up",
    }
    scenes = json.load(open(TIMELINE, encoding="utf-8"))["scenes"]
    return [(f"{int(s['start'] // 60)}:{int(s['start'] % 60):02d}", labels[s["id"]]) for s in scenes]


def build(video: str = VIDEO) -> None:
    prs = Presentation()
    prs.slide_width, prs.slide_height = W, H
    blank = prs.slide_layouts[6]
    core = prs.core_properties
    core.title = "Jot: the study buddy that sees your screen"
    core.subject = "HackRU Fall 2026, Education Track"

    # 1. Title -------------------------------------------------------------
    s = prs.slides.add_slide(blank)
    paper(s)
    pill(s, LEFT, Inches(0.8), "HackRU Fall 2026  ·  Education Track")
    title(s, "Jot", y=Inches(1.55), size=150)
    text_box(s, LEFT, Inches(4.05), Inches(7.2), Inches(1.3), "The study buddy that sees your screen and teaches instead of telling.", size=32, bold=True, name="Tagline", spacing=1.0)
    text_box(s, LEFT, Inches(5.55), Inches(7), Inches(0.5), "Share your screen or a class PDF, ask out loud, and get hints, not answers.", size=18, color=MUTED, name="Pitch")
    text_box(s, LEFT, Inches(6.55), Inches(7), Inches(0.4), "Built with Gemini Live, FastAPI, and vanilla JavaScript", size=13, color=MUTED, name="Built with")
    picture(s, "jot-wave.png", Inches(8.3), Inches(1.0), h=Inches(5.4), alt="Jot, the mascot, waving hello")
    notes(s, "Hi, we built Jot for the Education track: an in-browser tutor that can see the work a student chooses to share, talk it through out loud, and point at the exact step that needs attention. The key idea is that it teaches instead of telling.")

    # 2. Problem -----------------------------------------------------------
    s = prs.slides.add_slide(blank)
    paper(s)
    title(s, "Homework help is broken for learning")
    subtitle(s, "Students can get answers in seconds. Getting understanding is still hard.")
    problems = [
        ("Answers, not understanding", "Chatbots hand over full solutions, so students copy the result instead of finding the step they missed.", -1.5, "quiz"),
        ("Blind to your work", "Help can't see the problem. Students retype equations and lose the diagram, the notes, and where they got stuck.", 1.2, "monitor"),
        ("Your course sits in a folder", "Syllabus, lecture slides, notes, and past exams never shape the help a student gets.", -0.8, "book"),
    ]
    for i, (head, body, tilt, ic) in enumerate(problems):
        card(s, LEFT + Inches(i * 3.65), Inches(2.45), Inches(3.35), Inches(3.0), head, body, tilt=tilt, accent=CLASS_COLORS[i], icon=ic)
    picture(s, "jot-confused.png", Inches(11.35), Inches(5.35), h=Inches(1.95), alt="Jot looking confused")
    notes(s, "Three problems. AI tools give answers, which feels helpful but skips the learning. They can't see the student's actual work. And they know nothing about the student's course: the syllabus, the professor's notation, the old exams.")

    # 3. Meet Jot -------------------------------------------------------------
    s = prs.slides.add_slide(blank)
    paper(s)
    title(s, "Share. Ask. Understand.")
    subtitle(s, "A tutor session takes seconds to start, right in the browser.")
    steps = [
        ("Share", "Pick a tab, a window, or your whole screen, or open one of your class PDFs on the stage."),
        ("Ask", "Talk out loud or type. You can interrupt Jot anytime, like a real conversation."),
        ("Understand", "Jot answers about exactly what it sees and drops a quick mark on the step that matters."),
    ]
    for i, (head, body) in enumerate(steps):
        y = Inches(2.35 + i * 1.45)
        dot = s.shapes.add_shape(MSO_SHAPE.OVAL, LEFT, y, Inches(0.62), Inches(0.62))
        fill(dot, PRIMARY)
        no_line(dot)
        dot.name = f"Step {i + 1}"
        frame = dot.text_frame
        frame.margin_left = frame.margin_right = frame.margin_top = frame.margin_bottom = 0
        frame.vertical_anchor = MSO_ANCHOR.MIDDLE
        run = frame.paragraphs[0].add_run()
        run.text = str(i + 1)
        frame.paragraphs[0].alignment = PP_ALIGN.CENTER
        run.font.name, run.font.size, run.font.bold, run.font.color.rgb = FONT, Pt(22), True, SURFACE
        text_box(s, LEFT + Inches(0.9), y - Inches(0.05), Inches(4.3), Inches(1.2), [[(head, {"bold": True, "size": 22})], [(body, {"size": 15, "color": MUTED, "space": 3})]], name=head)
    picture(s, "scene-share.png", Inches(6.55), Inches(2.05), w=Inches(6.3), alt="Jot's tutor page with a shared worksheet and a spoken question")
    notes(s, "The flow is three steps. The browser's own picker chooses what to share, nothing is captured silently. Then the student just asks, by voice or text, and Jot answers about what's on screen.")

    # 4. Teaching, not telling ---------------------------------------------
    s = prs.slides.add_slide(blank)
    paper(s)
    title(s, "Teaching, not telling")
    subtitle(s, "Every part of Jot is designed so the student still does the thinking.")
    principles = [
        ("sparkles", "Hints before answers", "Jot starts with a question or a nudge, then lets the student take the next step."),
        ("pencil", "Points at the exact step", "A mark on the shared preview shows where the sign flipped or the step broke."),
        ("check", "Check my steps", "Follows along while the student works and speaks up only when something slips."),
        ("book", "Honest about sources", "Uses the class's own notes first, and says when they don't cover something."),
    ]
    for i, (ic, head, body) in enumerate(principles):
        icon_row(s, LEFT, Inches(2.3 + i * 1.18), ic, head, body)
    picture(s, "scene-hints.png", Inches(7.05), Inches(2.2), w=Inches(6.0), alt="A chat where the student asks for the answer and Jot replies with a hint")
    picture(s, "jot-celebrate.png", Inches(11.4), Inches(5.35), h=Inches(1.9), alt="Jot celebrating")
    notes(s, "This is the heart of our Education track entry. The tutor's instructions make it hint first. It can point at the exact spot on the student's work. Check my steps mode follows along and gives a short corrective hint when it sees a clear mistake, and stays quiet otherwise. And it's honest: it prefers the class notes and says when they don't cover something.")

    # 5. Demo video ----------------------------------------------------------
    s = prs.slides.add_slide(blank)
    paper(s)
    title(s, "See it in action")
    vx, vy, vw = LEFT, Inches(1.55), Inches(8.6)
    vh = Emu(int(vw * 9 / 16))
    frame_shape = s.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, vx - Inches(0.12), vy - Inches(0.12), vw + Inches(0.24), vh + Inches(0.24))
    frame_shape.adjustments[0] = 0.03
    fill(frame_shape, SURFACE)
    frame_shape.line.color.rgb = EDGE
    frame_shape.name = "Video frame"
    soft_shadow(frame_shape)
    if os.path.exists(video):
        movie = s.shapes.add_movie(video, vx, vy, vw, vh, poster_frame_image=asset("poster.png"), mime_type="video/mp4")
        movie.name = "Jot demo video"
    else:
        print(f"warning: {video} not found; the slide shows the poster only (pass --video)")
        picture(s, "poster.png", vx, vy, vw, vh, alt="Jot demo video poster")
    text_box(s, LEFT, vy + vh + Inches(0.3), vw, Inches(0.4), "Click the video to play. 1:40, narrated by Jot.", size=14, color=MUTED, name="Play hint")
    cx = Inches(10.45)
    text_box(s, cx, Inches(1.55), Inches(2.6), Inches(0.4), "Chapters", size=20, bold=True, name="Chapters heading")
    rows = [[(f"{stamp}   ", {"bold": True, "size": 14, "color": PRIMARY}), (label, {"size": 14, "space": 5})] for stamp, label in chapters()]
    text_box(s, cx, Inches(2.1), Inches(2.7), Inches(4.6), rows, name="Chapters")
    notes(s, "Play the video: click it, or press Alt+P while it's selected. It runs 1 minute 40 seconds. If time is short, jump to the chapters on the right.")

    # 6. Built around your course -----------------------------------------
    s = prs.slides.add_slide(blank)
    paper(s)
    title(s, "Built around the student's actual course")
    subtitle(s, "Each class is a notebook: its uploads become the tutor's context and the student's study sets.")
    picture(s, "scene-classes.png", LEFT - Inches(0.15), Inches(2.35), w=Inches(6.0), alt="Class cards for Calculus I, Biology, and World History")
    text_box(s, LEFT, Inches(4.6), Inches(5.5), Inches(1.6), [
        [("Upload anything from the course", {"bold": True, "size": 19})],
        [("Notes, documents, syllabi, lecture slides, past tests, and lecture recordings. Jot extracts the text and transcribes lectures.", {"size": 15, "color": MUTED, "space": 3})],
    ], name="Uploads")
    picture(s, "scene-study.png", Inches(7.05), Inches(2.0), w=Inches(6.1), alt="Generated quiz, flashcard, practice exam, and study guide")
    text_box(s, Inches(7.25), Inches(5.0), Inches(5.6), Inches(1.6), [
        [("Study sets from the student's own notes", {"bold": True, "size": 19})],
        [("Quizzes, flashcards, practice exams, and study guides, each question linked back to the upload it came from.", {"size": 15, "color": MUTED, "space": 3})],
    ], name="Study sets")
    notes(s, "Students make a class per course and upload materials. Those uploads become the tutor's background in that class, so explanations match the course's notation. They also generate study sets, and every question links back to its source, so students can check it.")

    # 7. Work on the page ---------------------------------------------------
    s = prs.slides.add_slide(blank)
    paper(s)
    title(s, "Practice right where the work is")
    subtitle(s, "Open a class PDF on the stage, or let Jot follow along as you solve.")
    picture(s, "scene-material.png", LEFT - Inches(0.05), Inches(2.05), w=Inches(5.75), alt="A PDF reading open on Jot's stage with a mark on the heading")
    picture(s, "scene-coach.png", Inches(7.3), Inches(2.1), w=Inches(5.7), alt="Check my steps catching a distribution mistake")
    text_box(s, Inches(7.4), Inches(4.4), Inches(5.4), Inches(2.6), [
        [("Check my steps", {"bold": True, "size": 19})],
        [("Jot watches the work and gives one short hint when a step slips, then lets the student fix it.", {"size": 15, "color": MUTED, "space": 3})],
        [("Language lock", {"bold": True, "size": 19, "space": 14})],
        [("Keeps Jot speaking and writing in one of 11 languages, even when the materials are in another.", {"size": 15, "color": MUTED, "space": 3})],
    ], name="Coaching")
    notes(s, "Two ways to practice. On the left, a class PDF opened on the stage instead of a shared screen: the student flips pages and Jot can mark them. On the right, Check my steps: it follows along and gives a short hint only when it sees a clear mistake. Language lock helps multilingual students learn in the language they think in.")

    # 8. Who it helps -------------------------------------------------------
    s = prs.slides.add_slide(blank)
    paper(s)
    title(s, "Who it helps")
    subtitle(s, "Designed for the moments when students usually get stuck alone.")
    cases = [
        ("Stuck at 11 p.m.", "Office hours are closed. Jot sees the actual problem and guides the next step.", "monitor"),
        ("Studying for the exam", "Practice sets built from the student's own course materials, with sources.", "exam"),
        ("Multilingual learners", "Language lock keeps the tutor in the student's language, whatever the materials use.", "globe"),
        ("Learning by talking", "Explaining out loud is how students find gaps. Jot listens and can be interrupted.", "mic"),
    ]
    for i, (head, body, ic) in enumerate(cases):
        col, row = i % 2, i // 2
        card(s, LEFT + Inches(col * 5.05), Inches(2.3 + row * 2.25), Inches(4.75), Inches(1.95), head, body, tilt=(-1.0, 0.9, 0.7, -0.8)[i], icon=None, heading_size=21)
        s.shapes.add_picture(asset(f"icon-{ic}.png"), LEFT + Inches(col * 5.05 + 4.05), Inches(2.3 + row * 2.25 + 0.28), Inches(0.42), Inches(0.42))
    picture(s, "jot-proud.png", Inches(11.0), Inches(4.2), h=Inches(2.6), alt="Jot looking proud")
    notes(s, "Who it's for: the student stuck late at night, the student prepping for an exam from their own notes, multilingual learners, and anyone who learns by explaining out loud.")

    # 9. Safe by design -----------------------------------------------------
    s = prs.slides.add_slide(blank)
    paper(s)
    title(s, "Safe by design")
    subtitle(s, "Students stay in control of what Jot sees and does.")
    picture(s, "scene-privacy.png", LEFT - Inches(0.1), Inches(2.2), w=Inches(7.0), alt="Checklist: only what you share, never recorded, never clicks or types")
    text_box(s, Inches(8.65), Inches(2.35), Inches(3.9), Inches(3.8), [
        [("It can only point", {"bold": True, "size": 19})],
        [("The model's one visual action is a temporary mark. No mouse, keyboard, or app control.", {"size": 15, "color": MUTED, "space": 3})],
        [("Live media stays in memory", {"bold": True, "size": 19, "space": 14})],
        [("Screen frames and audio are relayed, never logged or stored.", {"size": 15, "color": MUTED, "space": 3})],
        [("The API key stays on the server", {"bold": True, "size": 19, "space": 14})],
        [("The browser gets a short-lived, one-use session ticket.", {"size": 15, "color": MUTED, "space": 3})],
    ], name="Safety details")
    picture(s, "jot-happy.png", Inches(11.55), Inches(5.6), h=Inches(1.7), alt="Jot smiling")
    notes(s, "Trust matters for a tool that sees a student's screen. The browser's picker controls what's shared, live media is only held in memory, and the model can't click or type: its only visual action is a temporary mark that the browser validates before drawing.")

    # 10. How it works ------------------------------------------------------
    s = prs.slides.add_slide(blank)
    paper(s)
    title(s, "How it works")
    subtitle(s, "The browser captures and draws; the server owns the model, the prompts, and the tools.")
    nodes = [
        ("monitor", "Browser", "Screen preview, mic, and marks. Samples about one frame a second."),
        ("refresh", "FastAPI server", "Session tickets, class context, tool execution, SQLite library."),
        ("sparkles", "Gemini Live", "Sees frames, hears the student, speaks, and asks for marks."),
    ]
    for i, (ic, head, body) in enumerate(nodes):
        x = LEFT + Inches(i * 3.95)
        card(s, x, Inches(2.3), Inches(3.3), Inches(2.25), head, body, icon=ic, heading_size=21)
        if i < 2:
            arrow = s.shapes.add_shape(MSO_SHAPE.LEFT_RIGHT_ARROW, x + Inches(3.38), Inches(3.2), Inches(0.55), Inches(0.42))
            fill(arrow, HIGHLIGHT)
            arrow.line.color.rgb = INK
            arrow.line.width = Pt(1)
            arrow.name = "Two-way stream"
    text_box(s, LEFT, Inches(4.95), Inches(3), Inches(0.4), "Tools the tutor can call", size=17, bold=True, name="Tools heading")
    x = LEFT
    for label in ["add_annotations (marks)", "calculator", "Lean proof check", "Google Search"]:
        shape = pill(s, x, Inches(5.45), label, color=SURFACE, text_color=INK, size=14)
        shape.line.color.rgb = EDGE
        x += shape.width + Inches(0.18)
    text_box(s, LEFT, Inches(6.15), Inches(3), Inches(0.4), "Stack", size=17, bold=True, name="Stack heading")
    x = LEFT + Inches(0.8)
    for label in ["Python · FastAPI · Pydantic", "google-genai SDK", "Vanilla JS + Vite", "SQLite", "pdf.js"]:
        shape = pill(s, x, Inches(6.1), label, color=PRIMARY, size=13)
        x += shape.width + Inches(0.15)
    notes(s, "Architecture. The browser previews the shared screen and relays sampled frames and mic audio over a same-origin WebSocket. The FastAPI server holds the Gemini key, issues one-use tickets, builds class context, and runs the tools: annotations, a bounded calculator, Lean proof checking, and Google Search. Marks come back in Gemini's native box format and are converted and validated before the browser draws them.")

    # 11. What's next -------------------------------------------------------
    s = prs.slides.add_slide(blank)
    paper(s)
    title(s, "What's next")
    subtitle(s, "Bringing Jot to more of the places students learn.")
    nextsteps = [
        ("Classroom import", "Pull syllabi, slides, and assignments straight from a course site like Canvas.", "syllabus"),
        ("Pen and paper", "Tablet and stylus mode, or a phone camera pointed at handwritten homework.", "pencil"),
        ("Study groups", "Shared sessions where classmates work a problem set together with Jot.", "user"),
    ]
    for i, (head, body, ic) in enumerate(nextsteps):
        card(s, LEFT + Inches(i * 3.65), Inches(2.4), Inches(3.35), Inches(2.85), head, body, tilt=(-1.2, 1.0, -0.6)[i], accent=CLASS_COLORS[i + 1], icon=ic)
    notes(s, "Next: importing course materials directly from a course site, supporting handwritten work, and group study sessions. These are plans, not built yet.")

    # 12. Thank you ---------------------------------------------------------
    s = prs.slides.add_slide(blank)
    paper(s)
    title(s, "Thank you!", y=Inches(1.4), size=96)
    text_box(s, LEFT, Inches(3.2), Inches(7.5), Inches(0.8), "Share. Ask. Understand.", size=36, bold=True, name="Tagline")
    text_box(s, LEFT, Inches(4.15), Inches(7.5), Inches(0.5), "Questions? Try it live: we'll share a screen and ask Jot anything.", size=18, color=MUTED, name="Invite")
    pill(s, LEFT, Inches(5.25), "Education Track  ·  HackRU Fall 2026")
    text_box(s, LEFT, Inches(6.05), Inches(7.5), Inches(0.4), "github.com/allenz2423/hackruf26", size=15, color=PRIMARY, bold=True, name="Repository")
    picture(s, "jot-celebrate.png", Inches(8.5), Inches(1.2), h=Inches(5.2), alt="Jot celebrating")
    notes(s, "Thanks! Live demo setup: start the backend with backend\\.venv\\Scripts\\python -m uvicorn app.main:app --app-dir backend --reload, then in frontend run npm run dev and open http://127.0.0.1:5173. Quick tutor works without an account; classes need one.")

    prs.save(OUTPUT)
    print(f"Wrote {OUTPUT} ({len(prs.slides)} slides)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build the Jot presentation.")
    parser.add_argument("--video", default=VIDEO, help="The demo video to embed (default: demo-video/out/jot-demo.mp4).")
    build(parser.parse_args().video)
