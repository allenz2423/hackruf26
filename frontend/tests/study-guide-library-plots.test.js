import test from "node:test";
import assert from "node:assert/strict";
import {
  buildResponsePlotOption,
  buildResponseSeries,
  linearGraphValue,
  mountLinearGraph,
  mountResponsePlot,
  normalizeLinearGraphParameters,
  normalizeResponseParameters,
  responseValueAt
} from "../src/study-guide/libraries/plots.js";

test("linear graph parameters are finite and bounded before plotting", () => {
  assert.deepEqual(normalizeLinearGraphParameters({ slope: 2.5, intercept: -3 }), {
    slope: 2.5,
    intercept: -3
  });
  assert.deepEqual(normalizeLinearGraphParameters({ slope: 99, intercept: -Infinity }), {
    slope: 10,
    intercept: 0
  });
  assert.equal(linearGraphValue(4, { slope: -2, intercept: 3 }), -5);
});

test("response samples reproduce the first-order response and current marker", () => {
  const { response, current, parameters } = buildResponseSeries({
    initial: 0,
    final: 10,
    tau: 2,
    time: 2
  });

  assert.equal(response.length, 61);
  assert.deepEqual(response[0], [0, 0]);
  assert.equal(response.at(-1)[0], 10);
  assert.ok(Math.abs(response[12][1] - (10 * (1 - Math.exp(-1)))) < 1e-12);
  assert.deepEqual(current, [[2, responseValueAt(2, { initial: 0, final: 10, tau: 2 })]]);
  assert.deepEqual(parameters, { initial: 0, final: 10, tau: 2, time: 2 });
});

test("response parameters bound invalid values and time to the plotted interval", () => {
  assert.deepEqual(normalizeResponseParameters({ initial: "bad", final: 5000, tau: 0, time: 99 }), {
    initial: 0,
    final: 1000,
    tau: 0.01,
    time: 0.05
  });
  assert.deepEqual(normalizeResponseParameters({ initial: -5000, final: -5, tau: 7200, time: -1 }), {
    initial: -1000,
    final: -5,
    tau: 3600,
    time: 0
  });
});

test("ECharts option builder returns a fixed, repeatable line-and-current-point chart", () => {
  const first = buildResponsePlotOption({ initial: 2, final: 8, tau: 4, time: 1 });
  const second = buildResponsePlotOption({ initial: 2, final: 8, tau: 4, time: 1 });

  assert.deepEqual(first, second);
  assert.equal(first.animation, false);
  assert.deepEqual(first.xAxis, {
    type: "value",
    min: 0,
    max: 20,
    name: "Time (s)",
    nameLocation: "middle",
    nameGap: 34
  });
  assert.equal(first.series[0].type, "line");
  assert.equal(first.series[0].data.length, 61);
  assert.equal(first.series[1].type, "scatter");
  assert.deepEqual(first.series[1].data, [[1, 8 + (2 - 8) * Math.exp(-0.25)]]);
  assert.deepEqual(Object.keys(first), ["animation", "aria", "grid", "tooltip", "xAxis", "yAxis", "series"]);
});

test("mount adapters safely report unavailable placeholders and have idempotent handles", async () => {
  for (const mount of [mountLinearGraph, mountResponsePlot]) {
    const handle = mount(null, {});
    assert.equal(await handle.ready, false);
    assert.doesNotThrow(() => handle.update({ slope: 2, intercept: 1, initial: 4, final: 8, tau: 2, time: 1 }));
    assert.doesNotThrow(() => handle.dispose());
    assert.doesNotThrow(() => handle.dispose());
  }
});
