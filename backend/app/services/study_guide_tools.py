"""Compact discovery and opt-in usage instructions for study-guide visual tools."""

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
    "graph_traversal",
    "stress_cube_3d",
    "molecule_3d",
    "python_algorithm",
    "simcir_circuit",
    "custom_svg",
]


@dataclass(frozen=True)
class GuideTool:
    id: GuideToolId
    name: str
    purpose: str
    instructions: str


GUIDE_TOOLS: tuple[GuideTool, ...] = (
    GuideTool(
        id="custom_svg",
        name="Custom SVG illustration and animation",
        purpose="Draw an original labeled diagram, plot, schematic, spatial model, or animation for any subject.",
        instructions=(
            "This is a general drawing capability, not a fixed template. Use a `svg_diagram` "
            "block with `title`, `alt_text`, `fallback_text`, and a complete standalone SVG "
            "string in `svg`. Build the actual subject-specific visual yourself; do not leave "
            "the diagram as a description for the reader to imagine. Use an SVG root with a "
            "viewBox, and draw with paths, shapes, lines, text, groups, reusable local defs, "
            "gradients, markers, clipping, masks, and bounded filters as useful. Include a "
            "`title` and `desc` inside the SVG, label axes/parts/vectors clearly, and keep "
            "critical labels readable at normal page width. You may use native SMIL `animate`, "
            "`animateTransform`, `animateMotion`, or `set` for a conceptually meaningful "
            "animation; use time-based starts and a clear static starting frame. The reader "
            "adds play, pause, and restart controls, and print exports use the static frame. "
            "SVG is declarative drawing data, not executable code: never include `script`, "
            "event-handler attributes, `foreignObject`, `image`, links, `<style>`, a `style` "
            "attribute, external resources, JavaScript, or URLs. Local `url(#id)` references "
            "are allowed. Do not use Mermaid or another diagram DSL inside this field. Cite "
            "the course source that supports the visual's claims; do not invent values or "
            "scientific details."
        ),
    ),
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
    GuideTool(
        id="graph_traversal",
        name="Cytoscape graph traversal",
        purpose="Explore BFS or DFS on a small, source-grounded directed graph.",
        instructions=(
            "Use one `interactive_model` block with template_id `graph_traversal_v1` and "
            "parameters `{algorithm, start_node, nodes, edges}`. Algorithm is `bfs` or `dfs`; "
            "each node is `{id,label,description?}`, with 2–24 nodes and 1–48 directed edges "
            "`{source,target}`. IDs are unique 1–40 character letters, numbers, underscores, "
            "or hyphens; edge endpoints and start_node must name declared nodes. Node labels are "
            "1–80 characters and optional descriptions at most 240. Include only concepts and "
            "connections supported by cited course material. Do not supply Cytoscape styles, "
            "layout settings, selectors, or executable configuration."
        ),
    ),
    GuideTool(
        id="stress_cube_3d",
        name="Three-dimensional stress cube",
        purpose="Inspect principal stresses and the absolute maximum-shear plane for a symmetric tensor.",
        instructions=(
            "Use one `interactive_model` block with template_id `stress_cube_3d_v1` and "
            "parameters `{stress_mpa:[[xx,xy,xz],[yx,yy,yz],[zx,zy,zz]]}`. Values are finite "
            "numbers in MPa between -1000 and 1000 and the matrix must be symmetric; do not "
            "average or otherwise repair asymmetric source values. Enter only a tensor stated "
            "in the cited source or explicitly supplied by the user. State the tensile-positive "
            "sign convention. The fixed activity computes principal stresses and uses "
            "n=(e1+e3)/√2 for one absolute maximum-shear plane; when the extreme principal "
            "stresses are equal, the selected orientation is not unique."
        ),
    ),
    GuideTool(
        id="molecule_3d",
        name="Reviewed 3D molecule",
        purpose="Rotate an app-authored idealized 3D geometry for four common small molecules.",
        instructions=(
            "Use one `labeled_diagram` block with template_id `molecule_3d_v1` and parameters "
            "`{molecule_id}`. The only reviewed IDs are `water`, `carbon_dioxide`, `methane`, "
            "and `ammonia`. Coordinates, atom labels, and bonds come from the fixed app catalog; "
            "never provide SMILES, XYZ, PDB, URLs, arbitrary atom/bond arrays, or other molecule "
            "IDs. Treat the geometry as an idealized teaching model, not a measured structure."
        ),
    ),
    GuideTool(
        id="python_algorithm",
        name="Fixed Python algorithm walkthrough",
        purpose="Run a reviewed search or insertion-sort example on bounded integer inputs.",
        instructions=(
            "Use one `interactive_model` block with template_id `python_algorithm_trace_v1` and "
            "parameters `{algorithm, values, target}`. Algorithms are `linear_search`, "
            "`binary_search`, and `insertion_sort`; values are 2–16 integers from -100 to 100. "
            "Search algorithms require a target integer from -100 to 100; binary-search values "
            "must be sorted. Insertion sort omits target. The page runs a fixed app-owned Python "
            "program in an isolated worker. Never supply code, source, snippets, package names, "
            "or execution commands; the model chooses only the reviewed algorithm and input data."
        ),
    ),
    GuideTool(
        id="simcir_circuit",
        name="Simcir digital logic circuit",
        purpose="Explore a fixed AND gate, OR gate, or half-adder circuit and its truth table.",
        instructions=(
            "Use one `interactive_model` block with template_id `simcir_circuit_v1` and "
            "parameters `{circuit_id}`. The only circuit IDs are `and_gate`, `or_gate`, and "
            "`half_adder`. The app owns all devices, gate topology, labels, and wires. Do not "
            "provide Simcir JSON, component lists, wires, code, or arbitrary circuit settings. "
            "Describe the Boolean behavior supported by the supplied source; this template is "
            "a fixed digital-logic activity, not a general analog circuit editor."
        ),
    ),
)

_BY_ID = {tool.id: tool for tool in GUIDE_TOOLS}


def guide_tool_catalog_prompt() -> str:
    """Return the short always-on catalog; full usage details stay opt-in to save context."""
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
