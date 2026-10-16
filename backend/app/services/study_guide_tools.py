"""Small discovery catalog and opt-in instructions for bounded guide activities.

These are authoring capabilities, not executable model tools. The model may ask for
specific usage instructions while drafting; the server supplies details only from this
registry, and all generated activity data is still validated by the guide schema.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

GuideToolId = Literal[
    "math_graph",
    "vector_components",
    "response_plot",
    "molecule_diagram",
    "cell_biology",
    "process_pathway",
    "circuit_divider",
    "algorithm_trace",
    "sampling_basics",
]


@dataclass(frozen=True)
class GuideTool:
    id: GuideToolId
    name: str
    purpose: str
    instructions: str


GUIDE_TOOLS: tuple[GuideTool, ...] = (
    GuideTool(
        id="math_graph",
        name="JSXGraph function plot",
        purpose="Plot and explore a source-supported mathematical relationship with bounded controls.",
        instructions=(
            "The reader renders this fixed linear-function template with JSXGraph. Use one "
            "`interactive_model` block with template_id `linear_graph_v1` and parameters "
            "`{slope, intercept}` only when the supplied course material explicitly uses a "
            "linear relationship or the graph directly teaches a stated learning objective. "
            "Both values must be between -10 and 10 in 0.1 increments. Explain what the axes "
            "and controls mean. Never add a generic y = mx + b graph just to make the guide "
            "interactive, and do not invent source parameters."
        ),
    ),
    GuideTool(
        id="vector_components",
        name="Vector components",
        purpose="Explore the horizontal and vertical components of a force vector.",
        instructions=(
            "Use one `interactive_model` block with template_id `force_components_v1` and "
            "parameters `{force_n, angle_deg}`. Force is 0–1000 N and angle is 0–90 degrees; "
            "both use 0.1 increments. The model is first-quadrant vector decomposition. State "
            "that assumption in nearby prose when it matters."
        ),
    ),
    GuideTool(
        id="response_plot",
        name="ECharts response plot",
        purpose="Plot a source-supported first-order time response with a movable time marker.",
        instructions=(
            "The reader renders this fixed first-order curve with Apache ECharts. Use one "
            "`interactive_model` block with template_id `first_order_response_v1` "
            "and parameters `{initial_value, final_value, time_constant_s}`. Initial and final "
            "values are whole numbers from -1000 to 1000; the time constant is 0.01–3600 s in "
            "0.01 s increments. Include this only when the source teaches a first-order time "
            "response; do not turn unrelated slope, intercept, or generic process topics into "
            "a response curve. Describe the first-order model assumption."
        ),
    ),
    GuideTool(
        id="molecule_diagram",
        name="SmilesDrawer molecule",
        purpose="Show a reviewed 2D structure for water, carbon dioxide, methane, or ammonia.",
        instructions=(
            "The reader draws the selected fixed, reviewed SMILES with SmilesDrawer. Use one "
            "`labeled_diagram` block with template_id `molecule_2d_v1` and "
            "parameters `{molecule_id}`. The only molecule IDs are `water`, `carbon_dioxide`, "
            "`methane`, and `ammonia`. Do not provide or invent SMILES. This is a reviewed 2D "
            "teaching depiction, not a 3D geometry or a general chemical-structure editor."
        ),
    ),
    GuideTool(
        id="cell_biology",
        name="Animal-cell organelle map",
        purpose="Explore a schematic animal cell and the source-supported organelles it names.",
        instructions=(
            "Use one `labeled_diagram` block with template_id `animal_cell_map_v1` and "
            "parameters `{focus_ids:[...]}`. Allowed IDs are `cytoplasm`, `cytoskeleton`, "
            "`endoplasmic_reticulum`, `golgi_apparatus`, `lysosomes_peroxisomes`, `mitochondria`, "
            "`nucleus`, `plasma_membrane`, and `ribosomes`. Choose only IDs whose listed role is "
            "explicitly supported by the supplied source; the app provides fixed reviewed names "
            "and descriptions for those IDs. The view is schematic and not to scale, and positions "
            "are illustrative. Do not add model-authored labels, functions, or claims to the map."
        ),
    ),
    GuideTool(
        id="process_pathway",
        name="Mermaid process flow",
        purpose="Present a source-supported process as two to twelve ordered, connected stages.",
        instructions=(
            "The reader draws the ordered flow with Mermaid and keeps keyboard-accessible stage "
            "controls beside it. Use one `labeled_diagram` block with template_id "
            "`process_pathway_v1` and "
            "parameters `{stages:[{id,title,explanation}]}`. Include 2–12 ordered stages with "
            "unique short IDs. Keep each stage and explanation grounded in cited source material; "
            "a flow diagram represents order and does not imply a simulation or causality unless "
            "the source supports it."
        ),
    ),
    GuideTool(
        id="circuit_divider",
        name="Ideal resistor divider",
        purpose="Explore an unloaded two-resistor voltage divider with bounded controls.",
        instructions=(
            "Use one `interactive_model` block with template_id `resistor_divider_v1` and "
            "parameters `{voltage_v, r1_ohm, r2_ohm}`. Voltage is 0–24 V in 0.1 V increments; "
            "resistances are whole numbers from 1 to 1,000,000 ohms. The visual assumes an ideal "
            "source and wires and no load on the output."
        ),
    ),
    GuideTool(
        id="algorithm_trace",
        name="Binary-search trace",
        purpose="Step through binary search over a small sorted list of distinct integers.",
        instructions=(
            "Use one `interactive_model` block with template_id `binary_search_trace_v1` and "
            "parameters `{values,target}`. `values` must contain 2–20 sorted, unique integers; "
            "all values and the target must be from -10000 to 10000."
        ),
    ),
    GuideTool(
        id="sampling_basics",
        name="Sampling and assignment demonstration",
        purpose=(
            "Compare simple random, stratified, and front-row convenience samples in a fixed "
            "synthetic population, with optional random treatment assignment."
        ),
        instructions=(
            "Use one `interactive_model` block with template_id `sampling_demo_v1` and parameters "
            "`{method, sample_size, seed, random_assignment}`. `method` is `simple_random`, "
            "`stratified`, or `front_row_convenience`; sample_size is an even integer from 4 to 12; "
            "seed is an integer from 1 to 9999; random_assignment is true or false. The app owns "
            "a fixed synthetic population of 24 fictional students and practice-problem counts. "
            "These values are an app-authored illustration, not source data. Explain that random "
            "sampling selects who enters a study, while random assignment allocates selected "
            "participants to treatments. The front-row group is deliberately constructed with "
            "higher outcomes to illustrate one possible sampling bias; do not present this as a "
            "universal fact. The reproducible pseudorandom draw is a teaching demonstration, not "
            "a production sampling method."
        ),
    ),
)

_BY_ID = {tool.id: tool for tool in GUIDE_TOOLS}


def guide_tool_catalog_prompt() -> str:
    """Return the always-on, compact list of available guide authoring tools."""
    lines = [f"- {tool.id}: {tool.purpose}" for tool in GUIDE_TOOLS]
    return "Available activity tools (brief catalog):\n" + "\n".join(lines)


def guide_tool_instructions(requested_ids: list[str] | tuple[str, ...] | set[str]) -> str:
    """Return registered instructions for requested IDs in stable catalog order."""
    requested = set(requested_ids)
    unknown = requested - _BY_ID.keys()
    if unknown:
        raise ValueError("Unknown study-guide tool request.")
    if not requested:
        return ""
    entries = [tool for tool in GUIDE_TOOLS if tool.id in requested]
    return "Requested activity-tool instructions:\n" + "\n".join(
        f"- {tool.id} ({tool.name}): {tool.instructions}" for tool in entries
    )


def is_guide_tool_id(value: str) -> bool:
    return value in _BY_ID
