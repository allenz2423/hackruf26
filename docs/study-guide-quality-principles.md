# Study-guide quality principles

This document turns learning-science and accessibility findings into practical authoring and interface rules for Jot. It is guidance for generated course materials, not a claim that a diagram or interaction by itself improves learning.

## Evidence to design rules

| Evidence | Design decision for Jot |
| --- | --- |
| Practice testing and distributed practice were rated high-utility techniques across varied learners and material in Dunlosky et al.'s review. [APS overview](https://www.psychologicalscience.org/publications/journals/pspi/learning-techniques.html) | Guides should make room for low-stakes recall and application, with a clear path to practice questions. Do not make rereading the only study action. |
| The spatial contiguity principle recommends placing explanatory words close to the picture elements they explain. [Mayer, *Multimedia Learning*](https://www.cambridge.org/core/books/abs/multimedia-learning/spatial-contiguity-principle/B9B79EDC777C375C7ED410B82EF80247) | Put a visual beside the explanation that interprets it. Label the relevant parts directly; do not make learners search a remote legend or a separate paragraph. |
| Coherence favors excluding extraneous material, and segmentation favors learner-paced chunks. [Mayer, coherence](https://www.cambridge.org/core/books/abs/multimedia-learning/coherence-principle/4E80B70CB76E2166B76E5653EBDE7D3E), [Mayer, segmenting](https://www.cambridge.org/core/books/abs/multimedia-learning/segmenting-principle/37240877DDA0362355ADB39936027982) | Keep each section about a small set of connected ideas. Prefer a few useful visuals and interactions over one decorative figure for every paragraph. Keep navigation and section boundaries visible in long guides. |
| Active learning was associated with higher performance in a meta-analysis of 225 undergraduate STEM studies; this evidence is about course-level active learning, not a guarantee for any specific widget. [Freeman et al., PNAS](https://doi.org/10.1073/pnas.1319030111) | Ask learners to predict, choose, manipulate, sequence, or explain something that reveals a concept. A play button or zoom control alone is not a learning interaction. |
| W3C cognitive-accessibility guidance recommends clear, concise content, short blocks, whitespace, and clear structure. [W3C: Clear Content](https://www.w3.org/WAI/WCAG2/supplemental/objectives/o3-clear-content/) | Use semantic subheadings and real lists; keep prose paragraphs focused and set a readable line length. The HTML guide and PDF must interpret the same authored structure. |
| The [CAST UDL Guidelines](https://udlguidelines.cast.org/) encourage multiple ways to perceive and act on information. | Keep text alternatives for visuals, make interactions keyboard-operable, and preserve a static explanation for print and reduced-motion users. |

## Jot authoring contract

1. Preserve the order and scope of the supplied course material. Distinguish central concepts, prerequisites, examples, and exceptions instead of writing a compressed list of facts.
2. Give each paragraph one main idea. For a detailed guide, use several short paragraphs rather than one block that fills a screen. Use `###` subheadings inside a section when it teaches distinct concepts; use ordered lists for procedures and bullets for comparisons or grouped facts.
3. Put the explanation near its formula, diagram, or activity. Name units, assumptions, conventions, and the question the visual answers.
4. Use animation only for a source-supported change over time. Keep fixed labels still, show a meaningful initial state, and make motion reveal one process at a time. A still diagram is preferable when time is not part of the concept.
5. Make interaction require a learning choice: predict an outcome, change a meaningful parameter, select a process stage, or inspect a trace. The widget must respond with interpretable evidence and have a textual alternative.
6. Blend selected styles as compatible emphases. `text_dense` means explanatory depth, not uninterrupted walls of text. `quick_reference` means a compact recap or decision rule, not removing the detailed explanation. `visual` means purposeful visual explanations, not filling every section with a picture. `interactive` means learner actions with feedback, not merely zoom or playback controls.
7. Never use a tool just to advertise a library. For topics outside a tool's reviewed domain, use a source-grounded custom SVG or clear text rather than a mismatched template.
8. Treat structure, citations, and renderability as necessary but insufficient quality checks. Inspect source support and rendered output separately; do not use the authoring model as the only judge of its own work.

## Findings from the 2026-10-17 BIO170 review

The 28-section Volume II draft showed why a cited source and a valid schema do not establish correctness. The uploaded master guide itself contains at least two claims that need correction or instructor review:

- Its ABO summary says Type O “lacks a terminal sugar.” The more precise distinction is that Type O red cells retain the unmodified H antigen and lack the A- or B-specific addition. See [NCBI Medical Genetics Summaries: ABO Blood Group](https://www.ncbi.nlm.nih.gov/books/NBK100894/).
- Its formula register writes the Arrhenius rate equation with `ΔG‡` while calling it activation energy. The Arrhenius equation uses `Eₐ`; Gibbs free energy of activation belongs to transition-state theory and uses a different prefactor. See the [IUPAC Gold Book: Arrhenius equation](https://goldbook.iupac.org/terms/view/A00446) and [IUPAC: activation energy](https://goldbook.iupac.org/terms/view/A00102).

These conflicts should be called out beside the course citation and confirmed with the instructor. Preserve the supplied course source as evidence of what was taught, but do not silently reproduce a demonstrably incorrect formula or overstate a source claim.

The rendered diagrams exposed two more issues that were invisible in schema checks: the meniscus markers did not sit on the actual curve extrema, and a transport flowchart's connector made an alternative branch look like the next sequential step. Figure review must check geometry and relationships, not just legibility. For every arrow, follow the route from start to finish and confirm that it joins the intended objects. Place markers exactly on the point, endpoint, intersection, or extremum they describe. Keep each SVG's viewBox close to the drawing bounds so empty vertical space does not dominate the phone screen.

Markdown pipe tables are not supported by the current guide reader or printable export. Until both renderers share semantic table support, comparisons must use labeled bullet lists; a deterministic generation check now rejects pipe-table syntax and requests a local repair.

Bare-math detection must preserve ordinary hyphenated prose. A scanner that accepts a trailing minus can italicize fragments such as “six-carbon” or “non-reducing” in both the reader and PDF. The renderer now rejects an equation candidate that ends with an operator and treats multiword hyphenated terms as prose unless the author explicitly delimits them. Formula blocks with a short definition list also stay together in the PDF so an equation is not orphaned from its variables.

## Baseline audit of saved BIO170 material

Read-only inspection of the saved local database on 2026-10-17 found three ready full-course BIO170 guides and one failed attempt. The ready guides have 16, 24, and 24 sections; their saved PDFs contain 5,211, 7,841, and 10,294 words across 17, 23, and 44 pages. None has a blank PDF page. The newest PDF is about 2.9 MB, consistent with embedded static figure snapshots. Its 24 sections contain 83 paragraph blocks, 24 takeaways, nine formulas, three worked examples, one selectable process diagram, and 27 original SVGs. Across all three full-course guides, all 69 custom SVGs are static and no guide has an `interactive_model` block. Most figures are relevant and recognizable, but text and captions are too small or dense in several phone-scale previews. The newest guide averages about 500 words per section; its paragraphs are already split into three or four blocks in most sections. The main remaining problems are legibility and meaningful interaction, not an across-the-board absence of paragraph breaks.

The 32-question mixed-format quiz was regenerated with `openai/gpt-6-luna` on 2026-10-17 at 07:56 UTC for a reported $0.012175. It has 14 multiple-choice, eight true/false, and ten short-answer items; all 32 cite the BIO170 source and the review found no empty or duplicate prompts. The 100-card deck has 2,789 front/back words, all cards cite the BIO170 source, and there are no exact duplicate fronts. However, it has no provider-operation record: although `generated_at` was updated to 2026-10-17, `created_at` remains 2026-10-14. Its regeneration and cost therefore cannot be confirmed from the ledger. One separate nine-section tech-demo guide contains ten `interactive_model` blocks, showing that the renderer can display activities; the full-course generator did not choose those activities.

Local guide contact sheets included one 360-pixel-wide render for each saved custom SVG and a thumbnail of every page in each saved study-guide PDF. These are structural and visual checks, not factual verification. The author's visual self-preview now renders at the same phone-card scale before accepting a diagram edit, instead of presenting only a large desktop-like raster to the author. The interactive-style prompt now explicitly requires four meaningful actions across a long guide when the sources support them, including at least two that change a value or select a stage and show a concept-specific result.

This audit describes stored structure, not factual correctness or what a student actually sees in a browser. A later iteration must render the saved content at desktop and phone sizes and compare the PDF to the browser presentation.

### Volume II and saved practice material checked on 2026-10-17

The newly generated Volume II guide has 28 sections and about 9,900 words. It contains seven custom SVGs, including one native-SMIL osmosis animation, six selectable process pathways, nine formulas, and four worked examples. The final PDF compiled in strict-math mode to 37 letter-size pages. Manual review caught and corrected the ABO/H-antigen wording, the timing description of galactose C-4 epimerization, the Arrhenius symbol conflict, the tonicity assumption, two meniscus marker coordinates, a clipped transport label, and a looping animation that made the illustrative water particle appear to reverse its net direction. The source-level conflicts remain labeled for instructor confirmation.

The saved 32-question quiz was inspected item by item; no factual or structural defect was found in that pass. The saved 100-card deck was also inspected. Eleven cards were corrected for overclaiming or inaccurate wording: the boundary between viruses and cellular life, selection versus population-level evolution, isotope definition, the Pauling electronegativity order, conventional polarity cutoffs, the conditions and scope of Anfinsen's experiment, pyrophosphate's role in nucleotide polymerization, canonical base-pair width, amylose helicity, amphipathic lipids, and sucrose's nonreducing mechanism. The source page for the electronegativity chart groups sulfur, carbon, hydrogen, and phosphorus too loosely; the corrected card uses Pauling values and flags that source simplification. See the [PubChem Pauling-scale table](https://pubchem.ncbi.nlm.nih.gov/periodic-table/electronegativity), [IUPAC Arrhenius definition](https://goldbook.iupac.org/terms/view/A00446), [NCBI ABO summary](https://www.ncbi.nlm.nih.gov/books/NBK100894/), and [OpenStax tonicity discussion](https://openstax.org/books/biology-2e/pages/5-2-passive-transport) when checking those discrepancies.

The repaired guide content and its PDF/LaTeX exports must be updated atomically as one material revision. The existing quiz and flashcards remain separate materials; a saved practice response or a generated visual is not evidence that the full study task is complete.

## Review checklist

- Does each section teach a bounded topic in the course's actual sequence?
- Are paragraphs broken by idea, with subheadings and lists rendered as structure rather than literal Markdown characters?
- Is each formula defined and used in context?
- Does each visual answer a specific question, and are its labels readable at the default display size?
- Does an animated visual depict a supported process, start in a useful still state, and remain understandable with reduced motion?
- Does each interaction ask the learner to make or inspect a meaningful choice and then explain the result?
- Can the student reach sections, sources, practice questions, and answers on a phone and with a keyboard?
- Do the browser and PDF preserve the same section order, formulas, diagrams, and textual alternatives?
- Are all factual claims supported by the cited course uploads, including claims embedded in SVG labels?
- Does any course statement conflict with a basic fact, another supplied source, or a standard equation; if so, is the discrepancy narrowly corrected and flagged for instructor confirmation?
- Does every diagram connector express the intended relationship, and are markers placed on the exact geometry they identify?
- Does each SVG use a compact viewBox without unnecessary empty vertical space?

These are review criteria, not automatic guarantees. A clean schema or export check does not prove the content is correct.
