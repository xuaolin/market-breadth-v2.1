export function fmt(value, digits = 1, suffix = "") {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
  return `${Number(value).toFixed(digits)}${suffix}`;
}

export function scoreClass(value) {
  if (value === null || value === undefined) return "neutral";
  const v = Number(value);
  if (v < 15) return "extreme-fear";
  if (v < 30) return "fear";
  if (v < 45) return "cautious";
  if (v < 65) return "neutral";
  if (v < 90) return "risk-on";
  return "complacent";
}

export function signedPct(value, digits = 2) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
  const n = Number(value);
  return `${n >= 0 ? "+" : ""}${n.toFixed(digits)}%`;
}

export function freshness(stale) {
  return stale ? "STALE DATA" : "CURRENT";
}
