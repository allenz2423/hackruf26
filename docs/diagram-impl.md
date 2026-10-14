# Interactive STEM study guides: tools and implementation plan

Research date: **2026-10-14**.

Status: **research and implementation proposal**. The libraries below were researched through their official documentation, repositories, and license pages. They have not been installed, integrated, or benchmarked in this repository. Recommendations and effort estimates are engineering judgments, distinguished from documented tool capabilities.

Selection constraint: **visualization tools must work without a mandatory license fee, subscription, commercial-license purchase, or paid external API**. Use self-hosted open-source packages and freely usable or app-authored assets. This constraint applies to the visualization dependencies; infrastructure and the application's existing model-generation costs are separate.

This proposal complements the [product design](design.md), [frontend design](frontend-design.md), and [backend design](backend-design.md). Those documents remain the implementation source of truth. Agree on shared content/API changes and update the relevant designs before implementing them.

## 1. Recommended direction

Build a browser study-guide reader with optional interactive blocks inside ordinary explanations. Keep a printable PDF representation of the same material.

Start with a small component library:

| Need | Initial choice | Why this fits Jot |
| --- | --- | --- |
| Clickable labeled diagrams | App-authored SVG and HTML controls | Fits the existing browser stack and gives direct control over labels, keyboard behavior, and print layout. |
| Graphs, vectors, geometry, force components | JSXGraph | Its documented mathematical construction model matches draggable points, arrows, curves, and sliders. |
| Mathematical and chemical notation | Existing KaTeX, with its mhchem extension evaluated separately | Reuses the current formula renderer; chemical typesetting and molecular structure visualization remain separate tasks. |
| Concept maps, pathways, algorithm graphs | Cytoscape.js when these blocks are introduced | Provides a graph data model and interaction APIs; the app still supplies the teaching behavior. |
| Small-molecule 2D structures | SmilesDrawer; RDKit.js when chemical operations are needed | A depiction library is sufficient for display. A chemistry toolkit is appropriate when parsing, matching, or other chemistry operations become requirements. |
| Rotatable molecular structures | 3Dmol.js | A dedicated molecular viewer avoids building atom/bond rendering from scratch. |
| Circuit exploration | Begin with a bounded app-authored circuit template; evaluate CircuitJS1 for a larger simulator | A few reviewed circuits have a much smaller integration scope than a full editor and simulation environment. |

The supporting research and primary links appear in sections 4–9. These choices are a shortlist, not a proposal to install every package.

The first complete example should teach **force components**: explanation, formulas, a labeled vector diagram, an angle control, a related graph, a prediction prompt, citations, and a static export. It exercises the shared reader without requiring chemistry data, a circuit engine, or a general physics simulator.

## 2. What already exists in this repository

The current application already separates generated guide content from its downloadable files.

| Existing location | Current behavior | Implication for this proposal |
| --- | --- | --- |
| [frontend/package.json](../frontend/package.json) | Vanilla JavaScript/Vite with KaTeX, pdf.js, and JSZip declared. | Prefer imperative JavaScript libraries, web components, or separately embedded applications. No framework migration is required for the initial approach. |
| [backend/app/api/schemas.py](../backend/app/api/schemas.py) | `GuideSectionContent` contains heading, body, key terms, and sources. Its schema rejects extra fields. | Interactive blocks require an explicit schema extension. Sending new fields today is insufficient. |
| [backend/app/services/gemini_content.py](../backend/app/services/gemini_content.py) | Generates structured guide sections through draft/revision passes and checks source citations. | Introduce supported visual templates into this validated generation process. Keep provider configuration and prompts here. |
| [frontend/src/study.js](../frontend/src/study.js) | Adapts remote material into browser objects, retaining structured sections and also a Markdown representation. | Carry future block data through the structured path; verify adapters do not discard it or prefer a flattened representation. |
| [frontend/src/material-viewer.js](../frontend/src/material-viewer.js) | Renders guide sections as browser content and provides PDF/LaTeX downloads. | This is the starting point for a shared guide reader and block dispatcher. |
| [frontend/src/file-preview.js](../frontend/src/file-preview.js) | Separately renders upload companion documents with sections and detail levels. | A common section renderer may eventually serve both views, but generated guides should be the first rollout target. |
| [frontend/src/math.js](../frontend/src/math.js) | Escapes surrounding prose, loads KaTeX on demand, and disables trusted TeX features. | Extend the existing math path rather than adding a second inconsistent formula renderer. |
| [frontend/src/source-citations.js](../frontend/src/source-citations.js) | Opens authorized source uploads at available page/timestamp locators and shows source-change warnings. | Reuse this behavior for diagrams and their explanations. |
| [frontend/src/concept-graph.js](../frontend/src/concept-graph.js) | Displays source-backed concepts and relationships through cards and management controls. | There is graph data to reuse, but a node-link visualization still needs implementation. |
| [frontend/src/flashcards.js](../frontend/src/flashcards.js), [quiz-player.js](../frontend/src/quiz-player.js) | Provide existing study interactions and a server-backed assessment player. | Reuse appropriate components; preserve remote quiz grading and feedback-release rules. |
| [backend/app/api/class_library.py](../backend/app/api/class_library.py) | Persists guide content and generated PDF/LaTeX files before marking a guide ready; creation requires the PDF compiler. | Browser readiness independent of export would be a deliberate behavior change, not existing behavior. |
| [backend/app/services/study_files.py](../backend/app/services/study_files.py) | Uses server-owned LaTeX templates, escaped text, allow-listed math, and Tectonic. | Visual blocks need explicit export support; a browser library does not automatically change PDF generation. |
| [backend/app/services/calculator.py](../backend/app/services/calculator.py) | Implements a bounded parser backed by SymPy. | Keep authoritative symbolic operations on the existing server boundary; this is not a general simulation engine. |

Two details need particular attention during implementation:

1. The shared guide-section model is also used by upload companion documents. Decide whether a new guide-only block type is enough before changing both generation pipelines.
2. Source filtering in `_public_material_content` currently traverses known top-level item collections. Nested block citations and assets would need their own authorization/filtering paths.

This document proposes saved **generated educational content**. It does not propose recording live screen frames, audio, or tutoring conversations. Study interactions also do not expand the Live model's tool permissions.

## 3. Separate the jobs a tool might do

The word “diagram” covers several different capabilities:

| Capability | Example | What we still need to implement |
| --- | --- | --- |
| Renderer | Display arrows, nodes, atoms, or a plotted curve. | The meaning of the objects and the surrounding explanation. |
| Interaction layer | Drag a point, select an atom, change a slider. | Which actions are useful to the lesson and how the explanation responds. |
| Layout engine | Position a process graph without overlapping nodes. | Valid relationships and suitable reading order. |
| Domain engine | Solve an electrical network or recognize a molecular substructure. | Input validation, assumptions, units, supported cases, and result interpretation. |
| Learning activity | Ask a prediction, offer hints, reveal an example step. | The teaching sequence and integration with existing assessment policies. |
| Export renderer | Produce a static diagram for PDF. | A reproducible state, caption, source attribution, and connection to the existing file pipeline. |

For example, connecting resistor-shaped nodes in a diagram editor does not calculate circuit voltages. Drawing a molecule does not validate a reaction mechanism. A graphing package can plot data without knowing whether those data describe a valid physical model.

Our application should own the educational content contract. Libraries sit behind adapters rather than becoming the saved guide format.

## 4. Tools for graphs, geometry, and general diagrams

### 4.1 Native SVG with HTML controls — first choice for simple diagrams

SVG can live directly in an HTML document and provides a coordinate system, shapes, text, and accessible titles/descriptions. This makes it a useful foundation for labeled figures and a modest number of interactive objects. [MDN: SVG in HTML](https://developer.mozilla.org/en-US/docs/Web/SVG/Guides/SVG_in_HTML)

**Proposed uses:** force arrows, coordinate axes, simple beams, circuit illustrations, cell diagrams, process stages, and click-to-explain figures.

**Integration:** create elements from typed app data; associate object IDs with adjacent HTML explanations. Use ordinary buttons and numeric/range controls so dragging is an additional interaction rather than the only interaction.

**Limits:** we own layout, hit areas, resizing, and domain calculations. SVG itself does not provide a circuit solver, molecular editor, or constraints system.

**Decision:** use this for the first labeled diagram. Build a small vocabulary of trusted shapes; do not accept arbitrary generated SVG markup as the content contract.

### 4.2 JSXGraph — preferred initial mathematical interaction library

JSXGraph supports browser mathematical constructions including points, arrows, curves, sliders, vector fields, integrals, and 3D elements. Its API documents board creation and individual element types. The project offers MIT or LGPL licensing. [JSXGraph API](https://jsxgraph.org/docs/), [project overview and license](https://www.jsxgraph.org/home/)

**Proposed uses:** vector decomposition, derivative/tangent exploration, Riemann sums, geometry, coordinate transformations, and small phase-plane demonstrations.

**Integration:** an app-owned template creates a board in the block container and binds validated numeric parameters to trusted functions. Mount only when needed; dispose of the board and listeners when the reader closes or changes content.

**Limits:** mathematical drawing primitives do not establish physical correctness. We must supply units, angle conventions, constraints, and numerical fixtures. Avoid model-generated JavaScript callbacks or unrestricted JessieCode/function strings.

**Decision:** evaluate as the first added visualization dependency. Its name does not mean the application needs React or JSX.

### 4.3 D3 — useful when custom visual behavior becomes substantial

D3 provides DOM updates, scales, shapes, layouts, and interactions such as drag, zoom, and brushing. It is distributed under the ISC license. [D3 capabilities and license](https://d3js.org/)

**Proposed uses:** custom distribution explorers, linked charts, specialized vector diagrams, and visual algorithm traces.

**Integration:** use focused modules where they reduce real work, rather than bringing in a visualization framework for every figure.

**Limits:** D3 is a toolbox. The application still builds controls, scene structure, accessibility, and the scientific behavior.

**Decision:** keep as an alternative for bespoke work. Native SVG is sufficient for a small labeled figure, and JSXGraph is a closer fit for mathematical constructions.

### 4.4 Apache ECharts — strong option for data-oriented study blocks

ECharts supports multiple chart families and Canvas/SVG rendering. Its modular import guide documents loading selected charts, components, and renderers. The main project uses Apache-2.0 licensing. [ECharts overview](https://echarts.apache.org/en/index.html), [modular imports](https://echarts.apache.org/handbook/en/basics/import/), [license](https://github.com/apache/echarts/blob/master/LICENSE)

**Proposed uses:** histograms, scatter plots, time series, heatmaps, comparative experimental results, and parameter-response plots.

**Integration:** translate a narrow Jot chart schema into ECharts options. Prefer app-owned controls and labels, with resize and disposal handled by the adapter.

**Limits:** plotting a changing response still requires us to calculate or obtain the response. Do not pass unrestricted generated option objects, formatter functions, or external resource URLs straight through.

**Decision:** introduce when statistics and experimental-data activities justify a dedicated chart engine; avoid adding it alongside Plotly for the same first use case.

### 4.5 Plotly.js — alternative for scientific and 3D plots

Plotly.js exposes declarative scientific/statistical plots, including contours and 3D surfaces, with event APIs. Browser static export supports image generation; WebGL content may appear as raster content in vector exports. Its core license is MIT. [Plotly.js documentation](https://plotly.com/javascript/), [image export](https://plotly.com/javascript/static-image-export/), [license](https://raw.githubusercontent.com/plotly/plotly.js/master/LICENSE)

**Proposed uses:** response surfaces, spectra, contour plots, multidimensional measurements, and statistics lessons.

**Integration:** self-host and lazy-load the needed Plotly.js distribution, then map bounded app data into traces/layout. Use the open-source library without a cloud account or paid enterprise product.

**Limits:** it is a plotting engine, not a solver. Measure the actual production chunk before selecting it; do not promise a download size based on generic package comparisons.

**Decision:** choose over ECharts if its specific scientific/3D plot features are central to a pilot.

### 4.6 Konva — optional for a larger editable 2D canvas

Konva supplies a Canvas scene graph, shapes, layers, events, and dragging. It has a vanilla JavaScript API and an MIT license. [Konva overview](https://konvajs.org/docs/overview.html), [license](https://github.com/konvajs/konva/blob/master/LICENSE)

**Proposed uses:** constructing a free-body diagram, arranging labeled objects, or a larger drag-and-drop exercise.

**Integration:** synchronize the visible scene with Jot's own object IDs and state. Keep a separate HTML representation for controls and descriptions.

**Limits:** Canvas objects do not automatically provide semantic HTML or vector PDF output. Physics constraints and grading remain separate.

**Decision:** defer until editing needs exceed what our SVG components comfortably support.

### 4.7 MathLive — optional editable mathematical expressions

MathLive provides a mathfield web component for mathematical input, including a virtual keyboard. Its project uses MIT licensing. [MathLive introduction](https://mathlive.io/mathfield/), [integration guide](https://mathlive.io/mathfield/guides/integration/), [repository and license](https://github.com/arnog/mathlive)

**Proposed uses:** entering a formula, filling a missing expression, or changing the expression associated with a supported graph activity.

**Integration:** use the web component from vanilla JavaScript and translate its input into an explicitly supported backend representation. Verify notation conversion against the existing calculator API.

**Limits:** input is separate from symbolic evaluation, physical units, and answer grading. Do not assume the separately documented Compute Engine is bundled or approved, or that arbitrary LaTeX can be sent directly to the bounded calculator parser.

**Decision:** defer until an activity actually needs expression entry. Native number/range inputs are simpler for the first parameterized model.

## 5. Tools for concept maps, processes, and computer science

### 5.1 Cytoscape.js — preferred interactive network view

Cytoscape.js supports JSON graph data, node/edge styling, layouts, interaction events, and graph algorithms. Its core and first-party extensions use MIT licensing. [Cytoscape.js documentation](https://js.cytoscape.org/)

**Proposed uses:** source-backed concept maps, metabolic pathways, prerequisite displays, graph traversal, dependency networks, and finite-state-machine exploration.

**Integration:** map authorized concept IDs and typed edges into visual elements. Selecting a node can open its explanation or source. Teaching a traversal requires an app-authored sequence of states.

**Limits:** a displayed relationship is not proof of causality or prerequisite status. Use the repository's validated relationships and direction conventions. A guide scoped to selected uploads must not silently import unrelated class content.

**Decision:** use when adding interactive graphs. Retain existing management/review controls around the visualization.

### 5.2 Mermaid — useful for read-oriented diagrams

Mermaid renders text-defined diagrams and offers an application API. Its configuration includes security level and diagram-size limits; the project uses MIT licensing. [Mermaid usage](https://mermaid.js.org/config/usage.html), [configuration schema](https://mermaid.js.org/config/schema-docs/config.html), [license](https://github.com/mermaid-js/mermaid/blob/develop/LICENSE)

**Proposed uses:** process summaries, sequence diagrams, state diagrams, and algorithm overviews.

**Integration:** generate a restricted diagram description from validated nodes and edges. Keep configuration app-owned, use strict security settings, and bind allowed navigation outside generated diagram instructions.

**Limits:** a rendered flowchart is not a process simulator or a circuit engine. Interactive stepping would need app state and event handling.

**Decision:** a good optional static/read-oriented adapter. It does not replace Cytoscape for richer graph exploration.

### 5.3 maxGraph — consider when students must build connected diagrams

maxGraph is an interactive diagramming library with vertices, edges, editing, and layout support. It is the maintained successor to mxGraph and uses Apache-2.0 licensing. [maxGraph repository](https://github.com/maxGraph/maxGraph)

**Proposed uses:** constructing flowcharts, process-flow diagrams, logic networks, and block diagrams.

**Integration:** define domain-specific palettes and connection rules, then translate the edited graph to Jot data.

**Limits:** wiring symbols does not implement logic evaluation, process balances, or circuit analysis. A general editor also introduces significantly more UI than a guided figure.

**Decision:** defer until construction exercises are a demonstrated requirement.

### 5.4 ELK.js — layout support, not a standalone viewer

ELK.js supplies graph layout algorithms, particularly useful for directed diagrams with ports. It runs in JavaScript and is licensed under EPL-2.0. [ELK.js repository](https://github.com/kieler/elkjs), [license](https://github.com/kieler/elkjs/blob/master/LICENSE.md)

**Proposed uses:** arranging circuit-like port diagrams, dependency graphs, or process networks.

**Integration:** pass bounded graph geometry to a layout worker, then render its positions through another adapter.

**Limits:** it provides neither the visible editor nor domain calculations. It also adds a separate license and worker asset to manage.

**Decision:** only add after a renderer's simpler layouts prove insufficient.

### 5.5 Algorithm steppers — build the teaching state machine ourselves

Use SVG/HTML for small arrays, stacks, and recursion frames; use Cytoscape for graph algorithms. Store the example input and choose an app-owned algorithm template.

The app should generate or verify the trace deterministically. A model-written list of algorithm steps can look plausible while containing an incorrect transition. A rendering library will not detect that error.

Good first activities include binary search, breadth-first search, stack operations, and finite-state transitions. A general code-execution sandbox is a separate project and is not required for these activities.

## 6. Tools for chemistry, molecules, and biological structures

### 6.1 KaTeX plus mhchem — chemical notation, not molecular geometry

KaTeX is already present. Its separate mhchem extension adds chemical equation/unit commands such as `\ce` and `\pu`. The extension source carries MIT and inherited Apache-2.0 notices. [KaTeX extensions](https://katex.org/docs/libs), [mhchem usage](https://github.com/KaTeX/KaTeX/blob/main/contrib/mhchem/README.md), [source notices](https://raw.githubusercontent.com/KaTeX/KaTeX/main/contrib/mhchem/mhchem.js)

**Proposed uses:** formulas, charges, reaction notation, equilibrium expressions, and units.

**Integration:** load the compatible extension with the existing KaTeX path; verify the installed version rather than copying a newer CDN example.

**Limits:** it typesets an equation; it does not balance it, determine reaction feasibility, or generate a molecular structure.

**Repository-specific issue:** the PDF math allowlist currently lacks these chemistry commands. Browser notation and PDF support need coordinated work; preserve a readable textual fallback meanwhile.

**Decision:** an early chemistry improvement with a small UI footprint, independent of a molecular viewer.

### 6.2 SmilesDrawer — practical first 2D molecule depiction

SmilesDrawer parses and draws SMILES structures and is released under MIT licensing. [Official repository and examples](https://github.com/reymond-group/smilesDrawer)

**Proposed uses:** a structural formula beside a definition, comparing a few compounds, or a small structure gallery.

**Integration:** feed a validated, bounded structure string to the adapter and add app-owned explanatory controls. Confirm the chosen build's drawing/export APIs before relying on atom-level selection.

**Limits:** it is not a full chemical editor, a mechanism grader, or a source of physical 3D coordinates. Successful depiction is not proof that the intended stereochemistry was captured.

**Decision:** evaluate for the first display-only small-molecule block. Prefer RDKit.js if chemical querying and matching are part of the same requirement.

### 6.3 RDKit.js — chemistry operations and 2D depictions

RDKit.js exposes a WebAssembly-based subset of RDKit functionality, including molecule rendering and documented chemistry operations. The official repository identifies BSD-3-Clause licensing. [RDKit.js documentation](https://rdkitjs.com/), [official repository and license statement](https://github.com/rdkit/rdkit-js)

**Proposed uses:** parsing structures, substructure highlighting, chemical fingerprints, and 2D molecule depictions.

**Integration:** load the JavaScript/WASM assets on demand from our origin; explicitly release WASM-backed molecule objects. Validate available methods against the selected build.

**Limits:** the JavaScript subset is not the full Python toolkit. Do not assume a particular conformer-generation, reaction, or geometry-optimization method exists because RDKit supports related operations elsewhere.

**Decision:** add when the chemistry engine is needed; it is more scope than a viewer-only first exercise.

### 6.4 3Dmol.js — preferred initial 3D molecular viewer

3Dmol.js is a WebGL molecular viewer with common structure-format parsers, selectable/stylable atoms, labels, and multiple visual representations. Its main license is BSD-3-Clause, with bundled component notices. [3Dmol.js API and capabilities](https://3dmol.org/doc/), [license](https://raw.githubusercontent.com/3dmol/3Dmol.js/master/LICENSE)

**Proposed uses:** rotating molecular geometry, identifying functional groups, comparing conformations supplied in the data, and inspecting protein structures.

**Integration:** start with a reviewed, bundled structure or an authorized server-provided structure asset. Keep atom IDs, selection descriptions, and label text in Jot's data model.

**Limits:** the viewer does not establish correct geometry or chemistry. A SMILES string does not by itself provide validated 3D coordinates; those need a separate supported data/conformer workflow. Avoid automatic external database fetches for private course content.

**Decision:** first 3D chemistry candidate. Provide a static image and textual structure description when WebGL is unavailable.

### 6.5 Ketcher — full chemical structure editor, later

Ketcher supports chemical structure/reaction editing and offers a ready-to-run embedded application or React components. Its standalone mode has its own initialization and service/WASM requirements. The core license is Apache-2.0, with additional repository notices. [Ketcher integration documentation](https://github.com/epam/ketcher), [license](https://raw.githubusercontent.com/epam/ketcher/master/LICENSE)

**Proposed uses:** drawing a requested molecule, editing stereochemistry, and composing reaction structures.

**Integration:** for this vanilla JavaScript app, evaluate a self-hosted standalone iframe before adopting React components. Follow the selected release's ready/init protocol; the iframe load event alone is not sufficient.

**Limits:** a structure editor does not automatically grade chemical equivalence, stereochemical correctness, or a student's curved-arrow mechanism. Identify which operations require standalone Indigo/WASM versus a separate service.

**Decision:** a later construction/editing feature. Avoid making every chemistry explanation load a full editor.

### 6.6 Mol* — advanced structural biology alternative

Mol* focuses on macromolecular visualization and has an MIT-licensed implementation. [Mol* project](https://molstar.org/), [repository](https://github.com/molstar/molstar)

**Proposed uses:** protein complexes, larger biological structures, and lessons that need richer macromolecular inspection.

**Integration:** evaluate a bounded viewer integration with controlled structure assets and a reduced UI.

**Limits:** integration and interface scope are larger than a simple rotatable molecule block. Structure/data licensing is separate from viewer licensing.

**Decision:** evaluate when a structural-biology pilot outgrows the initial 3Dmol adapter.

### 6.7 Chemistry interactions we must still author

Equation balancing, reaction-rate experiments, titration explorers, equilibrium activities, and mechanism instruction need teaching templates and chemistry rules beyond the display libraries.

For balancing, separate the displayed equation from element/charge counts and the allowed coefficient controls. For titration or equilibrium, specify the supported chemical model and assumptions. For mechanisms, distinguish illustrative step reveals from validated student-response grading.

Do not ask a viewer or notation package to make these scientific decisions.

## 7. Tools for circuits and engineering

### 7.1 App-authored circuit templates — recommended first circuit activity

Use our SVG primitives to draw a small reviewed circuit and expose a few controls. Examples include a resistor divider, series/parallel resistance, or an idealized RC charging lesson.

Each template needs a circuit definition, parameter limits, units, model assumptions, calculation implementation, and numerical reference cases. Editing a value can update a teaching visualization locally through trusted application code; authoritative calculations or assessed answers stay under backend control.

This is an implementation recommendation rather than a capability supplied by a third-party renderer. It keeps the first lesson understandable and avoids committing to a general circuit editor before one is needed.

### 7.2 CircuitJS1 / Falstad — capable browser circuit simulator

CircuitJS1 runs in the browser and provides circuit editing and simulation. The documented JavaScript interface can inspect elements, control execution, and work with circuit state; its direct control interface requires the simulator and controller to share an origin. The upstream repository identifies GPL-2.0 licensing. [CircuitJS1 repository](https://github.com/sharpie7/circuitjs1), [JavaScript interface](https://www.falstad.com/circuit/doc/js-interface.html), [license](https://github.com/sharpie7/circuitjs1/blob/master/COPYING.txt)

**Proposed uses:** resistor networks, switches, capacitors, inductors, filters, and waveform exploration.

**Integration:** evaluate a pinned, self-hosted build in an iframe with a narrow Jot adapter. Begin with reviewed circuit examples, a reduced interface, and explicit initialization/teardown behavior.

**Limits:** its application/build model differs from a small Vite module. GPL distribution obligations need to be understood for the selected integration; an iframe does not by itself settle licensing. Do not assume every API in the live documentation exists in a chosen older build.

**Decision:** strongest full browser-simulator candidate researched here, after a focused integration and licensing review. Do not describe it as an MIT-licensed drawing widget.

### 7.3 ngspice — optional server-side circuit engine

ngspice offers a SPICE simulation engine and a shared-library interface. Its manual describes a modified-BSD base with component-specific exceptions. [Shared-library interface](https://ngspice.sourceforge.io/shared.html), [official manual and licensing details](https://ngspice.sourceforge.io/docs/ngspice-manual.pdf)

**Proposed uses:** a future circuit editor whose simulation results come from a server-controlled engine.

**Integration:** translate typed circuit elements into a server-owned netlist template; return bounded numeric series to the browser. Select supported device types and analysis modes explicitly.

**Limits:** this requires a new execution service, operating limits, deployment dependencies, and solver failure handling. Never execute unrestricted submitted netlists or control scripts: file includes and other directives extend far beyond a lesson's intended inputs.

**Decision:** later alternative when a domain engine is needed independently of CircuitJS's full interface. Verify the selected release/build's notices rather than treating every component as identically licensed.

### 7.4 Schemdraw — useful for schematic export

Schemdraw is a Python diagramming package, particularly suited to circuit schematics, with documented drawing/export workflows. It uses MIT licensing. [Schemdraw documentation](https://schemdraw.readthedocs.io/en/stable/), [repository and license](https://github.com/cdelker/schemdraw)

**Proposed uses:** deterministic circuit figures in saved guides and PDF exports.

**Integration:** map a constrained circuit specification into server-authored drawing code. Associate labels and component IDs with the same data used by the browser.

**Limits:** this is a drawing tool, not an electrical simulator or an interactive browser editor.

**Decision:** evaluate when circuit export is introduced; it should not be selected as the solution to circuit interaction by itself.

### 7.5 Mechanics and structures

For force diagrams and elementary structural models, JSXGraph/SVG can handle the visual layer. Use reviewed analytic templates for the actual behavior.

Examples to scope independently include force components, a block on an idealized incline, truss joint equilibrium, a supported beam under specified loads, and stress–strain comparisons.

A broad rigid-body engine is not a substitute for a structural solver. Finite-element analysis, contact mechanics, computational fluid dynamics, and unrestricted beam/truss solvers are separate capabilities with substantially larger validation requirements.

## 8. General simulation, 3D, and spatial tools

### 8.1 Matter.js — qualitative rigid-body activities

Matter.js is a 2D rigid-body physics engine with MIT licensing. [Matter.js project](https://brm.io/matter-js/), [license](https://github.com/liabru/matter-js/blob/master/LICENSE)

**Proposed uses:** interactive collision demonstrations, constraints, and qualitative motion experiments.

**Integration:** place a reviewed scenario behind play/pause/reset controls, with controlled timestep and bounded object counts.

**Limits:** simulation parameters and numerical behavior must be calibrated for the lesson. Do not use a visually convincing run as an authoritative statics, stress, or engineering-grade result.

**Decision:** optional later. Prefer analytic templates for the initial force-components activity.

### 8.2 Three.js — general 3D visualization

Three.js provides browser 3D scene infrastructure, including cameras, geometries, materials, and controls; its license is MIT. [Three.js documentation](https://threejs.org/docs/), [license](https://github.com/mrdoob/three.js/blob/dev/LICENSE)

**Proposed uses:** crystal lattices, geometric surfaces, simple orbital scenes, and mechanical assemblies.

**Integration:** build specific app-authored scenes with bounded geometry and controlled assets. Add HTML descriptions and controls around them.

**Limits:** it does not supply scientific models, molecular semantics, or correct orbital dynamics. Geometry, textures, animation loops, and renderer resources need explicit cleanup.

**Decision:** use for a general 3D requirement; prefer a specialized molecular viewer for chemistry.

### 8.3 Leaflet — optional maps and spatial overlays

Leaflet provides interactive browser maps and vector layers under BSD-2-Clause licensing. [Leaflet project](https://leafletjs.com/), [license](https://raw.githubusercontent.com/Leaflet/Leaflet/main/LICENSE)

**Proposed uses:** watershed boundaries, geological observations, environmental measurements, and geographically located datasets.

**Integration:** load authorized data and explicitly chosen map assets; provide a legend and coordinate/data descriptions. Use bundled or self-hosted datasets and map assets that permit the intended use without fees; no paid tile provider is part of this plan.

**Limits:** library licensing does not grant rights to map tiles or datasets. A map is not an atmospheric, groundwater, or ocean-circulation solver. External tile requests also create a network dependency.

**Decision:** later, when a spatial lesson requires a map rather than a self-contained cross-section.

## 9. Tools with no mandatory license or service fees

The remaining candidates can be self-hosted under their open-source licenses. Optional paid hosting, support, or enterprise products are not dependencies of the proposed integrations.

| Need | Candidates retained | Planned deployment |
| --- | --- | --- |
| Graphing and geometry | JSXGraph, D3, ECharts, Plotly.js. | Local JavaScript packages and app-authored controls. |
| Labeled diagrams and construction | Native SVG, Konva, maxGraph. | App-owned components and locally bundled assets. |
| Concept maps and process diagrams | Cytoscape.js, Mermaid, ELK.js. | Self-hosted rendering/layout, with no hosted diagram service. |
| Mathematical and chemical input/notation | KaTeX/mhchem, MathLive. | Local packages, fonts, and optional keyboard assets. |
| Molecular depictions and structures | SmilesDrawer, RDKit.js, 3Dmol.js, Ketcher, Mol*. | Self-hosted JavaScript/WASM or standalone application, plus reviewed structure data. |
| Circuits | CircuitJS1, ngspice, Schemdraw, app-authored templates. | Self-hosted simulator or server-owned engine/drawing code. |
| General simulation, 3D, and maps | Matter.js, Three.js, Leaflet. | Local engines with app-authored or freely usable assets. |
| Static export | Existing Pillow/Tectonic, Matplotlib, Schemdraw, CairoSVG, Playwright. | Run on our infrastructure without a paid conversion API. |

The detailed capabilities, license references, and limitations remain in sections 4–8 and 13. Keep the relevant attribution, notice, and source-distribution obligations; no license fee does not mean the licenses have no conditions.

Bundle or self-host assets needed at runtime. External datasets, molecule services, map tiles, model files, and fonts must also fit the no-fee requirement. Prefer app-authored examples and reviewed freely usable datasets so a nominally free viewer does not introduce a paid data dependency.

## 10. Coverage across STEM majors

The following are proposed activities, not claims that installing a library supplies a complete validated simulator for each major.

| Field | Example interactive guide block | Visual tools | Domain work still needed |
| --- | --- | --- | --- |
| Mechanical engineering | Force components; free-body diagrams; a specified beam example. | SVG, JSXGraph; Konva if construction is needed. | Force conventions, supports, equilibrium/beam equations, units. |
| Civil/structural engineering | Select a truss joint; vary a reviewed load case. | SVG, JSXGraph, optional graph layout. | Structural model, stable/unstable cases, reactions and member forces. |
| Electrical engineering | Change a resistor or capacitor and inspect a response. | SVG plus reviewed templates; CircuitJS1 for broader simulation. | Supported circuits, analysis modes, device models and solver interpretation. |
| Computer engineering | Toggle gates; inspect a timing diagram or register state. | SVG/HTML, Cytoscape, a chart adapter. | Boolean evaluation, clock semantics and deterministic state transitions. |
| Aerospace engineering | Explore force vectors, a supplied aerodynamic curve, or an orbit. | JSXGraph, charts, optional Three.js. | Model assumptions, reference frames, dynamics and data provenance. |
| Chemical engineering | Adjust a stream and inspect a material-balance diagram. | SVG, maxGraph if editing, chart adapter. | Conservation constraints, units, thermodynamics/reaction models. |
| Biomedical engineering | A simplified joint model or signal explorer. | SVG, charts, optional Three.js. | Explicit educational model, parameter meaning and valid ranges. |
| Materials science | Crystal lattice; stress–strain curve; phase-region selection. | Three.js, JSXGraph or charts. | Valid structures, constitutive assumptions and phase-diagram data. |
| Industrial/systems engineering | Queue behavior, bottlenecks, scheduling alternatives. | SVG/HTML and charts. | Event simulation, distributions, reproducible seeds and objective definitions. |
| Environmental engineering | Flow/treatment balances and contaminant observations. | Process diagrams, charts, optional Leaflet. | Transport/removal model, units, time/space assumptions. |
| Nuclear engineering | A bounded decay-chain or shielding illustration. | Graphs, charts, SVG. | Reviewed equations/constants and explicit model limitations. |
| Chemistry | Equation notation, molecular geometry and titration plots. | KaTeX/mhchem, SmilesDrawer/RDKit.js, 3Dmol, charts. | Chemical validation, coordinates, balancing and equilibrium models. |
| Organic chemistry | Compare structures, select functional groups, step through a mechanism. | 2D molecule adapter, 3Dmol, Ketcher for editing. | Stereochemistry, atom mapping, equivalent answers and mechanism rules. |
| Physical/analytical chemistry | Spectra, kinetics curves and energy diagrams. | Scientific charts, SVG, notation renderer. | Experimental data, supported equations and chemical interpretation. |
| Physics | Motion, vectors, waves, fields and simple optics. | JSXGraph, SVG; Matter.js for selected qualitative activities. | Reviewed models, numerical behavior, assumptions and units. |
| Mathematics | Tangents, integrals, matrix transformations and surfaces. | JSXGraph, optional scientific plots/Three.js. | Domain restrictions, singularities and verified constructions. |
| Statistics/data science | Sampling, regression and uncertainty exploration. | ECharts or Plotly; D3 for custom interactions. | Correct statistics, reproducible samples and appropriate interpretation. |
| Computer science | Search/traversal traces, stacks, recursion and automata. | SVG/HTML, Cytoscape, Mermaid for summaries. | Correct traces and state-machine logic; code execution is separate. |
| Biology/genetics | Pedigrees, chromosome stages and population models. | SVG, graphs and charts. | Inheritance/model assumptions and reviewed biological content. |
| Biochemistry/molecular biology | Enzyme kinetics, pathways and protein structures. | Charts, Cytoscape, 3Dmol or Mol*. | Kinetic model, pathway meaning and authorized structure data. |
| Neuroscience/physiology | Action-potential or feedback-loop exploration. | Charts, SVG, process diagrams. | Validated educational equations, parameters and units. |
| Geology/geophysics | Geological cross-sections, fault movement and seismic traces. | SVG/Three.js, charts, optional Leaflet. | Geometry/data correctness and geological interpretation. |
| Astronomy | Orbits, spectral features and transit curves. | JSXGraph/charts, optional Three.js. | Physical model, reference frames, sampling and units. |
| Meteorology/oceanography | Profiles, fields and circulation illustrations. | Charts, SVG, optional maps/3D. | Atmospheric/ocean models and spatial/temporal data. |
| Agricultural/food science | Nutrient balances, growth curves and heat-transfer examples. | Process diagrams and charts. | Supported process models and reviewed source parameters. |

Coverage should grow by reusable **activity templates**, such as vector decomposition or first-order response, rather than by installing one unrelated application for every degree.

## 11. Proposed guide format and generation contract

### 11.1 Keep a small application-owned block vocabulary

Initial block types could be:

| Block | Stored content | Browser behavior |
| --- | --- | --- |
| Paragraph | Plain text with the existing supported math notation. | Render prose and typeset formulas. |
| Formula | Expression plus variable definitions and units. | Display notation and expandable definitions. |
| Labeled diagram | A known diagram template, objects/labels, and descriptions. | Select a labeled object and show the matching explanation. |
| Interactive model | A known activity template and validated initial parameters. | Mount the appropriate adapter with trusted controls. |
| Example steps | An ordered, reviewed analogous example. | Reveal steps through app-owned controls. |
| Takeaway | Short explanatory text. | Reinforce the idea after the activity. |

A knowledge-check block can be added later. An assessed question should reference the existing authorized assessment/run flow rather than putting a hidden answer key into a public guide payload.

Use a versioned union on the backend and a browser dispatcher over that same published vocabulary. New kinds require an explicit renderer and a fallback. The model should select only from the server-advertised supported templates.

### 11.2 Illustrative payload

This is a **proposed** version-2 guide excerpt, not an existing accepted API payload. The source UUID is a placeholder.

~~~json
{
  "format": "study_guide",
  "schema_version": 2,
  "sections": [
    {
      "id": "section-force-components",
      "heading": "Resolving a force into components",
      "body": "A force can be represented by components along chosen perpendicular axes. This example measures the angle from the positive horizontal axis. Keep the force magnitude fixed. Predict how its horizontal and vertical components change as its angle increases. For a 10 N force at 30 degrees above the positive horizontal axis, the horizontal component is approximately 8.66 N and the vertical component is 5 N.",
      "key_terms": ["force", "component", "coordinate axis"],
      "sources": [
        {
          "upload_id": "11111111-1111-4111-8111-111111111111",
          "page": 4
        }
      ],
      "blocks": [
        {
          "id": "block-components-introduction",
          "type": "paragraph",
          "text": "A force can be represented by components along chosen perpendicular axes. This example measures the angle from the positive horizontal axis. Keep the force magnitude fixed. Predict how its horizontal and vertical components change as its angle increases."
        },
        {
          "id": "block-components-explorer",
          "type": "interactive_model",
          "template_id": "force_components_v1",
          "title": "Explore force components",
          "parameters": {
            "force_n": 10,
            "angle_deg": 30
          },
          "alt_text": "A force arrow with horizontal and vertical component arrows.",
          "fallback_text": "For a 10 N force at 30 degrees above the positive horizontal axis, the horizontal component is approximately 8.66 N and the vertical component is 5 N.",
          "sources": [
            {
              "upload_id": "11111111-1111-4111-8111-111111111111",
              "page": 4
            }
          ]
        }
      ]
    }
  ]
}
~~~

The template registry, not the model payload, defines parameter types, permitted ranges, units, formulas, controls, and the rendering adapter. For the example, an initial registry entry could permit nonnegative force and first-quadrant angles only.

For a version-2 section, ordered blocks would be the primary browser presentation. Retain `body` as a server-derived reading fallback for older consumers, rather than rendering both and duplicating the explanation. Derive its text from paragraph/formula text and visual fallback descriptions; do not let two independently generated narratives drift apart.

Stable section/block IDs should be assigned or validated by the backend. They identify content, not a claim that the student has mastered it.

### 11.3 Generation sequence

1. Resolve the user's selected authorized sources through the existing backend path.
2. Draft the section explanation and identify whether a supported activity helps.
3. Select a known template and produce its bounded data.
4. Validate syntax, object references, parameter domains, units, and source references.
5. Run deterministic template checks where scientific relationships can be checked.
6. Revise the explanation and activity together against the source context.
7. Produce fallback text and the default export representation.
8. Save the validated guide and its files according to the agreed readiness policy.

Model review is useful for explanation quality, but it is not a replacement for deterministic checks of supported formulas, circuit topology, atom references, or trace transitions.

When a source does not support a useful interaction, keep that section textual. When a particular activity fails validation, either fall back to the validated explanation with an explicit visual-unavailable state or fail that activity under a defined retry policy. Never silently substitute an unrelated template.

### 11.4 Backward compatibility and rollout

Treat existing unversioned guides as version 1. They can be adapted in memory to the new reader through text blocks without calling the provider or rewriting the saved material.

Roll out version-2 generation only when the corresponding browser renderer and PDF fallback are available. Older/unsupported clients must still receive a readable representation.

Do not change the shared companion-document schema accidentally. Either introduce a guide-specific section type or explicitly update companion generation, rendering, and export in a later coordinated change.

## 12. Frontend integration and component lifecycle

### 12.1 Suggested module boundaries

The following paths are illustrative implementation targets, not files added by this research task:

~~~text
frontend/src/study-guide/
  reader.js
  normalize.js
  block-renderer.js
  template-registry.js
  controls.js
  adapters/
    svg-diagram.js
    jsxgraph.js
    cytoscape.js
    molecule-2d.js
    molecule-3d.js
    circuit.js
~~~

Keep library-specific state inside adapters. The reader handles document navigation, source links, errors, and layout. A shared normalization layer keeps local/sample, legacy, and new remote guides consistent.

An adapter contract could expose:

~~~js
mount(container, validatedBlock, context)
// Returns an instance with:
// updateState(nextState)
// reset()
// resize()
// describeState()
// dispose()
~~~

The context should contain only the app services a component needs, such as source navigation and a status announcer. It should not expose provider configuration or a general execution interface.

### 12.2 Loading and cleanup

Use a fixed mapping from supported template IDs to dynamic imports. A payload must not choose module URLs or script paths.

Load large engines when their block is opened or approaches view. For a long guide, pause animations outside the visible activity and limit how many heavy viewers are mounted simultaneously. A collapsed activity still has its caption and reading fallback.

On navigation or close, dispose of listeners, observers, animation loops, workers, and graphics resources. For WASM-backed objects, follow the selected library's explicit memory-release requirements. An asynchronous import finishing after disposal must not remount an abandoned block.

Libraries must receive containers scoped to the current block. Avoid document-wide selectors and globals that would make multiple activities interfere with one another.

### 12.3 Accessibility and responsive behavior

Every block needs a title, explanation, and text alternative. A pointer gesture should have an equivalent numeric input, keyboard action, or list selection where practical.

Use units in control labels, visible focus, sufficient hit areas, and information beyond color alone. Expose selected-object explanations in ordinary HTML. Avoid flooding a live region with announcements during every animation frame.

On narrow screens, stack controls below the figure. Provide an explicit expand/close behavior that restores focus and reading position. Reduced-motion preferences should disable decorative animation without removing access to the scientific relationship.

These are responsibilities of the Jot wrapper; a library's accessibility features do not establish that the assembled activity is accessible.

### 12.4 State and existing product boundaries

Keep initial slider/object-selection state in memory. Saving a guide's authored default state is different from storing a student's interaction history.

Do not create persistent learner event logs, new mastery signals, or per-slider recordings as a side effect of adding visuals. Any future completion/history feature needs a separate product and data-contract decision.

The backend continues to own provider SDKs, credentials, model configuration, prompts, provider tools, and authoritative assessment behavior. Model-produced guide content is validated data, not a new Live action/tool channel.

Rendering deterministic app-authored animations in response to student controls does not require granting a model mouse, keyboard, shell, application-control, or unrestricted drawing tools.

### 12.5 Content and asset handling

Use typed IDs and numeric data rather than arbitrary HTML, SVG, JavaScript, CSS, or library callback strings. Create labels as text. Keep KaTeX trust settings restrictive.

For molecule/mesh assets, resolve an authorized asset ID through the backend. Do not let a guide introduce arbitrary fetch URLs. Model-generated SMILES, coordinates, or graph topology require domain validation or a reviewed dataset, even when the viewer can parse them.

If an iframe application is selected, define its exact hosting and messaging model. Check message source, origin, and payload shape. A same-origin iframe allows direct integration but is not a strong isolation boundary from the parent application.

Bound object counts, label sizes, graph size, sample counts, mesh complexity, and simulation duration per template. This keeps a valid-looking guide from freezing the reader.

## 13. PDF and static representation

### 13.1 Keep the existing export pipeline as the initial baseline

The current guide pipeline uses Tectonic and commits content/files together. For the first prototype, retaining that readiness policy avoids introducing unrelated status and recovery changes.

An independent browser-ready/export-pending state could be useful later, but it requires an explicit API/design change and client recovery behavior. It should not be silently inferred from this proposal.

Define a static representation before enabling each new activity:

| Browser activity | PDF representation |
| --- | --- |
| Clickable labeled figure | Diagram with all essential labels and accompanying explanations. |
| Graph with a slider | A default plot, parameter values, and selected comparison curves when needed. |
| Force diagram | The default arrows, angle/unit conventions, and a component summary. |
| Molecule viewer | One or more useful views, a structure identifier, and a textual description. |
| Circuit activity | Schematic, component values, assumptions, and selected response plots. |
| Algorithm/process stepper | Key states or numbered steps with captions. |

Export the guide's authored default or designated comparison state, not whichever transient state a student happened to leave on their screen. Include enough description to make the static lesson usable without an account link or interactive control.

### 13.2 Export tools to evaluate

| Tool/path | What it supplies | Fit and constraints |
| --- | --- | --- |
| Existing Pillow plus trusted drawing code | Server-generated raster diagrams. | Already a backend dependency; suitable for simple figures. The application owns layout and labels. |
| Matplotlib | Noninteractive plotting backends and PNG/PDF/SVG output. | Good for static numerical plots; uses its own project license. Adds a Python dependency and requires parity checks with the browser data. |
| Schemdraw | Server-authored schematic figures. | Useful circuit export companion; does not replace a circuit solver. |
| CairoSVG | Conversion of SVG to PNG/PDF, with an LGPLv3 license. | Adds Cairo/native deployment considerations. Convert only controlled SVG and constrain external resource access; do not enable its unsafe mode. |
| Plotly browser image export | Static images from a plotted figure. | Useful when Plotly is already selected; WebGL layers can be rasterized inside vector output. It does not by itself provide a server export job. |
| Playwright rendering | Element/page images produced by a controlled browser; Apache-2.0 project license. | Can reuse browser components, but adds a browser runtime, a render worker, and deterministic readiness requirements. |

Primary sources: [Matplotlib backends](https://matplotlib.org/stable/users/explain/figure/backends.html), [Matplotlib license](https://github.com/matplotlib/matplotlib/blob/main/LICENSE/LICENSE), [CairoSVG documentation](https://cairosvg.org/documentation/), [Plotly export](https://plotly.com/javascript/static-image-export/), [Playwright screenshots](https://playwright.dev/docs/screenshots), [Playwright license](https://github.com/microsoft/playwright/blob/main/LICENSE). Schemdraw sources are in section 7.

**Recommendation:** begin with a server-authored static export for the first template. Evaluate a controlled browser renderer only when keeping many browser/export adapters in sync becomes more expensive than operating a browser worker.

Generated diagram rendering is distinct from capturing a student's screen. An export job should receive only validated saved guide content and use trusted application templates; it must not capture or persist Live preview frames.

### 13.3 Integration details still required

The existing LaTeX template does not automatically embed browser SVG, Canvas, WebGL, or HTML. Add a deliberate image-embedding path, compatible formats, resource limits, and packaged fonts. Do not enable unrestricted TeX execution to accommodate diagrams.

Keep diagram asset IDs server-generated and scoped to the guide's access policy. Existing `study_material_files` formats are PDF/LaTeX; persisting additional standalone assets would require a storage/schema decision rather than inserting arbitrary new formats.

Temporary export artifacts can be created from the saved block specification and removed after the final files are produced. Do not add a per-interaction screenshot archive.

Any browser-based renderer should load only its controlled assets, wait for fonts/library readiness, disable time-dependent animation, use a fixed viewport and seed, and have bounded execution. Offline generation must not accidentally depend on public CDNs, map tiles, or molecule services.

### 13.4 PDF-first presentation remains possible

The repository already includes pdf.js. It supports page rendering through a viewport that accounts for scale and rotation. [PDF.js rendering examples](https://mozilla.github.io/pdf.js/examples/)

If the desired experience is a PDF page with controls beside it, use section/page associations to open the corresponding activity. Exact over-page widgets additionally need authored region coordinates tied to a specific exported PDF revision, with scaling and rotation handled by the viewer.

For the initial product, prefer a section-based browser reader. It avoids coupling every guide edit to PDF page/region mapping while preserving downloadable documents.

## 14. Implementation sequence and ownership

Effort labels below are relative planning estimates, not measured delivery dates. Library integration is only part of the work; scientific validation and export can dominate.

| Milestone | Deliverable | Main ownership | Relative effort |
| --- | --- | --- | --- |
| A. Agree on the block contract | Versioning, template vocabulary, source rules, fallback semantics, and readiness policy documented. | Shared design/API coordination. | Small to medium. |
| B. Build the reader foundation | Legacy adaptation, section navigation, block dispatcher, text/formula/diagram rendering, and sources. | Frontend. | Medium. |
| C. Ship one reviewed interactive template | Force components with controls, graph, text alternative, deterministic calculations, and static export. | Frontend adapter plus backend schema/generation/export. | Medium. |
| D. Generate compatible content | Draft/revision selects the supported template, validates data, and falls back clearly. | Backend; coordinated browser consumption. | Medium. |
| E. Add a chemistry pilot | Chemical notation, a 2D depiction, then a reviewed 3D structure if needed. | Frontend; backend asset/schema and export work. | Medium to large. |
| F. Evaluate circuits | One bounded circuit lesson; separate CircuitJS1 integration spike if full simulation is desired. | Frontend and backend coordination. | Medium for a template; large for a full simulator. |
| G. Expand reusable families | Graph activities, pathway views, algorithm traces, and subject-specific templates. | Per-template split. | Ongoing. |

Implementations remain within the active role's ownership. A frontend task should describe required backend contract work and leave backend implementation to that role, and vice versa.

Do not install every shortlisted package in milestone B. First compare a small JSXGraph prototype against an SVG-only alternative, inspect actual bundle output, and choose the simpler approach that meets the interaction requirements.

Suggested first template acceptance criteria:

1. The reader opens an existing saved guide and a new block-based guide.
2. A force arrow and its components use a documented coordinate convention.
3. Changing angle or magnitude updates the diagram, graph, and text consistently.
4. Keyboard and numeric inputs provide the same activity as pointer controls.
5. Reset restores the authored default.
6. Default numerical values match deterministic reference cases, including endpoint angles.
7. Sources remain accessible through the existing authorization path.
8. PDF contains a meaningful static representation with units and captions.
9. Closing/reopening does not leave timers, workers, or graphics instances behind.
10. A failed optional renderer leaves the explanation readable.

## 15. Verification, open questions, and adoption records

### 15.1 Meaningful checks before adopting an adapter

| Area | Checks that answer real integration questions |
| --- | --- |
| Scientific correctness | Reference calculations; units; coordinate conventions; parameter endpoints; invalid and singular states. |
| Chemistry | Invalid structures; atom-ID consistency; stereochemical examples; coordinate provenance; unsupported operations. |
| Circuit behavior | Known DC/transient examples; unsupported topology; disconnected nodes; convergence/failure reporting. |
| Reader behavior | Multiple blocks, stale async loads, navigation teardown, resize, keyboard operation, touch, and fallback text. |
| Rendering resources | Production chunk size, time to first usable block, memory after disposal, hidden animations, and WebGL-unavailable behavior. |
| Content contract | Legacy adaptation, unknown template handling, nested citation authorization, bounded data, and unsupported client versions. |
| Export | Default-state reproducibility, readable labels/fonts, source references, missing-renderer recovery, and continued PDF/LaTeX compatibility. |
| Access boundaries | Personal/shared material authorization, removed source access, asset endpoints, and preserved assessment feedback policies. |

Use real browser checks for graphics lifecycle and interaction. Pure string-output tests cannot establish that a WebGL scene disposes correctly or that a slider is usable on a small screen.

No runtime tests were required for writing this research document. The checks above describe future implementation verification; they are not claims that these integrations have already passed.

### 15.2 Decisions to refine next

- Is the first activity primarily a guided demonstration or a student construction exercise? This affects whether SVG/JSXGraph is enough or an editor becomes necessary.
- Which first topic has good source material and a clear deterministic model? Force components is the proposed starting point.
- Which export approach produces acceptable visual parity without unnecessary deployment complexity?
- Should chemistry begin with notation, 2D structures, or a rotatable reviewed molecule?
- Does circuit coverage need a few guided templates or a broad simulator?
- Which freely usable structure datasets and map assets should be bundled for the first examples?
- Should independently ready browser content and pending PDF export become a later explicit product change?

These questions do not block the documentation or a small technical prototype. They identify choices to settle as the plan is refined.

### 15.3 Record for each selected dependency

Before adding a library, record its exact release, upstream URL, license/notices, required workers/WASM/fonts, hosting behavior, production bundle measurement, tested browsers, disposal method, and export strategy.

Also record which scientific responsibilities remain in application code. A package upgrade should not silently change those assumptions or widen the supported content vocabulary.

All upstream links in this document are primary project/documentation/license sources researched on the date above. Terms, package contents, and APIs can change; verify the selected release and its no-fee deployment path when implementation begins. No paid visualization services or guaranteed delivery dates are assumed.
