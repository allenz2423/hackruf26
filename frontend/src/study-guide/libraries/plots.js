const GRAPH_MIN_X = -10;
const GRAPH_MAX_X = 10;
const RESPONSE_SAMPLE_COUNT = 60;
const RESPONSE_MAX_TIME_CONSTANTS = 5;
const RESPONSE_VALUE_LIMIT = 1000;
const RESPONSE_TAU_MIN = 0.01;
const RESPONSE_TAU_MAX = 3600;
const GRAPH_VALUE_LIMIT = 10;

let nextBoardId = 0;

function finiteNumber(value, fallback) {
  const number = Number(value);
  return Number.isFinite(number) ? number : fallback;
}

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

export function normalizeLinearGraphParameters({ slope = 1, intercept = 0 } = {}) {
  return {
    slope: clamp(finiteNumber(slope, 1), -GRAPH_VALUE_LIMIT, GRAPH_VALUE_LIMIT),
    intercept: clamp(finiteNumber(intercept, 0), -GRAPH_VALUE_LIMIT, GRAPH_VALUE_LIMIT)
  };
}

export function linearGraphValue(x, parameters = {}) {
  const { slope, intercept } = normalizeLinearGraphParameters(parameters);
  return slope * finiteNumber(x, 0) + intercept;
}

export function normalizeResponseParameters({ initial = 0, final = 10, tau = 1, time = 0 } = {}) {
  const initialValue = clamp(finiteNumber(initial, 0), -RESPONSE_VALUE_LIMIT, RESPONSE_VALUE_LIMIT);
  const finalValue = clamp(finiteNumber(final, 10), -RESPONSE_VALUE_LIMIT, RESPONSE_VALUE_LIMIT);
  const timeConstant = clamp(finiteNumber(tau, 1), RESPONSE_TAU_MIN, RESPONSE_TAU_MAX);
  return {
    initial: initialValue,
    final: finalValue,
    tau: timeConstant,
    time: clamp(finiteNumber(time, 0), 0, RESPONSE_MAX_TIME_CONSTANTS * timeConstant)
  };
}

export function responseValueAt(time, { initial, final, tau } = {}) {
  const values = normalizeResponseParameters({ initial, final, tau, time });
  return values.final + (values.initial - values.final) * Math.exp(-values.time / values.tau);
}

export function buildResponseSeries(parameters = {}) {
  const values = normalizeResponseParameters(parameters);
  const maxTime = RESPONSE_MAX_TIME_CONSTANTS * values.tau;
  const response = [];
  for (let index = 0; index <= RESPONSE_SAMPLE_COUNT; index += 1) {
    const elapsed = maxTime * index / RESPONSE_SAMPLE_COUNT;
    response.push([elapsed, values.final + (values.initial - values.final) * Math.exp(-elapsed / values.tau)]);
  }
  const current = [[values.time, values.final + (values.initial - values.final) * Math.exp(-values.time / values.tau)]];
  return { response, current, parameters: values };
}

export function buildResponsePlotOption(parameters = {}) {
  const { response, current, parameters: values } = buildResponseSeries(parameters);
  return {
    animation: false,
    aria: { enabled: true, decal: { show: false } },
    grid: { left: 56, right: 20, top: 24, bottom: 58, containLabel: true },
    tooltip: { trigger: "axis", confine: true },
    xAxis: {
      type: "value",
      min: 0,
      max: RESPONSE_MAX_TIME_CONSTANTS * values.tau,
      name: "Time (s)",
      nameLocation: "middle",
      nameGap: 34
    },
    yAxis: { type: "value", scale: true, name: "Response", nameLocation: "middle", nameGap: 42 },
    series: [
      {
        name: "First-order response",
        type: "line",
        data: response,
        showSymbol: false,
        silent: true,
        lineStyle: { width: 3, color: "#2563eb" }
      },
      {
        name: "Current value",
        type: "scatter",
        data: current,
        symbolSize: 10,
        itemStyle: { color: "#c2410c" }
      }
    ]
  };
}

function setHostStyle(host, height) {
  host.style.width = "100%";
  host.style.height = `${height}px`;
  host.style.minWidth = "0";
  host.style.boxSizing = "border-box";
  host.style.position = "relative";
  host.style.overflow = "hidden";
}

function makeMountHandle(container, initialize) {
  let disposed = false;
  let updateCurrent = () => {};
  let disposeCurrent = () => {};
  let pendingUpdate;
  const ready = (async () => {
    if (!container || typeof container.append !== "function" || !container.ownerDocument?.createElement) return false;
    try {
      const mounted = await initialize(container, () => disposed);
      if (disposed) {
        mounted?.dispose?.();
        return false;
      }
      if (!mounted) return false;
      updateCurrent = mounted.update || updateCurrent;
      disposeCurrent = mounted.dispose || disposeCurrent;
      if (pendingUpdate !== undefined) updateCurrent(pendingUpdate);
      return true;
    } catch {
      return false;
    }
  })();
  return {
    ready,
    update(parameters) {
      if (disposed) return;
      pendingUpdate = parameters;
      updateCurrent(parameters);
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      disposeCurrent();
    }
  };
}

export function mountLinearGraph(container, parameters = {}) {
  let current = normalizeLinearGraphParameters(parameters);
  let board = null;
  let curve = null;
  let host = null;
  let resizeObserver = null;

  return makeMountHandle(container, async (target, isDisposed) => {
    await import("../../../node_modules/jsxgraph/distrib/jsxgraph.css");
    const library = await import("jsxgraph");
    if (isDisposed()) return null;
    const api = library.JSXGraph || library.default?.JSXGraph || library.default;
    if (typeof api?.initBoard !== "function") return null;

    host = target.ownerDocument.createElement("div");
    host.id = `study-linear-graph-${++nextBoardId}`;
    host.className = "jxgbox";
    host.setAttribute("role", "img");
    host.setAttribute("aria-label", `Graph of y equals ${current.slope} x plus ${current.intercept}.`);
    setHostStyle(host, 260);
    target.append(host);

    try {
      board = api.initBoard(host.id, {
        boundingbox: [GRAPH_MIN_X, GRAPH_VALUE_LIMIT, GRAPH_MAX_X, -GRAPH_VALUE_LIMIT],
        axis: true,
        showNavigation: false,
        showCopyright: false,
        pan: { enabled: false },
        zoom: { enabled: false },
        keyboard: { enabled: false },
        renderer: "svg"
      });
      const curveStyle = { strokeColor: "#2563eb", strokeWidth: 3, fixed: true, highlight: false };
      const drawCurve = () => board.create(
        "functiongraph",
        [(x) => linearGraphValue(x, current), GRAPH_MIN_X, GRAPH_MAX_X],
        curveStyle
      );
      curve = drawCurve();
      board.update();
      const ResizeObserverImpl = target.ownerDocument.defaultView?.ResizeObserver;
      if (ResizeObserverImpl) {
        resizeObserver = new ResizeObserverImpl(() => {
          const width = host?.clientWidth || 0;
          const height = host?.clientHeight || 0;
          if (width > 0 && height > 0) {
            board?.resizeContainer?.(width, height, true, true);
            board?.update();
          }
        });
        resizeObserver.observe(host);
      }

      return {
        update(nextParameters = {}) {
          current = normalizeLinearGraphParameters(nextParameters);
          host?.setAttribute("aria-label", `Graph of y equals ${current.slope} x plus ${current.intercept}.`);
          if (!board) return;
          if (curve) board.removeObject(curve);
          curve = drawCurve();
          board.update();
        },
        resize() {
          const width = host?.clientWidth || 0;
          const height = host?.clientHeight || 0;
          if (width > 0 && height > 0) board?.resizeContainer?.(width, height, true, true);
          board?.update();
        },
        dispose() {
          resizeObserver?.disconnect();
          resizeObserver = null;
          if (board) api.freeBoard?.(board);
          board = null;
          curve = null;
          host?.remove();
          host = null;
        }
      };
    } catch {
      resizeObserver?.disconnect();
      resizeObserver = null;
      if (board) api.freeBoard?.(board);
      board = null;
      host.remove();
      host = null;
      return null;
    }
  });
}

export function mountResponsePlot(container, parameters = {}) {
  let current = normalizeResponseParameters(parameters);
  let chart = null;
  let host = null;
  let resizeObserver = null;

  return makeMountHandle(container, async (target, isDisposed) => {
    const [core, charts, components, renderers] = await Promise.all([
      import("echarts/core"),
      import("echarts/charts"),
      import("echarts/components"),
      import("echarts/renderers")
    ]);
    if (isDisposed()) return null;
    if (typeof core.init !== "function" || typeof core.use !== "function") return null;
    core.use([
      charts.LineChart,
      charts.ScatterChart,
      components.GridComponent,
      components.TooltipComponent,
      components.AriaComponent,
      renderers.SVGRenderer
    ]);

    host = target.ownerDocument.createElement("div");
    host.setAttribute("role", "img");
    host.setAttribute("aria-label", `First-order response starting at ${current.initial}, approaching ${current.final}, with time constant ${current.tau} seconds. Current time ${current.time} seconds.`);
    setHostStyle(host, 260);
    target.append(host);

    try {
      chart = core.init(host, null, { renderer: "svg" });
      chart.setOption(buildResponsePlotOption(current), { notMerge: true });
      const ResizeObserverImpl = target.ownerDocument.defaultView?.ResizeObserver;
      if (ResizeObserverImpl) {
        resizeObserver = new ResizeObserverImpl(() => chart?.resize());
        resizeObserver.observe(target);
      }
      return {
        update(nextParameters = {}) {
          current = normalizeResponseParameters(nextParameters);
          host?.setAttribute("aria-label", `First-order response starting at ${current.initial}, approaching ${current.final}, with time constant ${current.tau} seconds. Current time ${current.time} seconds.`);
          chart?.setOption(buildResponsePlotOption(current), { notMerge: true });
        },
        dispose() {
          resizeObserver?.disconnect();
          resizeObserver = null;
          chart?.dispose();
          chart = null;
          host?.remove();
          host = null;
        }
      };
    } catch {
      resizeObserver?.disconnect();
      resizeObserver = null;
      chart?.dispose();
      chart = null;
      host.remove();
      host = null;
      return null;
    }
  });
}
