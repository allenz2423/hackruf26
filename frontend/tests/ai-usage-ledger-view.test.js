import test from "node:test";
import assert from "node:assert/strict";

import {
  materialCostDetails,
  materialCostKindLabel,
  materialCostLabel,
  trackedBalanceInputValue
} from "../src/ai-usage-ledger-view.js";

test("unknown provider balance stays blank instead of inheriting another installation's snapshot", () => {
  assert.equal(trackedBalanceInputValue({ balance_known: false, balance_microusd: 63_860_000 }), "");
  assert.equal(trackedBalanceInputValue(null), "");
});

test("known tracked balance formats as a precise editable snapshot", () => {
  assert.equal(trackedBalanceInputValue({ balance_known: true, balance_microusd: 63_860_000 }), "63.860000");
  assert.equal(trackedBalanceInputValue({ balance_known: true, balance_microusd: "invalid" }), "");
});

test("material ledger never represents missing provider reports as a zero-cost generation", () => {
  const format = (microusd) => `$${(microusd / 1_000_000).toFixed(6)}`;
  assert.equal(materialCostLabel({ cost_status: "not_recorded" }, format), "Cost not recorded");
  assert.equal(materialCostLabel({ cost_status: "partial", known_cost_microusd: 0 }, format), "Total cost unknown");
  assert.equal(materialCostLabel({ cost_status: "partial", known_cost_microusd: 2400 }, format), "$0.002400 known · total unknown");
  assert.equal(materialCostLabel({ cost_status: "complete", known_cost_microusd: 0 }, format), "$0.000000 reported");
});

test("material ledger details show every recorded attempt and uncertainty", () => {
  assert.equal(
    materialCostDetails({ provider_calls: 4, unpriced_calls: 1, unsuccessful_calls: 2 }),
    "4 provider calls · 1 unpriced · 2 unsuccessful"
  );
  assert.equal(materialCostDetails({ provider_calls: 1 }), "1 provider call");
});

test("companion notes include their detail variant in the cost ledger label", () => {
  assert.equal(
    materialCostKindLabel({ kind: "companion_notes", detail_level: "detailed" }),
    "companion notes · detailed"
  );
});
