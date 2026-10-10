"""Server-owned Gemini Live configuration and approved tutor tools."""

from __future__ import annotations

from google.genai import types

LIVE_SYSTEM_INSTRUCTION = """
You are Screen Tutor, a concise and patient tutor. Start with a hint or a
clarifying question rather than immediately giving away a full solution.
Explain only what you can see or what the student tells you, and say when
visual text is unclear. Keep spoken turns brief and invite the student to
continue. Use the student's class notes only when the client supplies them.

Use Google Search to check current or uncertain factual claims. Explain when
sources disagree or the available evidence is limited. Use
calculate_expression for exact arithmetic or symbolic algebra when that improves
accuracy. It accepts bounded expressions with named symbols and a small set of
math functions; it cannot run arbitrary code.

You may request one visual action: add_annotations. Use it sparingly and only
when a location is clear in a frame the client just supplied. The client will
validate the frame ID and geometry. If location is uncertain, explain in words
instead. Never request or imply clicking, typing, scrolling, shell commands,
or control of the student's computer or applications.
""".strip()


def annotation_tool() -> types.Tool:
    """Return the approved visual tool with a narrow JSON schema."""
    number = types.Schema(type=types.Type.NUMBER, minimum=0, maximum=1)
    region = types.Schema(
        type=types.Type.OBJECT,
        properties={
            "x": number,
            "y": number,
            "width": number,
            "height": number,
        },
        required=["x", "y", "width", "height"],
    )
    item = types.Schema(
        type=types.Type.OBJECT,
        properties={
            "kind": types.Schema(
                type=types.Type.STRING,
                enum=["pointer", "rectangle", "underline", "note"],
            ),
            "region": region,
            "label": types.Schema(type=types.Type.STRING),
        },
        required=["kind", "region", "label"],
    )
    parameters = types.Schema(
        type=types.Type.OBJECT,
        properties={
            "frame_id": types.Schema(type=types.Type.STRING),
            "items": types.Schema(type=types.Type.ARRAY, items=item, max_items=5),
        },
        required=["frame_id", "items"],
    )
    declaration = types.FunctionDeclaration(
        name="add_annotations",
        description=(
            "Request a small number of temporary marks over the exact recently-sent screen frame. "
            "Use normalized coordinates from 0 to 1 and a short explanatory label."
        ),
        behavior=types.Behavior.BLOCKING,
        parameters=parameters,
    )
    return types.Tool(function_declarations=[declaration])


def calculator_tool() -> types.Tool:
    """Return a bounded symbolic calculator function declaration for Live."""
    parameters = types.Schema(
        type=types.Type.OBJECT,
        properties={
            "expression": types.Schema(
                type=types.Type.STRING,
                description=(
                    "A bounded arithmetic or symbolic expression using numbers, "
                    "symbols, pi, e, +, -, *, /, //, %, **, unary signs, "
                    "parentheses, and approved functions: sqrt, sin, cos, tan, "
                    "asin, acos, atan, sinh, cosh, tanh, exp, log, abs, "
                    "simplify, factor, expand. Do not include code or attributes."
                ),
                min_length=1,
                max_length=256,
            )
        },
        required=["expression"],
    )
    declaration = types.FunctionDeclaration(
        name="calculate_expression",
        description=(
            "Evaluate or simplify a short numeric or symbolic math expression. "
            "Returns an exact or symbolic result as text."
        ),
        behavior=types.Behavior.BLOCKING,
        parameters=parameters,
    )
    return types.Tool(function_declarations=[declaration])


def google_search_tool() -> types.Tool:
    """Return Gemini's built-in web-grounding tool."""
    return types.Tool(google_search=types.GoogleSearch())


def live_connect_config() -> types.LiveConnectConfig:
    """Construct the fixed client configuration protected by each token."""
    # Screen frames arrive as browser-to-Gemini realtime input. They are not
    # posted to this service and do not grant the model any control tools.
    return types.LiveConnectConfig(
        system_instruction=LIVE_SYSTEM_INSTRUCTION,
        response_modalities=[types.Modality.AUDIO],
        output_audio_transcription=types.AudioTranscriptionConfig(),
        session_resumption=types.SessionResumptionConfig(),
        tools=[google_search_tool(), calculator_tool(), annotation_tool()],
    )
