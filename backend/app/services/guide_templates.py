"""Deterministic calculations and reviewed defaults for typed study-guide templates."""

from __future__ import annotations

import math

MOLECULES: dict[str, dict] = {
    "water": {
        "name": "Water",
        "formula": "H₂O",
        "description": (
            "A schematic bent 2D connectivity diagram with oxygen bonded to two hydrogen atoms; "
            "bond angles are not to scale."
        ),
        "atoms": [
            {
                "id": "o",
                "element": "O",
                "x": 50,
                "y": 34,
                "description": "Oxygen is the central atom in this model.",
            },
            {
                "id": "h1",
                "element": "H",
                "x": 27,
                "y": 57,
                "description": "This hydrogen is bonded to oxygen.",
            },
            {
                "id": "h2",
                "element": "H",
                "x": 73,
                "y": 57,
                "description": "This hydrogen is bonded to oxygen.",
            },
        ],
        "bonds": [{"from": "o", "to": "h1", "order": 1}, {"from": "o", "to": "h2", "order": 1}],
    },
    "carbon_dioxide": {
        "name": "Carbon dioxide",
        "formula": "CO₂",
        "description": (
            "A linear model with carbon between two oxygen atoms and a double bond to each."
        ),
        "atoms": [
            {
                "id": "o1",
                "element": "O",
                "x": 22,
                "y": 36,
                "description": "This terminal oxygen is double-bonded to carbon.",
            },
            {
                "id": "c",
                "element": "C",
                "x": 50,
                "y": 36,
                "description": "Carbon is the central atom in this linear model.",
            },
            {
                "id": "o2",
                "element": "O",
                "x": 78,
                "y": 36,
                "description": "This terminal oxygen is double-bonded to carbon.",
            },
        ],
        "bonds": [{"from": "o1", "to": "c", "order": 2}, {"from": "c", "to": "o2", "order": 2}],
    },
    "methane": {
        "name": "Methane",
        "formula": "CH₄",
        "description": (
            "A 2D projection of one carbon bonded to four hydrogen atoms; it does not represent "
            "methane's 3D tetrahedral geometry."
        ),
        "atoms": [
            {
                "id": "c",
                "element": "C",
                "x": 50,
                "y": 38,
                "description": "Carbon is the central atom in this model.",
            },
            {
                "id": "h1",
                "element": "H",
                "x": 25,
                "y": 18,
                "description": "This hydrogen is bonded to carbon.",
            },
            {
                "id": "h2",
                "element": "H",
                "x": 75,
                "y": 18,
                "description": "This hydrogen is bonded to carbon.",
            },
            {
                "id": "h3",
                "element": "H",
                "x": 25,
                "y": 58,
                "description": "This hydrogen is bonded to carbon.",
            },
            {
                "id": "h4",
                "element": "H",
                "x": 75,
                "y": 58,
                "description": "This hydrogen is bonded to carbon.",
            },
        ],
        "bonds": [
            {"from": "c", "to": "h1", "order": 1},
            {"from": "c", "to": "h2", "order": 1},
            {"from": "c", "to": "h3", "order": 1},
            {"from": "c", "to": "h4", "order": 1},
        ],
    },
    "ammonia": {
        "name": "Ammonia",
        "formula": "NH₃",
        "description": (
            "A 2D projection of one nitrogen bonded to three hydrogen atoms; it does not "
            "represent ammonia's 3D geometry."
        ),
        "atoms": [
            {
                "id": "n",
                "element": "N",
                "x": 50,
                "y": 39,
                "description": "Nitrogen is the central atom in this model.",
            },
            {
                "id": "h1",
                "element": "H",
                "x": 25,
                "y": 18,
                "description": "This hydrogen is bonded to nitrogen.",
            },
            {
                "id": "h2",
                "element": "H",
                "x": 75,
                "y": 18,
                "description": "This hydrogen is bonded to nitrogen.",
            },
            {
                "id": "h3",
                "element": "H",
                "x": 50,
                "y": 64,
                "description": "This hydrogen is bonded to nitrogen.",
            },
        ],
        "bonds": [
            {"from": "n", "to": "h1", "order": 1},
            {"from": "n", "to": "h2", "order": 1},
            {"from": "n", "to": "h3", "order": 1},
        ],
    },
}

# Fixed content is transcribed from the example Human Biology and Genetics course
# source. It is deliberately not authored by the guide model. Coordinates are only
# schematic placement anchors, not claims about organelle size, abundance, or location.
ANIMAL_CELL_ORGANELLES: dict[str, dict[str, str | int]] = {
    "cytoplasm": {
        "name": "Cytoplasm",
        "description": "A jelly-like fluid called cytosol and other structures surround the nucleus.",
        "x": 24,
        "y": 66,
    },
    "cytoskeleton": {
        "name": "Cytoskeleton",
        "description": (
            "A network of long fibers forms the cell's structural framework. It helps determine "
            "cell shape, participates in cell division, allows cells to move, and provides tracks "
            "that direct movement of organelles and other substances."
        ),
        "x": 67,
        "y": 67,
    },
    "endoplasmic_reticulum": {
        "name": "Endoplasmic reticulum (ER)",
        "description": (
            "The ER helps process molecules created by the cell and transports them to specific "
            "destinations inside or outside the cell."
        ),
        "x": 65,
        "y": 42,
    },
    "golgi_apparatus": {
        "name": "Golgi apparatus",
        "description": "The Golgi packages molecules processed by the ER for transport out of the cell.",
        "x": 40,
        "y": 73,
    },
    "lysosomes_peroxisomes": {
        "name": "Lysosomes and peroxisomes",
        "description": (
            "They digest foreign bacteria, remove toxic substances, and recycle worn-out cell "
            "components."
        ),
        "x": 77,
        "y": 55,
    },
    "mitochondria": {
        "name": "Mitochondria",
        "description": (
            "Mitochondria convert energy from food into a form the cell can use. They have their "
            "own genetic material, separate from DNA in the nucleus, and can make copies of "
            "themselves."
        ),
        "x": 28,
        "y": 42,
    },
    "nucleus": {
        "name": "Nucleus and nuclear envelope",
        "description": (
            "The nucleus houses DNA and sends directions for cell activities. The nuclear "
            "envelope protects DNA and separates the nucleus from the rest of the cell."
        ),
        "x": 58,
        "y": 45,
    },
    "plasma_membrane": {
        "name": "Plasma membrane",
        "description": (
            "The plasma membrane is the cell's outer lining. It separates the cell from its "
            "environment and allows materials to enter and leave."
        ),
        "x": 88,
        "y": 50,
    },
    "ribosomes": {
        "name": "Ribosomes",
        "description": (
            "Ribosomes process the cell's genetic instructions to create proteins. They can "
            "float freely in the cytoplasm or be connected to the ER."
        ),
        "x": 71,
        "y": 27,
    },
}

ANIMAL_CELL_LIMITATION = (
    "Schematic animal-cell view; shapes and positions are illustrative and not to scale. "
    "It is not a micrograph and does not show relative size, abundance, or exact placement."
)

# App-authored synthetic teaching data. The first 12 fictional learners are marked as
# front-row students and have deliberately higher practice counts than the second section.
# This illustrates one possible biased convenience sample; it is not course source data
# or a claim about real front-row students.
SAMPLING_DEMO_POPULATION: tuple[dict[str, str | int | bool], ...] = tuple(
    [
        {
            "id": f"A{index + 1:02d}",
            "stratum": "Section A",
            "practice_problems": value,
            "front_row": True,
        }
        for index, value in enumerate((10, 9, 8, 8, 7, 7, 6, 5, 4, 4, 3, 2))
    ]
    + [
        {
            "id": f"B{index + 1:02d}",
            "stratum": "Section B",
            "practice_problems": value,
            "front_row": False,
        }
        for index, value in enumerate((5, 4, 4, 3, 3, 2, 2, 1, 1, 0, 0, 0))
    ]
)


def _sampling_shuffle[T](items: list[T], seed: int) -> list[T]:
    """Fisher-Yates using the LCG mirrored in frontend sampling-demo.js."""
    shuffled = list(items)
    state = seed & 0xFFFFFFFF
    for index in range(len(shuffled) - 1, 0, -1):
        state = (1664525 * state + 1013904223) & 0xFFFFFFFF
        swap_index = state * (index + 1) // 0x1_0000_0000
        shuffled[index], shuffled[swap_index] = shuffled[swap_index], shuffled[index]
    return shuffled


def _sampling_num(value: float) -> str:
    """Format nonnegative means with the same half-up rule as the browser."""
    rounded = math.floor(value * 100 + 0.5) / 100
    return f"{rounded:.2f}".rstrip("0").rstrip(".") or "0"


def sampling_demo_result(
    method: str,
    sample_size: int,
    seed: int,
    random_assignment: bool,
) -> dict:
    """Return the deterministic sample and optional assignment for the fixed population."""
    if method == "simple_random":
        selected = _sampling_shuffle(list(SAMPLING_DEMO_POPULATION), seed)[:sample_size]
    elif method == "stratified":
        per_stratum = sample_size // 2
        selected = _sampling_shuffle(
            [row for row in SAMPLING_DEMO_POPULATION if row["stratum"] == "Section A"], seed
        )[:per_stratum]
        selected += _sampling_shuffle(
            [row for row in SAMPLING_DEMO_POPULATION if row["stratum"] == "Section B"],
            seed + 1_000_003,
        )[:per_stratum]
    elif method == "front_row_convenience":
        selected = [row for row in SAMPLING_DEMO_POPULATION if row["front_row"]][:sample_size]
    else:
        raise ValueError("Unknown sampling demonstration method.")

    assignment: dict[str, str] = {}
    if random_assignment:
        assigned_order = _sampling_shuffle(selected, seed ^ 0x9E3779B9)
        midpoint = len(assigned_order) // 2
        assignment = {
            str(row["id"]): "Treatment A" if index < midpoint else "Treatment B"
            for index, row in enumerate(assigned_order)
        }
    practice_total = sum(int(row["practice_problems"]) for row in selected)
    population_total = sum(int(row["practice_problems"]) for row in SAMPLING_DEMO_POPULATION)
    return {
        "selected": selected,
        "assignment": assignment,
        "sample_mean": practice_total / len(selected),
        "population_mean": population_total / len(SAMPLING_DEMO_POPULATION),
        "stratum_counts": {
            name: sum(row["stratum"] == name for row in selected)
            for name in ("Section A", "Section B")
        },
    }


def animal_cell_map_fallback(focus_ids: list[str] | tuple[str, ...]) -> str:
    """Return the fixed descriptions for the selected cell-map features."""
    details = []
    for organelle_id in focus_ids:
        organelle = ANIMAL_CELL_ORGANELLES[organelle_id]
        details.append(f"{organelle['name']}: {organelle['description']}")
    return f"{ANIMAL_CELL_LIMITATION} " + " ".join(details)


def animal_cell_map_alt_text(focus_ids: list[str] | tuple[str, ...]) -> str:
    names = ", ".join(str(ANIMAL_CELL_ORGANELLES[item]["name"]) for item in focus_ids)
    return f"{ANIMAL_CELL_LIMITATION} Numbered features: {names}."


def binary_search_trace(values: list[int], target: int) -> tuple[list[dict], int | None]:
    """Return the deterministic lower-middle binary-search trace and matching index."""
    trace: list[dict] = []
    low, high = 0, len(values) - 1
    while low <= high:
        middle = (low + high) // 2
        value = values[middle]
        if target == value:
            comparison = "equal"
            trace.append(
                {
                    "low": low,
                    "high": high,
                    "middle": middle,
                    "value": value,
                    "comparison": comparison,
                }
            )
            return trace, middle
        if target < value:
            comparison = "smaller"
            trace.append(
                {
                    "low": low,
                    "high": high,
                    "middle": middle,
                    "value": value,
                    "comparison": comparison,
                }
            )
            high = middle - 1
        else:
            comparison = "larger"
            trace.append(
                {
                    "low": low,
                    "high": high,
                    "middle": middle,
                    "value": value,
                    "comparison": comparison,
                }
            )
            low = middle + 1
    return trace, None


def _num(value: float, digits: int = 2) -> str:
    return f"{value:.{digits}f}".rstrip("0").rstrip(".") or "0"


def guide_block_fallback(block: dict) -> str:
    """Build text available to clients that cannot render a particular template."""
    kind = block.get("type")
    if kind in {"paragraph", "takeaway"}:
        return str(block.get("text") or "")
    if kind == "formula":
        result = "Formula: $" + str(block.get("expression") or "") + "$"
        definitions = block.get("definitions") or []
        if definitions:
            result += " " + "; ".join(
                f"{item['symbol']}: {item['meaning']}"
                + (f" ({item['unit']})" if item.get("unit") else "")
                for item in definitions
            )
        return result
    if kind == "example_steps":
        steps = block.get("steps") or []
        return "Worked example: " + " ".join(
            f"Step {index}: {step.get('title', '')}. {step.get('explanation', '')}"
            + (f" Expression: ${step['expression']}$." if step.get("expression") else "")
            for index, step in enumerate(steps, 1)
        )
    if kind == "labeled_diagram" and block.get("template_id") == "process_pathway_v1":
        stages = (block.get("parameters") or {}).get("stages", [])
        return "Process pathway: " + " → ".join(
            f"{stage.get('title', '')}: {stage.get('explanation', '')}" for stage in stages
        )
    if kind == "labeled_diagram" and block.get("template_id") == "animal_cell_map_v1":
        focus_ids = (block.get("parameters") or {}).get("focus_ids", [])
        return animal_cell_map_fallback(focus_ids)

    parameters = block.get("parameters") or {}
    template_id = block.get("template_id")
    if template_id == "force_components_v1":
        force = float(parameters["force_n"])
        angle = float(parameters["angle_deg"])
        horizontal = force * math.cos(math.radians(angle))
        vertical = force * math.sin(math.radians(angle))
        return (
            f"For a {_num(force)} N force at {_num(angle)} degrees above the positive horizontal "
            f"axis, the horizontal component is {_num(horizontal)} N and the vertical component "
            f"is {_num(vertical)} N."
        )
    if template_id == "linear_graph_v1":
        slope = float(parameters["slope"])
        intercept = float(parameters["intercept"])
        equation = f"y = {_num(slope)}x"
        equation += f" - {_num(abs(intercept))}" if intercept < 0 else f" + {_num(intercept)}"
        return (
            f"The plotted linear relationship is {equation}. "
            "The slope sets the change in y for each unit increase in x; the intercept is y when "
            "x is zero."
        )
    if template_id == "first_order_response_v1":
        initial = float(parameters["initial_value"])
        final = float(parameters["final_value"])
        tau = float(parameters["time_constant_s"])
        one_tau = final + (initial - final) * math.exp(-1)
        return (
            f"This first-order response starts at {_num(initial)}, approaches {_num(final)}, "
            f"and has a time constant of {_num(tau)} s. After one time constant it is about "
            f"{_num(one_tau)}."
        )
    if template_id == "resistor_divider_v1":
        voltage = float(parameters["voltage_v"])
        r1 = float(parameters["r1_ohm"])
        r2 = float(parameters["r2_ohm"])
        output = voltage * r2 / (r1 + r2)
        current = voltage / (r1 + r2)
        return (
            f"For an ideal divider with a {_num(voltage)} V input, R₁ = {_num(r1)} Ω and "
            f"R₂ = {_num(r2)} Ω, the output across R₂ is {_num(output)} V and the series current "
            f"is {_num(current * 1000)} mA."
        )
    if template_id == "binary_search_trace_v1":
        values = parameters["values"]
        target = int(parameters["target"])
        trace, result = binary_search_trace(values, target)
        conclusion = (
            f"The target is at index {result}."
            if result is not None
            else "The target is not in the list."
        )
        return (
            f"Binary search checks the sorted values {values} for {target} in "
            f"{len(trace)} comparisons. "
            + conclusion
        )
    if template_id == "sampling_demo_v1":
        method = str(parameters["method"])
        result = sampling_demo_result(
            method,
            int(parameters["sample_size"]),
            int(parameters["seed"]),
            bool(parameters["random_assignment"]),
        )
        method_name = {
            "simple_random": "simple random sample",
            "stratified": "proportional stratified sample",
            "front_row_convenience": "front-row convenience sample",
        }[method]
        selected_ids = ", ".join(str(row["id"]) for row in result["selected"])
        text = (
            "App-authored synthetic illustration, not source data: this fixed population is "
            "24 fictional students and their practice-problem counts. The selected "
            f"{method_name} contains {parameters['sample_size']} students ({selected_ids}); "
            f"its mean is {_sampling_num(result['sample_mean'])} problems, compared with the fixed "
            f"population mean of {_sampling_num(result['population_mean'])}. "
            "The front-row convenience group is deliberately constructed with higher values "
            "to illustrate one possible bias, not a universal claim. Sampling chooses who "
            "enters a study; random assignment chooses which treatment sampled participants "
            "receive. This seeded pseudorandom draw is a repeatable teaching demonstration, "
            "not a production sampling method."
        )
        if parameters["random_assignment"]:
            groups = {"Treatment A": [], "Treatment B": []}
            for row in result["selected"]:
                groups[result["assignment"][str(row["id"])]].append(str(row["id"]))
            text += (
                " Random assignment in this illustration places "
                f"{', '.join(groups['Treatment A'])} in Treatment A and "
                f"{', '.join(groups['Treatment B'])} in Treatment B."
            )
        return text
    if template_id == "molecule_2d_v1":
        molecule = MOLECULES[parameters["molecule_id"]]
        return f"{molecule['name']} ({molecule['formula']}): {molecule['description']}"
    return str(
        block.get("fallback_text")
        or "This activity is unavailable; the explanation remains available."
    )


def guide_block_alt_text(block: dict) -> str:
    template_id = block.get("template_id")
    if template_id == "force_components_v1":
        return guide_block_fallback(block)
    if template_id == "linear_graph_v1":
        return "A coordinate graph of the line y = mx + b, with adjustable slope and intercept."
    if template_id == "first_order_response_v1":
        return "A graph of a first-order response approaching its final value over time."
    if template_id == "resistor_divider_v1":
        return (
            "An ideal two-resistor series divider with output measured across the lower resistor."
        )
    if template_id == "binary_search_trace_v1":
        return "A step-by-step trace of binary search over a sorted list."
    if template_id == "molecule_2d_v1":
        molecule = MOLECULES[block["parameters"]["molecule_id"]]
        return f"{molecule['name']}, formula {molecule['formula']}. {molecule['description']}"
    if template_id == "process_pathway_v1":
        stages = block["parameters"]["stages"]
        return "An ordered process pathway with " + str(len(stages)) + " selectable stages."
    if template_id == "animal_cell_map_v1":
        focus_ids = block["parameters"]["focus_ids"]
        return animal_cell_map_alt_text(focus_ids)
    if template_id == "sampling_demo_v1":
        return (
            "An app-authored synthetic population of 24 fictional students shows a selected "
            "sample, its mean practice-problem count, and optional randomized treatment groups. "
            "The population and values are illustrative, not course source data."
        )
    return str(block.get("alt_text") or "Interactive educational diagram.")
