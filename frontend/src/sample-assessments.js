const CREATED_AT = "2026-10-10T12:00:00.000Z";

function question(id, prompt, choices, answerIndex, explanation) {
  return { id, prompt, choices, answerIndex, explanation };
}

export const SAMPLE_ALGEBRA_MATERIALS = [
  {
    id: "sample-algebra-foundations-quiz",
    title: "Basic Algebra Foundations",
    type: "Quiz",
    subject: "Basic Algebra",
    scope: "Expressions and equations",
    difficulty: "Foundational",
    count: 6,
    status: "ready",
    createdAt: CREATED_AT,
    isSample: true,
    questions: [
      question("foundations-1", "Solve 3x + 5 = 20.", ["x = 3", "x = 5", "x = 7", "x = 15"], 1, "Subtract 5 from both sides to get 3x = 15, then divide by 3."),
      question("foundations-2", "Simplify 4a - 2 + 3a + 9.", ["7a + 7", "7a - 11", "a + 7", "12a + 7"], 0, "Combine like terms: 4a + 3a = 7a and -2 + 9 = 7."),
      question("foundations-3", "Solve 5x = -35.", ["x = -7", "x = 7", "x = -30", "x = 30"], 0, "Divide both sides by 5: x = -35 / 5 = -7."),
      question("foundations-4", "Evaluate 2a² - 3a when a = -2.", ["2", "8", "14", "-14"], 2, "Substitute -2: 2(-2)² - 3(-2) = 8 + 6 = 14."),
      question("foundations-5", "Simplify 2(x + 3) - 3(x - 1).", ["5x + 3", "9 - x", "-x + 3", "x + 9"], 1, "Distribute, then combine: 2x + 6 - 3x + 3 = 9 - x."),
      question("foundations-6", "Solve x/5 - 3 = 1.", ["x = 4", "x = 10", "x = 20", "x = 25"], 2, "Add 3 to get x/5 = 4, then multiply both sides by 5." )
    ]
  },
  {
    id: "sample-algebra-linear-equations-exam",
    title: "Linear Equations Practice Exam",
    type: "Practice test",
    subject: "Basic Algebra",
    scope: "One-variable linear equations",
    difficulty: "Foundational",
    count: 8,
    timeLimitMinutes: 10,
    status: "ready",
    createdAt: CREATED_AT,
    isSample: true,
    questions: [
      question("linear-1", "Solve 4(x - 2) = 2x + 10.", ["x = 5", "x = 7", "x = 9", "x = 11"], 2, "Distribute to get 4x - 8 = 2x + 10. Subtract 2x and add 8: 2x = 18, so x = 9."),
      question("linear-2", "Solve 5 - 2x = 17.", ["x = -11", "x = -6", "x = 6", "x = 11"], 1, "Subtract 5 to get -2x = 12, then divide by -2."),
      question("linear-3", "Solve (3/4)x + 2 = 11.", ["x = 9", "x = 12", "x = 13", "x = 16"], 1, "Subtract 2 to get (3/4)x = 9. Multiply by 4/3 to get x = 12."),
      question("linear-4", "Solve 2(3x + 1) - 4x = 10.", ["x = 2", "x = 3", "x = 4", "x = 5"], 2, "Distribute and combine: 6x + 2 - 4x = 10, so 2x = 8 and x = 4."),
      question("linear-5", "Solve 7x + 3 = 4x + 24.", ["x = 5", "x = 7", "x = 9", "x = 21"], 1, "Subtract 4x and 3: 3x = 21, so x = 7."),
      question("linear-6", "Solve 0.2x + 4 = 7.", ["x = 3", "x = 10", "x = 15", "x = 35"], 2, "Subtract 4 to get 0.2x = 3, then divide by 0.2."),
      question("linear-7", "Solve 3(x + 2) = 2(x + 7).", ["x = 4", "x = 6", "x = 8", "x = 10"], 2, "Distribute: 3x + 6 = 2x + 14. Subtract 2x and 6 to get x = 8."),
      question("linear-8", "Solve (x - 4)/3 = 5.", ["x = 11", "x = 15", "x = 19", "x = 23"], 2, "Multiply both sides by 3 to get x - 4 = 15, then add 4." )
    ]
  },
  {
    id: "sample-algebra-factoring-exam",
    title: "Expressions and Factoring Practice Exam",
    type: "Practice test",
    subject: "Basic Algebra",
    scope: "Exponents, factoring, and quadratics",
    difficulty: "Mixed",
    count: 8,
    timeLimitMinutes: 12,
    status: "ready",
    createdAt: CREATED_AT,
    isSample: true,
    questions: [
      question("factoring-1", "Factor x² + 7x + 12.", ["(x + 2)(x + 6)", "(x + 3)(x + 4)", "(x - 3)(x - 4)", "(x + 1)(x + 12)"], 1, "Find two numbers that multiply to 12 and add to 7: 3 and 4."),
      question("factoring-2", "Factor x² - 16.", ["(x - 8)(x + 2)", "(x - 4)²", "(x - 4)(x + 4)", "(x - 16)(x + 1)"], 2, "This is a difference of squares: x² - 4² = (x - 4)(x + 4)."),
      question("factoring-3", "Expand (x + 5)(x - 2).", ["x² + 7x - 10", "x² + 3x - 10", "x² - 3x + 10", "x² + 3x + 10"], 1, "Multiply the terms: x² - 2x + 5x - 10 = x² + 3x - 10."),
      question("factoring-4", "Solve x² - 5x + 6 = 0.", ["x = -2 or -3", "x = 2 or 3", "x = 1 or 6", "x = -1 or -6"], 1, "Factor as (x - 2)(x - 3) = 0, so x = 2 or x = 3."),
      question("factoring-5", "Factor 2x² + 7x + 3.", ["(2x + 1)(x + 3)", "(2x + 3)(x + 1)", "(2x - 1)(x - 3)", "(x + 1)(x + 3)"], 0, "Expanding (2x + 1)(x + 3) gives 2x² + 7x + 3."),
      question("factoring-6", "Solve (x - 4)(x + 1) = 0.", ["x = 4 or -1", "x = -4 or 1", "x = 4 only", "x = -1 only"], 0, "By the zero-product property, x - 4 = 0 or x + 1 = 0."),
      question("factoring-7", "Factor x² - 9x + 20.", ["(x - 2)(x - 10)", "(x + 4)(x + 5)", "(x - 4)(x - 5)", "(x + 2)(x + 10)"], 2, "The numbers -4 and -5 multiply to 20 and add to -9."),
      question("factoring-8", "Solve x² = 25.", ["x = 5 only", "x = -5 only", "x = 5 or -5", "x = 25 or -25"], 2, "Both 5² and (-5)² equal 25, so both values solve the equation." )
    ]
  }
];
