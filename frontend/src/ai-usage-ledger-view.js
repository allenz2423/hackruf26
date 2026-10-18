export function trackedBalanceInputValue(snapshot) {
  if (!snapshot?.balance_known) return "";
  const microusd = Number(snapshot.balance_microusd);
  if (!Number.isFinite(microusd) || microusd < 0) return "";
  return (microusd / 1_000_000).toFixed(6);
}

export function materialCostLabel(material, formatUsd) {
  if (!material || material.cost_status === "not_recorded") return "Cost not recorded";
  const cost = formatUsd(Number(material.known_cost_microusd) || 0);
  if (material.cost_status === "partial") {
    return Number(material.known_cost_microusd) > 0 ? `${cost} known · total unknown` : "Total cost unknown";
  }
  return `${cost} reported`;
}

export function materialCostDetails(material) {
  const calls = Number(material?.provider_calls) || 0;
  const details = [`${calls} provider ${calls === 1 ? "call" : "calls"}`];
  const unpriced = Number(material?.unpriced_calls) || 0;
  const unsuccessful = Number(material?.unsuccessful_calls) || 0;
  if (unpriced) details.push(`${unpriced} unpriced`);
  if (unsuccessful) details.push(`${unsuccessful} unsuccessful`);
  return details.join(" · ");
}

export function materialCostKindLabel(material) {
  if (material?.kind === "companion_notes") {
    const detail = material.detail_level ? ` · ${material.detail_level}` : "";
    return `companion notes${detail}`;
  }
  return String(material?.kind || "material").replaceAll("_", " ");
}
