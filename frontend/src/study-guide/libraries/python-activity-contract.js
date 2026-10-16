const MAX_TITLE_LENGTH = 100;
const MAX_ALT_LENGTH = 500;
const MAX_VALUES = 16;
const MAX_ABS_VALUE = 100;
export const MAX_OUTPUT_LENGTH = 8_000;
const ALGORITHMS = new Set(["linear_search", "binary_search", "insertion_sort"]);

const PROGRAMS = Object.freeze({
  linear_search: `import json
data = json.loads(activity_input)
values = data["values"]
target = data["target"]
steps = []
found_index = None
for index, value in enumerate(values):
    steps.append(f"Check index {index}: {value}")
    if value == target:
        found_index = index
        steps.append(f"Found {target} at index {index}.")
        break
if found_index is None:
    steps.append(f"{target} is not in the list.")
activity_output = json.dumps({"steps": steps, "found_index": found_index})`,
  binary_search: `import json
data = json.loads(activity_input)
values = data["values"]
target = data["target"]
low = 0
high = len(values) - 1
steps = []
found_index = None
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
activity_output = json.dumps({"steps": steps, "found_index": found_index})`,
  insertion_sort: `import json
data = json.loads(activity_input)
values = data["values"][:]
steps = [f"Start: {values}"]
for index in range(1, len(values)):
    current = values[index]
    position = index
    while position > 0 and values[position - 1] > current:
        values[position] = values[position - 1]
        position -= 1
    values[position] = current
    steps.append(f"Insert {current}: {values}")
activity_output = json.dumps({"steps": steps, "sorted_values": values})`
});

const EXAMPLE_INPUTS = Object.freeze({
  linear_search: Object.freeze({ values: [4, 9, 2, 7, 5, 1], target: 7 }),
  binary_search: Object.freeze({ values: [1, 3, 5, 7, 9, 11, 13], target: 9 }),
  insertion_sort: Object.freeze({ values: [8, 3, 6, 1, 5] })
});

const TITLES = Object.freeze({
  linear_search: "Linear search",
  binary_search: "Binary search",
  insertion_sort: "Insertion sort"
});



function isRecord(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const prototype = Object.getPrototypeOf(value);
  return prototype === Object.prototype || prototype === null;
}

function textField(value, fallback, field, maxLength) {
  if (value === undefined || value === null || value === "") return fallback;
  if (typeof value !== "string") throw new TypeError(`${field} must be text.`);
  const text = value.trim();
  if (!text || text.length > maxLength || /[\u0000-\u001f\u007f-\u009f]/u.test(text)) {
    throw new RangeError(`${field} must contain 1 to ${maxLength} printable characters.`);
  }
  return text;
}

function normalizeValues(values) {
  if (!Array.isArray(values) || values.length < 2 || values.length > MAX_VALUES) {
    throw new RangeError(`Python algorithm examples need 2 to ${MAX_VALUES} integer values.`);
  }
  return values.map((value, index) => {
    if (!Number.isSafeInteger(value) || Math.abs(value) > MAX_ABS_VALUE) {
      throw new TypeError(`Value ${index + 1} must be an integer from -${MAX_ABS_VALUE} to ${MAX_ABS_VALUE}.`);
    }
    return value;
  });
}

/**
 * Normalize the only Python activity accepted by this adapter. The guide can
 * choose a reviewed algorithm and small integer input, but never supply code.
 */
export function normalizePythonAlgorithmBlock(block) {
  if (!isRecord(block)) throw new TypeError("Python algorithm activity must be an object.");
  if (block.template_id !== "python_algorithm_trace_v1") {
    throw new TypeError('Python activity template_id must be "python_algorithm_trace_v1".');
  }
  for (const forbidden of ["code", "source", "python", "snippet", "program"]) {
    if (Object.hasOwn(block, forbidden)) throw new TypeError("Python source code is not accepted in study-guide data.");
  }

  const parameters = block.parameters ?? {};
  if (!isRecord(parameters)) throw new TypeError("Python activity parameters must be an object.");
  for (const key of Object.keys(parameters)) {
    if (!["algorithm", "values", "target"].includes(key)) {
      throw new TypeError(`Unsupported Python activity parameter: ${key}.`);
    }
  }
  if (!ALGORITHMS.has(parameters.algorithm)) throw new TypeError("Choose a supported Python algorithm template.");

  const values = normalizeValues(parameters.values ?? EXAMPLE_INPUTS[parameters.algorithm].values);
  let target;
  if (parameters.algorithm === "linear_search" || parameters.algorithm === "binary_search") {
    const defaultTarget = EXAMPLE_INPUTS[parameters.algorithm].target;
    target = parameters.target === undefined ? defaultTarget : parameters.target;
    if (!Number.isSafeInteger(target) || Math.abs(target) > MAX_ABS_VALUE) {
      throw new TypeError(`Search target must be an integer from -${MAX_ABS_VALUE} to ${MAX_ABS_VALUE}.`);
    }
    if (parameters.algorithm === "binary_search" && values.some((value, index) => index > 0 && values[index - 1] > value)) {
      throw new TypeError("Binary search input values must be sorted in ascending order.");
    }
  } else if (parameters.target !== undefined && parameters.target !== null) {
    throw new TypeError("Insertion sort does not accept a search target.");
  }

  const normalizedParameters = { algorithm: parameters.algorithm, values };
  if (target !== undefined) normalizedParameters.target = target;
  return {
    template_id: "python_algorithm_trace_v1",
    title: textField(block.title, TITLES[parameters.algorithm], "Title", MAX_TITLE_LENGTH),
    alt_text: textField(block.alt_text, "A fixed Python algorithm example runs on a short list of integers.", "Alternative text", MAX_ALT_LENGTH),
    parameters: normalizedParameters
  };
}

/** Return only app-owned Python text; no guide field can affect executable source. */
export function getTrustedPythonProgram(algorithm) {
  if (!ALGORITHMS.has(algorithm)) throw new TypeError("Unsupported Python algorithm template.");
  return PROGRAMS[algorithm];
}

export function getPythonExampleCode(block) {
  const normalized = normalizePythonAlgorithmBlock(block);
  return getTrustedPythonProgram(normalized.parameters.algorithm);
}

export function normalizePythonTraceResult(result, parameters) {
  const algorithm = typeof parameters === "string" ? parameters : parameters?.algorithm;
  if (!isRecord(result) || !Array.isArray(result.steps) || result.steps.length > 128) {
    throw new TypeError("Python worker returned an invalid trace.");
  }
  const steps = result.steps.map((step) => textField(step, "", "Trace step", 240));
  const normalized = { steps };
  if (algorithm === "linear_search" || algorithm === "binary_search") {
    const values = typeof parameters === "string" ? null : parameters?.values;
    const target = typeof parameters === "string" ? undefined : parameters?.target;
    const count = Array.isArray(values) ? values.length : MAX_VALUES;
    if (result.found_index !== null && (!Number.isInteger(result.found_index) || result.found_index < 0 || result.found_index >= count)) {
      throw new TypeError("Python worker returned an invalid search index.");
    }
    if (Array.isArray(values) && Number.isInteger(target)) {
      let expectedIndex = null;
      if (algorithm === "linear_search") {
        const foundAt = values.findIndex((value) => value === target);
        expectedIndex = foundAt < 0 ? null : foundAt;
      } else {
        let low = 0;
        let high = values.length - 1;
        while (low <= high) {
          const middle = Math.floor((low + high) / 2);
          if (values[middle] === target) {
            expectedIndex = middle;
            break;
          }
          if (values[middle] < target) low = middle + 1;
          else high = middle - 1;
        }
      }
      if (expectedIndex !== result.found_index) {
        throw new TypeError("Python worker returned a search result inconsistent with its input.");
      }
    }
    normalized.found_index = result.found_index;
  } else {
    const sortedValues = normalizeValues(result.sorted_values);
    if (typeof parameters !== "string" && Array.isArray(parameters?.values)) {
      const expected = [...parameters.values].sort((left, right) => left - right);
      if (expected.length !== sortedValues.length || expected.some((value, index) => value !== sortedValues[index])) {
        throw new TypeError("Python worker returned a sort result inconsistent with its input.");
      }
    }
    normalized.sorted_values = sortedValues;
  }
  return normalized;
}
