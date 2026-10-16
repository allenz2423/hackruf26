import { escapeHtml } from "../ui.js";
import { displayMathHtml, mathHtml, typesetMath } from "../math.js";
import { mountAdditionalGuideBlocks, renderAdditionalGuideBlock } from "./activity-blocks.js";
import { renderStudyGuideText } from "./text-renderer.js";

let instanceCount = 0;

function cleanNumber(value, digits = 2) {
  return Number(value).toFixed(digits).replace(/\.?0+$/, "") || "0";
}

function components(force, angle) {
  const radians = angle * Math.PI / 180;
  return { x: force * Math.cos(radians), y: force * Math.sin(radians) };
}

function arrowMarker(id, color) {
  return `<marker id="${id}" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="${color}" /></marker>`;
}

function forceDiagram(id, force, angle) {
  const values = components(force, angle);
  const scale = 150 / Math.max(force, 1);
  const x0 = 42;
  const y0 = 188;
  const xTip = x0 + values.x * scale;
  const yTip = y0 - values.y * scale;
  const markers = {
    force: `${id}-force-arrow`,
    x: `${id}-x-arrow`,
    y: `${id}-y-arrow`
  };
  const label = `${cleanNumber(force)} newtons at ${cleanNumber(angle)} degrees; horizontal component ${cleanNumber(values.x)} newtons and vertical component ${cleanNumber(values.y)} newtons.`;
  return `<svg class="force-diagram" viewBox="0 0 360 230" role="img" aria-label="${escapeHtml(label)}">
    <defs>${arrowMarker(markers.force, "var(--force-vector)")}${arrowMarker(markers.x, "var(--force-x)")}${arrowMarker(markers.y, "var(--force-y)")}</defs>
    <line class="force-axis" x1="${x0}" y1="${y0}" x2="326" y2="${y0}" />
    <line class="force-axis" x1="${x0}" y1="${y0}" x2="${x0}" y2="22" />
    <line class="force-component-x" x1="${x0}" y1="${y0}" x2="${xTip}" y2="${y0}" marker-end="url(#${markers.x})" />
    <line class="force-component-y" x1="${xTip}" y1="${y0}" x2="${xTip}" y2="${yTip}" marker-end="url(#${markers.y})" />
    <line class="force-resultant" x1="${x0}" y1="${y0}" x2="${xTip}" y2="${yTip}" marker-end="url(#${markers.force})" />
    <text class="force-axis-label" x="330" y="${y0 + 5}">x</text><text class="force-axis-label" x="34" y="18">y</text>
    <text class="force-component-label force-x-label" x="${Math.max(x0 + 4, (x0 + xTip) / 2 - 12)}" y="${y0 + 22}">Fₓ</text>
    <text class="force-component-label force-y-label" x="${Math.min(xTip + 9, 300)}" y="${Math.max(30, (y0 + yTip) / 2)}">Fᵧ</text>
    <text class="force-component-label force-resultant-label" x="${Math.min(xTip + 8, 295)}" y="${Math.max(24, yTip - 9)}">F</text>
    <path class="force-angle-arc" d="M ${x0 + 32} ${y0} A 32 32 0 0 0 ${x0 + 32 * Math.cos(angle * Math.PI / 180)} ${y0 - 32 * Math.sin(angle * Math.PI / 180)}" />
    <text class="force-angle-label" x="${x0 + 39}" y="${y0 - 9}">${cleanNumber(angle)}°</text>
  </svg>`;
}

function forceGraph(id, force, angle) {
  const left = 48;
  const right = 330;
  const top = 22;
  const bottom = 166;
  const width = right - left;
  const height = bottom - top;
  const max = force || 1;
  const xAt = (degrees) => left + width * degrees / 90;
  const yAt = (value) => bottom - height * value / max;
  const xPoints = [];
  const yPoints = [];
  for (let degrees = 0; degrees <= 90; degrees += 3) {
    const value = components(force, degrees);
    xPoints.push(`${xAt(degrees).toFixed(2)},${yAt(value.x).toFixed(2)}`);
    yPoints.push(`${xAt(degrees).toFixed(2)},${yAt(value.y).toFixed(2)}`);
  }
  const current = components(force, angle);
  const marker = `${id}-graph-angle`;
  return `<svg class="force-graph" viewBox="0 0 360 205" role="img" aria-label="Graph of horizontal and vertical force components from zero to ninety degrees; current angle ${cleanNumber(angle)} degrees.">
    <line class="force-gridline" x1="${left}" y1="${top}" x2="${right}" y2="${top}" />
    <line class="force-gridline" x1="${left}" y1="${top + height / 2}" x2="${right}" y2="${top + height / 2}" />
    <line class="force-graph-axis" x1="${left}" y1="${bottom}" x2="${right}" y2="${bottom}" />
    <line class="force-graph-axis" x1="${left}" y1="${top}" x2="${left}" y2="${bottom}" />
    <text class="force-graph-tick" x="${left}" y="${bottom + 17}">0°</text><text class="force-graph-tick" x="${right - 15}" y="${bottom + 17}">90°</text>
    <text class="force-graph-tick" x="8" y="${top + 3}">${cleanNumber(force)} N</text><text class="force-graph-tick" x="8" y="${bottom + 3}">0 N</text>
    <polyline class="force-graph-x" points="${xPoints.join(" ")}" />
    <polyline class="force-graph-y" points="${yPoints.join(" ")}" />
    <line class="force-current-angle" x1="${xAt(angle)}" y1="${top}" x2="${xAt(angle)}" y2="${bottom}" />
    <circle class="force-graph-point-x" cx="${xAt(angle)}" cy="${yAt(current.x)}" r="4" />
    <circle class="force-graph-point-y" cx="${xAt(angle)}" cy="${yAt(current.y)}" r="4" />
    <text class="force-graph-label" x="${right - 24}" y="${top + 14}">Fₓ</text><text class="force-graph-label" x="${right - 24}" y="${bottom - 7}">Fᵧ</text>
    <title id="${marker}">Component values at ${cleanNumber(angle)} degrees</title>
  </svg>`;
}

function forceCard(block) {
  const instanceId = `force-model-${++instanceCount}`;
  const force = block.parameters.force_n;
  const angle = block.parameters.angle_deg;
  const values = components(force, angle);
  const title = mathHtml(block.title);
  const id = escapeHtml(instanceId);
  return `<section class="study-interactive-model force-components-model" data-force-model="${id}" data-default-force="${force}" data-default-angle="${angle}" aria-labelledby="${id}-title">
    <div class="force-model-heading"><div><span class="guide-meta-label">Interactive example</span><h4 id="${id}-title">${title}</h4></div><button class="btn btn-secondary btn-small" type="button" data-force-reset>Reset</button></div>
    <p class="force-model-intro">Explore how a force resolves along perpendicular axes. The angle is measured from the positive horizontal axis.</p>
    <div class="force-model-layout">
      <figure class="force-model-figure">
        <div data-force-diagram>${forceDiagram(instanceId, force, angle)}</div>
        <figcaption>${mathHtml(block.alt_text)}</figcaption>
      </figure>
      <div class="force-model-controls">
        <label class="force-control"><span>Force magnitude</span><span class="force-control-inputs"><input type="range" min="0" max="1000" step="0.1" value="${force}" data-force-value="force" aria-label="Force magnitude in newtons" aria-describedby="${id}-values"><input type="number" min="0" max="1000" step="0.1" value="${force}" data-force-value="force" aria-label="Force magnitude in newtons" aria-describedby="${id}-values"><span>N</span></span></label>
        <label class="force-control"><span>Angle from +x axis</span><span class="force-control-inputs"><input type="range" min="0" max="90" step="0.1" value="${angle}" data-force-value="angle" aria-label="Angle in degrees" aria-describedby="${id}-values"><input type="number" min="0" max="90" step="0.1" value="${angle}" data-force-value="angle" aria-label="Angle in degrees" aria-describedby="${id}-values"><span>°</span></span></label>
        <p class="force-equations">${mathHtml("$F_x=F\\cos\\theta$ ")}<br>${mathHtml("$F_y=F\\sin\\theta$")}</p>
      </div>
    </div>
    <p class="force-component-values" id="${id}-values" data-force-values>At ${cleanNumber(angle)}°: Fₓ = ${cleanNumber(values.x)} N; Fᵧ = ${cleanNumber(values.y)} N.</p>
    <div class="force-graph-panel"><h5>How the components change with angle</h5><div data-force-graph>${forceGraph(instanceId, force, angle)}</div><div class="force-graph-legend"><span class="legend-x">Fₓ horizontal component</span><span class="legend-y">Fᵧ vertical component</span></div></div>
    <fieldset class="force-prediction"><legend>Make a prediction</legend><label for="${id}-prediction">If the angle increases while the magnitude stays fixed, what happens?</label><div class="force-prediction-row"><select id="${id}-prediction" data-force-prediction><option value="">Choose a prediction</option><option value="x-down-y-up">Fₓ decreases and Fᵧ increases</option><option value="both-up">Both components increase</option><option value="x-up-y-down">Fₓ increases and Fᵧ decreases</option><option value="both-down">Both components decrease</option></select><button class="btn btn-secondary" type="button" data-force-reveal>Reveal</button></div><p class="force-prediction-feedback" data-force-feedback role="status" hidden></p></fieldset>
  </section>`;
}

export function renderStudyGuideBlock(block) {
  if (block.type === "paragraph") {
    return `<div class="guide-block-paragraph">${renderStudyGuideText(block.text)}</div>`;
  }
  if (block.type === "formula") {
    const definitions = block.definitions.length ? `<dl class="guide-formula-definitions">${block.definitions.map((item) => `<div><dt>${mathHtml(item.symbol)}</dt><dd>${mathHtml(item.meaning)}${item.unit ? ` <span>(${mathHtml(item.unit)})</span>` : ""}</dd></div>`).join("")}</dl>` : "";
    return `<div class="guide-formula-block"><div class="guide-formula-expression">${displayMathHtml(block.expression)}</div>${definitions}</div>`;
  }
  if (block.type === "takeaway") {
    return `<aside class="guide-takeaway"><strong>Key takeaway</strong>${renderStudyGuideText(block.text)}</aside>`;
  }
  if (block.type === "interactive_model" && block.template_id === "force_components_v1") {
    return forceCard(block);
  }
  const additionalBlock = renderAdditionalGuideBlock(block);
  if (additionalBlock) return additionalBlock;
  if (block.type === "unavailable") {
    return `<aside class="guide-activity-unavailable" role="note"><strong>Interactive visual unavailable</strong><p>${mathHtml(block.text)}</p></aside>`;
  }
  return `<p class="field-hint" role="note">${mathHtml(block.fallback_text || "This activity is unavailable. The saved explanation remains available above.")}</p>`;
}

export function mountStudyGuideBlocks(root) {
  root.querySelectorAll("[data-force-model]").forEach((card) => {
    const defaultForce = Number(card.dataset.defaultForce);
    const defaultAngle = Number(card.dataset.defaultAngle);
    const forceInputs = [...card.querySelectorAll('[data-force-value="force"]')];
    const angleInputs = [...card.querySelectorAll('[data-force-value="angle"]')];
    const clamp = (value, min, max) => Math.min(max, Math.max(min, value));

    function values() {
      const active = document.activeElement;
      if (active?.dataset?.forceValue === "force") {
        return { force: Number(active.value), angle: Number(angleInputs[1]?.value ?? defaultAngle) };
      }
      if (active?.dataset?.forceValue === "angle") {
        return { force: Number(forceInputs[1]?.value ?? defaultForce), angle: Number(active.value) };
      }
      return {
        force: Number(forceInputs[1]?.value ?? forceInputs[0]?.value ?? defaultForce),
        angle: Number(angleInputs[1]?.value ?? angleInputs[0]?.value ?? defaultAngle)
      };
    }

    function update() {
      const { force, angle } = values();
      if (!Number.isFinite(force) || !Number.isFinite(angle)) return;
      const currentForce = clamp(force, 0, 1000);
      const currentAngle = clamp(angle, 0, 90);
      forceInputs.forEach((input) => { if (document.activeElement !== input) input.value = String(currentForce); });
      angleInputs.forEach((input) => { if (document.activeElement !== input) input.value = String(currentAngle); });
      const result = components(currentForce, currentAngle);
      const valueLabel = card.querySelector("[data-force-values]");
      valueLabel.innerHTML = mathHtml(`At ${cleanNumber(currentAngle)}°: Fₓ = ${cleanNumber(result.x)} N; Fᵧ = ${cleanNumber(result.y)} N.`);
      void typesetMath(valueLabel);
      card.querySelector("[data-force-diagram]").innerHTML = forceDiagram(card.dataset.forceModel, currentForce, currentAngle);
      card.querySelector("[data-force-graph]").innerHTML = forceGraph(card.dataset.forceModel, currentForce, currentAngle);
    }

    [...forceInputs, ...angleInputs].forEach((input) => input.addEventListener("input", () => {
      if (input.value === "") return;
      update();
    }));
    [...forceInputs, ...angleInputs].forEach((input) => input.addEventListener("change", () => {
      const numeric = Number(input.value);
      if (!Number.isFinite(numeric)) {
        const pair = input.dataset.forceValue === "force" ? forceInputs : angleInputs;
        input.value = pair.find((item) => item !== input)?.value || "0";
      } else {
        input.value = String(clamp(numeric, Number(input.min), Number(input.max)));
      }
      update();
    }));
    card.querySelector("[data-force-reset]").addEventListener("click", () => {
      forceInputs.forEach((input) => { input.value = String(defaultForce); });
      angleInputs.forEach((input) => { input.value = String(defaultAngle); });
      const feedback = card.querySelector("[data-force-feedback]");
      feedback.hidden = true;
      feedback.textContent = "";
      card.querySelector("[data-force-prediction]").value = "";
      update();
    });
    card.querySelector("[data-force-reveal]").addEventListener("click", () => {
      const prediction = card.querySelector("[data-force-prediction]").value;
      const feedback = card.querySelector("[data-force-feedback]");
      feedback.hidden = false;
      feedback.innerHTML = mathHtml(prediction === "x-down-y-up"
        ? "That matches the model: with fixed magnitude in the first quadrant, Fₓ = F cos θ decreases while Fᵧ = F sin θ increases."
        : "With fixed magnitude in the first quadrant, Fₓ = F cos θ decreases while Fᵧ = F sin θ increases as the angle rises.");
      void typesetMath(feedback);
    });
  });
  mountAdditionalGuideBlocks(root);
}
