import test from "node:test";
import assert from "node:assert/strict";
import { normalizeGuideBlocks } from "../src/study-guide/normalize.js";
import { mountAdditionalGuideBlocks, renderAdditionalGuideBlock } from "../src/study-guide/activity-blocks.js";

const section = { heading: "Chemistry", body: "Plain-text chemistry fallback.", sources: [] };

test("fixed molecule structures render deterministically with keyboard-accessible atoms", () => {
  const expected = {
    water: ["H₂O", "Oxygen is the central atom"],
    carbon_dioxide: ["CO₂", "Carbon is the central atom"],
    methane: ["CH₄", "Carbon is the central atom"],
    ammonia: ["NH₃", "Nitrogen is the central atom"]
  };

  for (const [moleculeId, [formula, atomDescription]] of Object.entries(expected)) {
    const input = {
      type: "labeled_diagram", template_id: "molecule_2d_v1",
      title: "Review this structure", parameters: { molecule_id: moleculeId },
      fallback_text: `Text fallback for ${moleculeId.replaceAll("_", " ")}.`
    };
    const [block] = normalizeGuideBlocks({ ...section, blocks: [input] });
    const html = renderAdditionalGuideBlock(block);
    const rerendered = renderAdditionalGuideBlock(block);

    assert.equal(block.template_id, "molecule_2d_v1");
    assert.match(html, new RegExp(`aria-label="[^"]*${formula}`));
    assert.match(html, /data-smiles-drawer/);
    assert.match(html, /data-molecule-fallback/);
    assert.match(html, /class="molecule-atom-controls"/);
    assert.match(html, /data-molecule-atom="[a-z0-9]+"[^>]*role="button" tabindex="0"/);
    assert.match(html, /aria-pressed="false"/);
    assert.match(html, /data-molecule-status role="status" aria-live="polite"/);
    assert.match(html, new RegExp(atomDescription));
    assert.match(html, new RegExp(`Text fallback for ${moleculeId.replaceAll("_", " ")}\\.`));
    // The instance IDs are intentionally unique; the app-authored content is stable.
    assert.equal(html.replace(/molecule-\d+/g, "molecule-ID"), rerendered.replace(/molecule-\d+/g, "molecule-ID"));
  }
});

test("process pathways expose each stage, announce selection, and escape authored text", () => {
  const [block] = normalizeGuideBlocks({
    ...section,
    blocks: [{
      type: "labeled_diagram", template_id: "process_pathway_v1",
      title: "<script>bad()</script>",
      fallback_text: "Plain-text path: evaporate, then condense.",
      parameters: { stages: [
        { id: "evaporation", title: "Evaporation", explanation: "Liquid becomes vapor." },
        { id: "condensation", title: "Condensation", explanation: "<script>unsafe()</script> vapor forms droplets." }
      ] }
    }]
  });
  const html = renderAdditionalGuideBlock(block);

  assert.doesNotMatch(html, /data-mermaid-pathway/);
  assert.match(html, /<ol class="pathway-stage-list" aria-label="Ordered process stages">/);
  assert.match(html, /<button class="pathway-stage-button" type="button" data-pathway-stage="evaporation"[^>]* aria-describedby="pathway-\d+-stage-explanation" aria-pressed="true"/);
  assert.match(html, /data-pathway-stage="condensation"[^>]*aria-pressed="false"/);
  assert.match(html, /data-stage-text="&lt;script&gt;unsafe\(\)&lt;\/script&gt; vapor forms droplets\."/);
  assert.match(html, /data-pathway-active-explanation>Liquid becomes vapor\.<\/p>/);
  assert.equal((html.match(/class="pathway-stage-explanation"/g) || []).length, 1);
  assert.match(html, /role="status" aria-live="polite"/);
  assert.match(html, /Plain-text path: evaporate, then condense\./);
  assert.doesNotMatch(html, /<script>unsafe\(\)<\/script>/);
  assert.match(html, /&lt;script&gt;unsafe\(\)&lt;\/script&gt;/);
});

class FakeElement {
  constructor(dataset = {}) {
    this.dataset = dataset;
    this.attributes = {};
    this.listeners = {};
    this.hidden = false;
    this.textContent = "";
    this.innerHTML = "";
  }

  addEventListener(type, callback) {
    this.listeners[type] = callback;
  }

  setAttribute(name, value) {
    this.attributes[name] = value;
  }

  querySelectorAll(selector) {
    return this.matches?.[selector] || [];
  }

  querySelector(selector) {
    return this.matches?.[selector] || null;
  }
}

test("molecule keyboard and pathway buttons update accessible status state", () => {
  const moleculeStatus = new FakeElement();
  const oxygen = new FakeElement({ moleculeAtom: "o" });
  const hydrogen = new FakeElement({ moleculeAtom: "h1" });
  const molecule = new FakeElement({ guideActivity: "molecule_2d_v1", moleculeId: "water" });
  molecule.matches = {
    "[data-molecule-status]": moleculeStatus,
    "[data-molecule-atom]": [oxygen, hydrogen]
  };
  mountAdditionalGuideBlocks({ querySelectorAll: () => [molecule] });

  let prevented = false;
  hydrogen.listeners.keydown({ key: "Enter", preventDefault: () => { prevented = true; } });
  assert.equal(prevented, true);
  assert.equal(hydrogen.attributes["aria-pressed"], "true");
  assert.equal(oxygen.attributes["aria-pressed"], "false");
  assert.equal(moleculeStatus.textContent, "H: This hydrogen is bonded to oxygen.");

  const firstButton = new FakeElement({ pathwayStage: "evaporation", stageNumber: "1", stageTitle: "Evaporation", stageText: "The liquid evaporates." });
  const secondButton = new FakeElement({ pathwayStage: "condensation", stageNumber: "2", stageTitle: "Condensation", stageText: "Vapor condenses." });
  const activeExplanation = new FakeElement();
  activeExplanation.id = "pathway-explanation";
  const pathwayStatus = new FakeElement();
  const pathway = new FakeElement({ guideActivity: "process_pathway_v1" });
  pathway.matches = {
    "[data-pathway-stage]": [firstButton, secondButton],
    "[data-pathway-status]": pathwayStatus,
    "[data-pathway-active-explanation]": activeExplanation
  };
  mountAdditionalGuideBlocks({ querySelectorAll: () => [pathway] });
  secondButton.listeners.click();

  assert.equal(firstButton.attributes["aria-pressed"], "false");
  assert.equal(secondButton.attributes["aria-pressed"], "true");
  assert.equal(secondButton.attributes["aria-describedby"], "pathway-explanation");
  assert.equal(activeExplanation.innerHTML, "Vapor condenses.");
  assert.equal(pathwayStatus.innerHTML, "Stage 2 of 2: Condensation. Vapor condenses.");
});
