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

# Fixed catalog mirrored by the browser's app-authored 3D molecule activity. These
# descriptive records are not loaded from the model and do not contain geometry
# payloads supplied by the generated guide.
MOLECULE_3D_CATALOG: dict[str, dict[str, str]] = {
    "water": {
        "name": "Water",
        "formula": "H₂O",
        "geometry": "bent molecular geometry",
        "description": "One oxygen atom is bonded to two hydrogen atoms in a bent arrangement.",
        "bond_description": "Two O–H single bonds; the H–O–H arrangement is bent.",
    },
    "carbon_dioxide": {
        "name": "Carbon dioxide",
        "formula": "CO₂",
        "geometry": "linear molecular geometry",
        "description": "A central carbon atom forms a double bond with an oxygen atom at each end.",
        "bond_description": "Two C=O double bonds arranged as O=C=O.",
    },
    "methane": {
        "name": "Methane",
        "formula": "CH₄",
        "geometry": "tetrahedral molecular geometry",
        "description": "A central carbon atom is bonded to four hydrogen atoms in a tetrahedral arrangement.",
        "bond_description": "Four C–H single bonds arranged tetrahedrally.",
    },
    "ammonia": {
        "name": "Ammonia",
        "formula": "NH₃",
        "geometry": "trigonal pyramidal molecular geometry",
        "description": "A nitrogen atom is bonded to three hydrogen atoms in a trigonal pyramidal arrangement.",
        "bond_description": "Three N–H single bonds in a trigonal pyramidal arrangement.",
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


def _bounded_text(value: str, limit: int = 500) -> str:
    """Keep deterministic text alternatives inside the browser activity contract."""
    value = " ".join(value.split())
    if len(value) <= limit:
        return value
    return value[: limit - 1].rsplit(" ", 1)[0].rstrip(" ,;:") + "…"


def _graph_traversal(parameters: dict) -> tuple[list[str], list[str]]:
    nodes = parameters["nodes"]
    adjacency = {node["id"]: [] for node in nodes}
    for edge in parameters["edges"]:
        adjacency[edge["source"]].append(edge["target"])
    for neighbors in adjacency.values():
        neighbors.sort()

    visits: list[str] = []
    seen = {parameters["start_node"]}
    if parameters.get("algorithm", "bfs") == "dfs":
        def visit(node_id: str) -> None:
            visits.append(node_id)
            for neighbor in adjacency[node_id]:
                if neighbor not in seen:
                    seen.add(neighbor)
                    visit(neighbor)

        visit(parameters["start_node"])
    else:
        queue = [parameters["start_node"]]
        for node_id in queue:
            visits.append(node_id)
            for neighbor in adjacency[node_id]:
                if neighbor not in seen:
                    seen.add(neighbor)
                    queue.append(neighbor)
    unreachable = sorted(set(adjacency) - seen)
    return visits, unreachable


def _stress_state(stress_mpa: list[list[float]]) -> dict:
    """Small deterministic Jacobi eigensolver for a validated symmetric 3x3 tensor."""
    matrix = [[float(value) for value in row] for row in stress_mpa]
    vectors = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
    for _ in range(40):
        p, q = 0, 1
        if abs(matrix[0][2]) > abs(matrix[p][q]):
            p, q = 0, 2
        if abs(matrix[1][2]) > abs(matrix[p][q]):
            p, q = 1, 2
        off_diagonal = matrix[p][q]
        if abs(off_diagonal) < 1e-13:
            break
        tau = (matrix[q][q] - matrix[p][p]) / (2 * off_diagonal)
        tangent = (1 if tau >= 0 else -1) / (abs(tau) + math.sqrt(1 + tau * tau))
        cosine = 1 / math.sqrt(1 + tangent * tangent)
        sine = tangent * cosine
        app, aqq = matrix[p][p], matrix[q][q]
        matrix[p][p] = app - tangent * off_diagonal
        matrix[q][q] = aqq + tangent * off_diagonal
        matrix[p][q] = matrix[q][p] = 0.0
        for index in range(3):
            if index != p and index != q:
                aip, aiq = matrix[index][p], matrix[index][q]
                matrix[index][p] = matrix[p][index] = cosine * aip - sine * aiq
                matrix[index][q] = matrix[q][index] = sine * aip + cosine * aiq
            vip, viq = vectors[index][p], vectors[index][q]
            vectors[index][p] = cosine * vip - sine * viq
            vectors[index][q] = sine * vip + cosine * viq

    eigenpairs = []
    for index in range(3):
        direction = [vectors[row][index] for row in range(3)]
        norm = math.sqrt(sum(component * component for component in direction))
        direction = [component / norm for component in direction]
        largest_component = max(range(3), key=lambda component: abs(direction[component]))
        if direction[largest_component] < 0:
            direction = [-component for component in direction]
        eigenpairs.append((matrix[index][index], direction))
    eigenpairs.sort(key=lambda pair: pair[0], reverse=True)
    values = [pair[0] for pair in eigenpairs]
    e1, _e2, e3 = [pair[1] for pair in eigenpairs]
    normal = [e1[index] + e3[index] for index in range(3)]
    norm = math.sqrt(sum(component * component for component in normal))
    normal = [component / norm for component in normal]
    traction = [sum(stress_mpa[row][column] * normal[column] for column in range(3)) for row in range(3)]
    normal_stress = sum(normal[index] * traction[index] for index in range(3))
    shear = [traction[index] - normal_stress * normal[index] for index in range(3)]
    shear_magnitude = math.sqrt(sum(component * component for component in shear))
    eigenvalue_scale = max(1.0, *(abs(value) for value in values))
    repeated_principal_tolerance = 1e-8 * eigenvalue_scale

    def dot(left: list[float], right: list[float]) -> float:
        return sum(left[index] * right[index] for index in range(3))

    def subtract(left: list[float], right: list[float]) -> list[float]:
        return [left[index] - right[index] for index in range(3)]

    def cross(left: list[float], right: list[float]) -> list[float]:
        return [
            left[1] * right[2] - left[2] * right[1],
            left[2] * right[0] - left[0] * right[2],
            left[0] * right[1] - left[1] * right[0],
        ]

    vertices = [[x, y, z] for x in (-1.0, 1.0) for y in (-1.0, 1.0) for z in (-1.0, 1.0)]
    intersections: list[list[float]] = []

    def append_unique(point: list[float]) -> None:
        if not any(math.dist(existing, point) < 1e-8 for existing in intersections):
            intersections.append(point)

    for first in range(len(vertices)):
        for second in range(first + 1, len(vertices)):
            a, b = vertices[first], vertices[second]
            differing = [axis for axis in range(3) if a[axis] != b[axis]]
            if len(differing) != 1:
                continue
            da, db = dot(normal, a), dot(normal, b)
            if abs(da) < 1e-9 and abs(db) < 1e-9:
                append_unique(a)
                append_unique(b)
            elif da * db <= 0:
                fraction = da / (da - db)
                if -1e-10 <= fraction <= 1 + 1e-10:
                    append_unique([a[i] + fraction * (b[i] - a[i]) for i in range(3)])
    if len(intersections) >= 3:
        center = [sum(point[index] for point in intersections) / len(intersections) for index in range(3)]
        reference = [1.0, 0.0, 0.0] if abs(normal[0]) < 0.8 else [0.0, 1.0, 0.0]
        projection = dot(reference, normal)
        u_raw = [reference[i] - projection * normal[i] for i in range(3)]
        u_length = math.sqrt(dot(u_raw, u_raw))
        u = [component / u_length for component in u_raw]
        v = cross(normal, u)
        intersections.sort(
            key=lambda point: math.atan2(dot(subtract(point, center), v), dot(subtract(point, center), u))
        )
    return {
        "principal_stresses": values,
        "normal": normal,
        "normal_stress": normal_stress,
        "shear": shear,
        "shear_magnitude": shear_magnitude,
        "plane_vertices": intersections,
        "second_principal_direction": eigenpairs[1][1],
        "orientation_unique": (
            abs(values[0] - values[1]) > repeated_principal_tolerance
            and abs(values[1] - values[2]) > repeated_principal_tolerance
        ),
    }


def _python_algorithm_trace(parameters: dict) -> dict:
    algorithm = parameters["algorithm"]
    values = list(parameters["values"])
    steps: list[str] = []
    result: dict = {"steps": steps}
    if algorithm == "linear_search":
        target = parameters["target"]
        found_index = None
        for index, value in enumerate(values):
            steps.append(f"Check index {index}: {value}")
            if value == target:
                found_index = index
                steps.append(f"Found {target} at index {index}.")
                break
        if found_index is None:
            steps.append(f"{target} is not in the list.")
        result["found_index"] = found_index
    elif algorithm == "binary_search":
        target = parameters["target"]
        low, high, found_index = 0, len(values) - 1, None
        while low <= high:
            middle = (low + high) // 2
            value = values[middle]
            steps.append(f"low={low}, high={high}, middle={middle}: compare {value}")
            if value == target:
                found_index = middle
                steps.append(f"Found {target} at index {middle}.")
                break
            if value < target:
                low = middle + 1
            else:
                high = middle - 1
        if found_index is None:
            steps.append(f"{target} is not in the list.")
        result["found_index"] = found_index
    else:
        steps.append(f"Start: {values}")
        for index in range(1, len(values)):
            current = values[index]
            position = index
            while position > 0 and values[position - 1] > current:
                values[position] = values[position - 1]
                position -= 1
            values[position] = current
            steps.append(f"Insert {current}: {values}")
        result["sorted_values"] = values
    return result


def _simcir_truth_table(circuit_id: str) -> list[dict[str, int]]:
    rows = []
    for a in (0, 1):
        for b in (0, 1):
            if circuit_id == "and_gate":
                outputs = {"Y": int(bool(a and b))}
            elif circuit_id == "or_gate":
                outputs = {"Y": int(bool(a or b))}
            else:
                outputs = {"SUM": int(a != b), "CARRY": int(bool(a and b))}
            rows.append({"A": a, "B": b, **outputs})
    return rows


def guide_block_fallback(block: dict) -> str:
    """Build text available to clients that cannot render a particular template."""
    kind = block.get("type")
    if kind == "svg_diagram":
        return str(block.get("fallback_text") or block.get("alt_text") or "Custom SVG diagram.")
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
    if template_id == "graph_traversal_v1":
        algorithm = parameters.get("algorithm", "bfs").upper()
        labels = {node["id"]: node["label"] for node in parameters["nodes"]}
        start = labels[parameters["start_node"]]
        visited_ids, unreachable_ids = _graph_traversal(parameters)
        shown = [labels[node_id] for node_id in visited_ids[:6]]
        order = " → ".join(shown)
        if len(visited_ids) > len(shown):
            order += f" → … ({len(visited_ids) - len(shown)} more)"
        text = (
            f"{algorithm} on a directed graph starts at {start} and visits "
            f"{len(visited_ids)} of {len(parameters['nodes'])} nodes in this order: {order}."
        )
        if unreachable_ids:
            text += f" Unreachable nodes: {', '.join(labels[node_id] for node_id in unreachable_ids[:4])}"
            if len(unreachable_ids) > 4:
                text += f" and {len(unreachable_ids) - 4} more"
            text += "."
        return _bounded_text(text)
    if template_id == "stress_cube_3d_v1":
        state = _stress_state(parameters["stress_mpa"])
        sigma1, sigma2, sigma3 = state["principal_stresses"]
        normal = ", ".join(_num(component, 3) for component in state["normal"])
        orientation = (
            "the plane orientation is not unique because an extreme principal stress is repeated"
            if not state["orientation_unique"]
            else "the absolute maximum-shear plane is 45 degrees from the σ₁ and σ₃ directions"
        )
        return (
            "For this symmetric stress tensor (MPa), the principal stresses are "
            f"σ₁ = {_num(sigma1)}, σ₂ = {_num(sigma2)}, and σ₃ = {_num(sigma3)}. "
            f"The absolute maximum shear stress is τmax = (σ₁ − σ₃)/2 = "
            f"{_num(state['shear_magnitude'])} MPa. The selected plane normal is "
            f"n = ({normal}); {orientation}. Tension is positive."
        )
    if template_id == "molecule_3d_v1":
        molecule = MOLECULE_3D_CATALOG[parameters["molecule_id"]]
        return (
            f"{molecule['name']} ({molecule['formula']}): {molecule['geometry']}. "
            f"{molecule['description']} Geometry is an app-authored idealized teaching model."
        )
    if template_id == "python_algorithm_trace_v1":
        result = _python_algorithm_trace(parameters)
        algorithm = parameters["algorithm"].replace("_", " ")
        values = ", ".join(str(value) for value in parameters["values"])
        if parameters["algorithm"] in {"linear_search", "binary_search"}:
            found = result["found_index"]
            outcome = f"found at index {found}" if found is not None else "not found"
            return (
                f"Fixed Python {algorithm} checks [{values}] for {parameters['target']}; "
                f"the target is {outcome} after "
                f"{sum(step.startswith('Check index') or ': compare ' in step for step in result['steps'])} comparisons."
            )
        sorted_values = ", ".join(str(value) for value in result["sorted_values"])
        return f"Fixed Python insertion sort changes [{values}] into [{sorted_values}]."
    if template_id == "simcir_circuit_v1":
        circuit_id = parameters["circuit_id"]
        title, description = {
            "and_gate": ("AND gate", "The output is 1 only when both inputs are 1."),
            "or_gate": ("OR gate", "The output is 1 when at least one input is 1."),
            "half_adder": ("Half adder", "XOR gives the sum and AND gives the carry."),
        }[circuit_id]
        rows = _simcir_truth_table(circuit_id)
        output_names = ("Y",) if circuit_id != "half_adder" else ("SUM", "CARRY")
        table = "; ".join(
            " ".join(f"{name}={row[name]}" for name in ("A", "B", *output_names))
            for row in rows
        )
        return f"{title}: {description} Truth table (A, B, outputs): {table}."
    if template_id == "molecule_2d_v1":
        molecule = MOLECULES[parameters["molecule_id"]]
        return f"{molecule['name']} ({molecule['formula']}): {molecule['description']}"
    return str(
        block.get("fallback_text")
        or "This activity is unavailable; the explanation remains available."
    )


def guide_block_alt_text(block: dict) -> str:
    if block.get("type") == "svg_diagram":
        return str(block.get("alt_text") or block.get("fallback_text") or "Custom SVG diagram.")
    template_id = block.get("template_id")
    parameters = block.get("parameters") or {}
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
    if template_id == "graph_traversal_v1":
        parameters = block["parameters"]
        labels = {node["id"]: node["label"] for node in parameters["nodes"]}
        algorithm = parameters.get("algorithm", "bfs").upper()
        visited_ids, unreachable_ids = _graph_traversal(parameters)
        sequence = " → ".join(labels[node_id] for node_id in visited_ids[:5])
        if len(visited_ids) > 5:
            sequence += f" → … ({len(visited_ids) - 5} more)"
        if len(unreachable_ids) == 1:
            unreachable_text = " 1 node is unreachable from the start node."
        elif unreachable_ids:
            unreachable_text = f" {len(unreachable_ids)} nodes are unreachable from the start node."
        else:
            unreachable_text = ""
        return _bounded_text(
            f"A directed graph with {len(parameters['nodes'])} nodes and "
            f"{len(parameters['edges'])} edges is traversed using {algorithm} from "
            f"{labels[parameters['start_node']]}. Visit order: {sequence}.{unreachable_text}"
        )
    if template_id == "stress_cube_3d_v1":
        state = _stress_state(parameters["stress_mpa"])
        sigma1, _sigma2, sigma3 = state["principal_stresses"]
        return (
            "Three-dimensional stress cube for a symmetric MPa tensor. "
            f"Principal stresses are {_num(sigma1)}, {_num(state['principal_stresses'][1])}, "
            f"and {_num(sigma3)} MPa; the maximum shear is "
            f"{_num(state['shear_magnitude'])} MPa. The plane normal and in-plane shear "
            "traction are computed from t = σn."
        )
    if template_id == "molecule_3d_v1":
        molecule = MOLECULE_3D_CATALOG[block["parameters"]["molecule_id"]]
        return (
            f"Rotatable 3D model of {molecule['name']} ({molecule['formula']}), "
            f"showing {molecule['geometry']}."
        )
    if template_id == "python_algorithm_trace_v1":
        algorithm = parameters["algorithm"].replace("_", " ")
        return _bounded_text(
            f"A fixed Python {algorithm} walkthrough uses {len(parameters['values'])} integers "
            "from the supplied bounded example input. The application supplies all executable code."
        )
    if template_id == "simcir_circuit_v1":
        title, description = {
            "and_gate": ("AND gate", "Output Y is 1 only when both A and B are 1."),
            "or_gate": ("OR gate", "Output Y is 1 when either A or B is 1."),
            "half_adder": ("Half adder", "XOR produces SUM; AND produces CARRY."),
        }[parameters["circuit_id"]]
        return f"{title} digital logic circuit. {description}"
    return str(block.get("alt_text") or "Interactive educational diagram.")
