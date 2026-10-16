/** Fixed synthetic teaching data, mirrored by backend guide_templates.py. */
export const SAMPLING_DEMO_POPULATION = Object.freeze([
  ...[10, 9, 8, 8, 7, 7, 6, 5, 4, 4, 3, 2].map((practiceProblems, index) => ({
    id: `A${String(index + 1).padStart(2, "0")}`,
    stratum: "Section A",
    practiceProblems,
    frontRow: true
  })),
  ...[5, 4, 4, 3, 3, 2, 2, 1, 1, 0, 0, 0].map((practiceProblems, index) => ({
    id: `B${String(index + 1).padStart(2, "0")}`,
    stratum: "Section B",
    practiceProblems,
    frontRow: false
  }))
]);

const METHOD_NAMES = Object.freeze({
  simple_random: "simple random sample",
  stratified: "proportional stratified sample",
  front_row_convenience: "front-row convenience sample"
});

function seededShuffle(items, seed) {
  const shuffled = [...items];
  let state = seed >>> 0;
  for (let index = shuffled.length - 1; index > 0; index -= 1) {
    state = (Math.imul(1664525, state) + 1013904223) >>> 0;
    const swapIndex = Math.floor((state * (index + 1)) / 0x1_0000_0000);
    [shuffled[index], shuffled[swapIndex]] = [shuffled[swapIndex], shuffled[index]];
  }
  return shuffled;
}

export function samplingDemoResult(method, sampleSize, seed, randomAssignment) {
  let selected;
  if (method === "simple_random") {
    selected = seededShuffle(SAMPLING_DEMO_POPULATION, seed).slice(0, sampleSize);
  } else if (method === "stratified") {
    const perStratum = sampleSize / 2;
    selected = [
      ...seededShuffle(SAMPLING_DEMO_POPULATION.filter((row) => row.stratum === "Section A"), seed)
        .slice(0, perStratum),
      ...seededShuffle(SAMPLING_DEMO_POPULATION.filter((row) => row.stratum === "Section B"), seed + 1_000_003)
        .slice(0, perStratum)
    ];
  } else if (method === "front_row_convenience") {
    selected = SAMPLING_DEMO_POPULATION.filter((row) => row.frontRow).slice(0, sampleSize);
  } else {
    throw new Error("Unknown sampling demonstration method.");
  }

  const assignment = {};
  if (randomAssignment) {
    const assignedOrder = seededShuffle(selected, seed ^ 0x9e3779b9);
    const midpoint = assignedOrder.length / 2;
    assignedOrder.forEach((row, index) => {
      assignment[row.id] = index < midpoint ? "Treatment A" : "Treatment B";
    });
  }
  const sampleTotal = selected.reduce((total, row) => total + row.practiceProblems, 0);
  const populationTotal = SAMPLING_DEMO_POPULATION.reduce((total, row) => total + row.practiceProblems, 0);
  return {
    selected,
    assignment,
    sampleMean: sampleTotal / selected.length,
    populationMean: populationTotal / SAMPLING_DEMO_POPULATION.length,
    stratumCounts: {
      "Section A": selected.filter((row) => row.stratum === "Section A").length,
      "Section B": selected.filter((row) => row.stratum === "Section B").length
    }
  };
}

export function samplingDemoNumber(value) {
  const rounded = Math.floor(value * 100 + 0.5) / 100;
  return rounded.toFixed(2).replace(/0+$/, "").replace(/\.$/, "");
}

export function samplingDemoFallback(method, sampleSize, seed, randomAssignment) {
  const result = samplingDemoResult(method, sampleSize, seed, randomAssignment);
  const selectedIds = result.selected.map((row) => row.id).join(", ");
  let text = `App-authored synthetic illustration, not source data: this fixed population is 24 fictional students and their practice-problem counts. The selected ${METHOD_NAMES[method]} contains ${sampleSize} students (${selectedIds}); its mean is ${samplingDemoNumber(result.sampleMean)} problems, compared with the fixed population mean of ${samplingDemoNumber(result.populationMean)}. The front-row convenience group is deliberately constructed with higher values to illustrate one possible bias, not a universal claim. Sampling chooses who enters a study; random assignment chooses which treatment sampled participants receive. This seeded pseudorandom draw is a repeatable teaching demonstration, not a production sampling method.`;
  if (randomAssignment) {
    const groupA = result.selected.filter((row) => result.assignment[row.id] === "Treatment A").map((row) => row.id).join(", ");
    const groupB = result.selected.filter((row) => result.assignment[row.id] === "Treatment B").map((row) => row.id).join(", ");
    text += ` Random assignment in this illustration places ${groupA} in Treatment A and ${groupB} in Treatment B.`;
  }
  return text;
}

export function samplingDemoAltText() {
  return "An app-authored synthetic population of 24 fictional students shows a selected sample, its mean practice-problem count, and optional randomized treatment groups. The population and values are illustrative, not course source data.";
}
