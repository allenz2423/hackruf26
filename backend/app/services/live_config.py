"""Server-owned Gemini Live configuration and annotation tool declaration."""

from __future__ import annotations

from google.genai import types

LIVE_SYSTEM_INSTRUCTION = """
You are Screen Tutor, a concise and patient tutor. Start with a hint or a
clarifying question rather than immediately giving away a full solution.
Explain only what you can see or what the student tells you, and say when
visual text is unclear. Keep spoken turns brief and invite the student to
continue. Use the student's class notes only when the client supplies them.

You may request one visual action: add_annotations. Use it sparingly and only
when a location is clear in a frame the client just supplied. The client will
validate the frame ID and geometry. If location is uncertain, explain in words
instead. Never request or imply clicking, typing, scrolling, shell commands,
or control of the student's computer or applications.
""".strip()


def annotation_tool() -> types.Tool:
    """Return the single supported model tool, with a narrow JSON schema."""
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
        parameters=parameters,
    )
    return types.Tool(function_declarations=[declaration])


def live_connect_config() -> types.LiveConnectConfig:
    """Construct the fixed client configuration protected by each token."""
    # Screen frames arrive as browser-to-Gemini realtime input. They are not
    # posted to this service and do not grant the model any control tools.
    return types.LiveConnectConfig(
        system_instruction=LIVE_SYSTEM_INSTRUCTION,
        response_modalities=[types.Modality.AUDIO],
        output_audio_transcription=types.AudioTranscriptionConfig(),
        session_resumption=types.SessionResumptionConfig(),
        tools=[annotation_tool()],
    )
