export const SAMPLE_VERSION = 4;

const now = "2026-10-10T15:00:00.000Z";

export const starterClasses = [
  {
    id: "math-151",
    name: "Math 151",
    color: "#3f6577",
    icon: "M",
    nextExam: "Midterm 1 on Oct 18",
    focus: "Derivatives, limits, and curve sketching",
    uploads: [
      {
        id: "math-syllabus",
        isSample: true,
        name: "Math 151 syllabus.pdf",
        kind: "Syllabus",
        size: 612000,
        status: "ready",
        progress: 100,
        createdAt: "2026-09-02T13:00:00.000Z",
        summary: "Weekly topics, office hours, grading weights, and two midterm windows.",
        sources: ["Weeks 1-8", "Exam schedule"],
        content: `MATH 151 | Calculus I\nFall term sample syllabus\n\nCourse focus\nLimits, continuity, derivatives, and applications of derivatives.\n\nWeekly topics\n1. Functions and limits\n2. Continuity and the Intermediate Value Theorem\n3. Derivative definition and rules\n4. Product, quotient, and chain rules\n5. Implicit differentiation and related rates\n6. Extrema, the Mean Value Theorem, and curve sketching\n\nAssessment\nMidterm 1: October 18, covering limits, derivative rules, and implicit differentiation.\nOffice hours: Tuesday and Thursday, 2:00-4:00 PM.\n\nStudy note\nBring one worked example and one question to each office-hour visit.`,
        outline: {
          topics: ["Limits and continuity", "Derivative rules", "Applications of derivatives", "Curve sketching"],
          exams: [{ name: "Midterm 1", date: "2026-10-18", topics: ["Limits", "Derivative rules", "Implicit differentiation"] }]
        }
      },
      {
        id: "math-week-4-notes",
        isSample: true,
        name: "Week 4 derivative rules.md",
        kind: "Notes",
        size: 18400,
        status: "ready",
        progress: 100,
        createdAt: "2026-09-26T16:30:00.000Z",
        summary: "Product, quotient, and chain rule examples with common sign mistakes.",
        sources: ["Page 2", "Examples 4-9"],
        content: `Week 4 | Derivative rules\n\nProduct rule\nIf y = f(x)g(x), then y' = f'(x)g(x) + f(x)g'(x).\nExample: d/dx [x^2 sin(x)] = 2x sin(x) + x^2 cos(x).\n\nQuotient rule\nIf y = f(x)/g(x), then y' = [f'(x)g(x) - f(x)g'(x)] / [g(x)]^2.\nKeep the denominator squared, and preserve the order in the numerator.\n\nChain rule\nFor a composition f(g(x)), differentiate the outside function first, then multiply by the derivative of the inside.\nExample: d/dx [sin(3x^2)] = cos(3x^2) * 6x = 6x cos(3x^2).\n\nCommon check\nBefore simplifying, identify the outer function, inner function, and the rule that connects them.`
      },
      {
        id: "math-lecture-5",
        isSample: true,
        name: "Lecture 5 - Chain rule.mp4",
        kind: "Lecture",
        size: 156200000,
        status: "ready",
        progress: 100,
        createdAt: "2026-10-08T21:15:00.000Z",
        summary: "Board walkthrough of nested functions and related rates setup.",
        duration: "48:12",
        transcript: [
          { at: "12:44", text: "Identify the outside function before differentiating the inside expression." },
          { at: "23:18", text: "For related rates, write the relationship before substituting numbers." }
        ],
        content: `Sample lecture | Chain rule\n\nKey idea\nA composite function is differentiated from the outside inward. For related rates, first write an equation connecting the changing quantities, then differentiate with respect to time.`
      }
    ],
    materials: [
      {
        id: "math-midterm-quiz",
        isSample: true,
        type: "Quiz",
        scope: "Midterm 1 review",
        difficulty: "Mixed",
        count: 3,
        status: "ready",
        score: "2/3 correct",
        createdAt: "2026-10-09T20:00:00.000Z",
        questions: [
          "Differentiate y = sin(3x^2).",
          "Find where f(x) = x^3 - 3x is increasing.",
          "Use the quotient rule on (x^2 + 1)/(x - 4)."
        ],
        items: [
          {
            prompt: "Differentiate y = sin(3x^2).",
            choices: ["3 cos(3x^2)", "6x cos(3x^2)", "6x sin(3x^2)", "cos(6x)"],
            answer: "6x cos(3x^2)",
            explanation: "Apply the chain rule: the derivative of sin(u) is cos(u), then multiply by u' = 6x."
          },
          {
            prompt: "Where is f(x) = x^3 - 3x increasing?",
            choices: ["-1 < x < 1", "x < -1 or x > 1", "All real x", "x > 0"],
            answer: "x < -1 or x > 1",
            explanation: "f'(x) = 3x^2 - 3 = 3(x - 1)(x + 1). It is positive outside the critical points -1 and 1."
          },
          {
            prompt: "What is the derivative of (x^2 + 1)/(x - 4)?",
            choices: ["(x^2 - 8x - 1)/(x - 4)^2", "(x^2 + 8x + 1)/(x - 4)^2", "2x/(x - 4)", "(2x - 1)/(x - 4)^2"],
            answer: "(x^2 - 8x - 1)/(x - 4)^2",
            explanation: "The quotient rule gives [2x(x - 4) - (x^2 + 1)]/(x - 4)^2, which simplifies to the listed result."
          }
        ],
        sources: ["Week 4 derivative rules.md", "Math 151 syllabus.pdf"]
      },
      {
        id: "math-chain-flashcards",
        isSample: true,
        type: "Flashcards",
        scope: "Chain rule",
        difficulty: "Easier",
        count: 3,
        status: "ready",
        score: "2 due",
        createdAt: "2026-10-08T12:10:00.000Z",
        questions: ["Outer function", "Inner function", "Related rate variables"],
        cards: [
          { front: "What is the chain rule?", back: "For f(g(x)), differentiate the outer function at the inner function, then multiply by the derivative of the inner function." },
          { front: "How do you identify the inner function in sin(3x^2)?", back: "The inner function is 3x^2; the outer function is sin(u)." },
          { front: "What should you do first in a related-rates problem?", back: "Write an equation relating the changing quantities before substituting numerical values." }
        ],
        sources: ["Lecture 5 - Chain rule.mp4"]
      }
    ],
    updatedAt: now
  },
  {
    id: "biology",
    name: "Biology",
    color: "#4f7340",
    icon: "B",
    nextExam: "Quiz Friday",
    focus: "Cell membranes and enzyme kinetics",
    uploads: [
      {
        id: "bio-slides",
        isSample: true,
        name: "Ch4 membranes.pptx",
        kind: "Slides",
        size: 4120000,
        status: "ready",
        progress: 100,
        createdAt: "2026-10-05T18:20:00.000Z",
        summary: "Membrane structure, transport types, and osmosis diagrams.",
        sources: ["Slides 8-22"],
        content: `Biology | Chapter 4: Cell membranes\nSample slide notes\n\nSlide 8 - The fluid mosaic model\nA cell membrane is a flexible phospholipid bilayer with proteins embedded in or attached to it.\n\nSlide 12 - Passive transport\nDiffusion and facilitated diffusion move substances down their concentration gradient without direct energy input.\n\nSlide 16 - Osmosis\nWater moves across a selectively permeable membrane toward the side with the higher solute concentration.\n\nSlide 20 - Active transport\nMembrane pumps use energy to move substances against a concentration gradient.\n\nReview prompt\nCompare facilitated diffusion with active transport: what moves, which direction, and where does the energy come from?`
      },
      {
        id: "bio-lab",
        isSample: true,
        name: "Enzyme lab notes.jpg",
        kind: "Notes",
        size: 2380000,
        status: "ready",
        progress: 100,
        createdAt: "2026-10-10T13:40:00.000Z",
        summary: "Handwritten notes on temperature, reaction rate, and enzyme activity.",
        sources: ["Image OCR"],
        content: `Enzyme lab notes | OCR sample\n\nQuestion: How does temperature affect enzyme activity?\nObservation: Reaction rate increased as temperature rose toward the enzyme's optimum. Above the optimum, the rate fell quickly.\n\nInterpretation: Higher temperature initially increases molecular collisions. Excessive heat can change the enzyme's shape and reduce the number of working active sites.\n\nLab variables\nIndependent: temperature\nDependent: reaction rate, measured by product formed per minute\nControl: keep pH and substrate concentration constant`
      }
    ],
    materials: [
      {
        id: "bio-guide",
        isSample: true,
        type: "Study guide",
        scope: "Membranes",
        difficulty: "Mixed",
        count: 3,
        status: "ready",
        score: "New",
        createdAt: "2026-10-09T11:45:00.000Z",
        questions: ["Passive vs active transport", "Osmosis examples", "Membrane protein roles"],
        content: `Chapter 4 study guide | Cell membranes\n\n1. Membrane structure\nThe phospholipid bilayer forms a flexible boundary. Hydrophilic heads face watery environments; hydrophobic tails face inward. Membrane proteins help transport substances and receive signals.\n\n2. Passive transport\nDiffusion moves particles down a concentration gradient. Facilitated diffusion uses a channel or carrier protein but does not directly use ATP.\n\n3. Osmosis\nWater crosses a selectively permeable membrane toward the solution with the higher solute concentration. Predict the direction by comparing solute levels on both sides.\n\n4. Active transport\nA pump uses energy, commonly ATP, to move a substance against its concentration gradient.\n\nCheck yourself\nExplain why a cell placed in a very concentrated salt solution loses water.`,
        items: [
          { prompt: "Which statement describes facilitated diffusion?", choices: ["It moves substances down a gradient through a membrane protein", "It moves substances against a gradient using ATP", "It moves only water", "It always breaks down the membrane"], answer: "It moves substances down a gradient through a membrane protein", explanation: "Facilitated diffusion is passive: it follows the concentration gradient while using a channel or carrier protein." },
          { prompt: "A cell is placed in a solution with a higher solute concentration than its cytoplasm. Which way does water move?", choices: ["Into the cell", "Out of the cell", "No net movement", "Water turns into solute"], answer: "Out of the cell", explanation: "Water moves toward the side with higher solute concentration, so it leaves the cell." },
          { prompt: "What does a membrane pump need to move a substance against its concentration gradient?", choices: ["ATP or another energy source", "Only a channel protein", "A lower temperature", "No energy"], answer: "ATP or another energy source", explanation: "Movement against a gradient requires energy; many pumps use ATP directly." }
        ],
        sources: ["Ch4 membranes.pptx"]
      }
    ],
    updatedAt: now
  },
  {
    id: "history-202",
    name: "History 202",
    color: "#7a4a72",
    icon: "H",
    nextExam: "Essay outline due Oct 14",
    focus: "Primary source analysis",
    uploads: [
      {
        id: "history-reader",
        isSample: true,
        name: "Reconstruction reader.pdf",
        kind: "Document",
        size: 8920000,
        status: "ready",
        progress: 100,
        createdAt: "2026-10-01T10:15:00.000Z",
        summary: "Primary source packet with speeches, newspaper excerpts, and commentary.",
        sources: ["Pages 14-38"],
        content: `History 202 | Reconstruction reader\nSample primary-source packet\n\nContext\nThe sources in this packet examine the political and social changes that followed the Civil War. Read each excerpt with attention to its author, intended audience, and historical moment.\n\nSource A - Congressional debate, 1866\nA representative argues that federal policy must define and protect the rights of formerly enslaved people.\n\nSource B - Newspaper editorial, 1870\nAn editor describes local reactions to new voting protections and disputes over who should enforce them.\n\nSource analysis checklist\n- Who created the source, and when?\n- What audience was it meant to reach?\n- What claim or point of view does it express?\n- What does it reveal, and what can it not tell us?\n\nEssay reminder\nUse a specific detail from a primary source as evidence, then explain how that evidence supports your claim.`
      }
    ],
    materials: [
      {
        id: "history-source-guide",
        isSample: true,
        type: "Study guide",
        scope: "Primary source analysis",
        difficulty: "Mixed",
        count: 3,
        status: "ready",
        score: "New",
        createdAt: "2026-10-08T15:30:00.000Z",
        questions: ["Who created the source?", "What is the author's claim?", "What evidence supports your interpretation?"],
        content: `Primary source analysis | Reconstruction\n\nStart with context\nIdentify who created the source, when it was created, and the audience it was meant to reach.\n\nRead for claims\nState the author's main claim in one sentence. Separate the author's evidence from your own interpretation.\n\nCheck perspective\nConsider the author's position, purpose, and limits. A source can be valuable evidence without representing every person's experience.\n\nBuild an argument\nUse a specific phrase or detail from the source, explain what it shows, and connect it to your historical claim.`,
        items: [
          { prompt: "Which question helps establish a source's context?", choices: ["Who created it, and when?", "How long is the source?", "Is the author's handwriting neat?", "Does it agree with a modern textbook?"], answer: "Who created it, and when?", explanation: "Authorship and date help situate the source and clarify its point of view." },
          { prompt: "A newspaper editorial argues that a new law is harmful. What is the best first step in analyzing this claim?", choices: ["Treat the editorial as neutral fact", "Identify the author's claim and intended audience", "Ignore the publication date", "Assume it represents every resident"], answer: "Identify the author's claim and intended audience", explanation: "Purpose and audience help explain why the author chose this argument." },
          { prompt: "How should you use a source detail in an essay?", choices: ["Quote it without explanation", "Use it as evidence and explain how it supports your claim", "Replace your claim with the quote", "Use only details that agree with your view"], answer: "Use it as evidence and explain how it supports your claim", explanation: "Historical analysis connects source evidence to a clearly stated interpretation." }
        ],
        sources: ["Reconstruction reader.pdf"]
      }
    ],
    updatedAt: now
  }
];
