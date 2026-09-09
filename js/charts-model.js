/** Mutable chart runtime state (object so other modules can assign fields). */
export const chartState = {
  historyChart: null,
  chartCallbacks: {},
  activeHistory: null,
  activeRange: "5Y",
  pointerDown: null,
  indicatorFocus: null, // { key, label, scoreKey } | null
  seriesVisibility: {
    UMSI: true,
    Stress: false,
    Fragility: false,
    SPX: false,
  },
  hideLowQuality: false,
};

export const LOW_QUALITY_THRESHOLD = 0.85;

export const SERIES_META = {
  UMSI: { borderColor: "#9ee7ff", backgroundColor: "rgba(158,231,255,.03)", yKey: "umsi", yAxisID: "y" },
  Stress: { borderColor: "#ff8c42", backgroundColor: "rgba(255,140,66,.03)", yKey: "stress", yAxisID: "y" },
  Fragility: { borderColor: "#a55eea", backgroundColor: "rgba(165,94,234,.03)", yKey: "fragility", yAxisID: "y" },
  SPX: { borderColor: "rgba(120,180,140,.85)", backgroundColor: "rgba(120,180,140,.04)", yKey: "sp500", yAxisID: "y1" },
};

export const rangeDays = {
  "1Y": 365,
  "3Y": 365 * 3,
  "5Y": 365 * 5,
  "10Y": 365 * 10,
  "MAX": Infinity,
};

export function toTs(date) {
  return Date.parse(`${date}T00:00:00Z`);
}

export function filterRange(series, range) {
  if (!series?.length) return [];
  if (range === "MAX") return series;
  const days = rangeDays[range] || 365;
  const maxDate = toTs(series.at(-1).date);
  const minDate = maxDate - days * 86400000;
  return series.filter(p => toTs(p.date) >= minDate);
}


export const FACTOR_KEYS = [
  "term", "credit", "breadth", "vix", "put_call", "aaii", "drawdown",
  "factors", "indicators", "scores", "contributions",
];

function tsToDate(ts) {
  return new Date(ts).toISOString().slice(0, 10);
}

export function filterSeriesByWindow(series, minTs, maxTs) {
  if (!series?.length) return [];
  const lo = Math.min(minTs, maxTs);
  const hi = Math.max(minTs, maxTs);
  return series.filter((p) => {
    const t = toTs(p.date);
    return t >= lo && t <= hi;
  });
}

export function summarizeWindow(points) {
  const nums = (key) =>
    points.map((p) => p[key]).filter((v) => v != null && Number.isFinite(Number(v))).map(Number);

  function stats(arr) {
    if (!arr.length) return null;
    const sum = arr.reduce((a, b) => a + b, 0);
    return { avg: sum / arr.length, min: Math.min(...arr), max: Math.max(...arr), n: arr.length };
  }

  const umsi = stats(nums("umsi"));
  const stress = stats(nums("stress"));
  const fragility = stats(nums("fragility"));

  let spxChange = null;
  const withSpx = points.filter((p) => p.sp500 != null && Number.isFinite(Number(p.sp500)));
  if (withSpx.length >= 2) {
    const first = Number(withSpx[0].sp500);
    const last = Number(withSpx.at(-1).sp500);
    if (first) spxChange = ((last - first) / first) * 100;
  }

  return {
    count: points.length,
    start: points[0]?.date || null,
    end: points.at(-1)?.date || null,
    umsi,
    stress,
    fragility,
    spxChange,
  };
}

export function findNearestSeriesPoint(series, date) {
  if (!series?.length || !date) return null;
  const target = toTs(date);
  let best = series[0];
  let bestDist = Math.abs(toTs(best.date) - target);
  for (let i = 1; i < series.length; i++) {
    const d = Math.abs(toTs(series[i].date) - target);
    if (d < bestDist) {
      best = series[i];
      bestDist = d;
    }
  }
  return best;
}

export function extractFactorBreakdown(point) {
  if (!point) return [];
  const out = [];
  for (const key of FACTOR_KEYS) {
    const val = point[key];
    if (val == null) continue;
    if (typeof val === "number" && Number.isFinite(val)) {
      out.push({ key, label: key, value: val });
    } else if (typeof val === "object" && !Array.isArray(val)) {
      Object.entries(val).forEach(([k, v]) => {
        if (v == null) return;
        if (typeof v === "number" && Number.isFinite(v)) {
          out.push({ key: `${key}.${k}`, label: k, value: v });
        } else if (v && typeof v === "object" && (v.score != null || v.contribution != null)) {
          out.push({
            key: `${key}.${k}`,
            label: v.label || k,
            value: v.score ?? v.contribution,
            score: v.score,
            contribution: v.contribution,
            weight: v.weight,
          });
        }
      });
    }
  }
  Object.entries(point).forEach(([k, v]) => {
    if (["date", "umsi", "stress", "fragility", "sp500", "quality", "event"].includes(k)) return;
    if (FACTOR_KEYS.includes(k)) return;
    if (typeof v === "number" && Number.isFinite(v) && /(score|contrib|factor)/i.test(k)) {
      out.push({ key: k, label: k, value: v });
    }
  });
  return out;
}

/** Pull a numeric factor score for indicator key from a history point, if present. */
export function extractFactorScore(point, indicatorKey) {
  if (!point || !indicatorKey) return null;
  const key = String(indicatorKey);
  const candidates = [
    point[key],
    point[`${key}_score`],
    point.scores?.[key],
    point.factors?.[key],
    point.indicators?.[key],
  ];
  for (const c of candidates) {
    if (c == null) continue;
    if (typeof c === "number" && Number.isFinite(c)) return c;
    if (typeof c === "object") {
      const v = c.score ?? c.value;
      if (v != null && Number.isFinite(Number(v))) return Number(v);
    }
  }
  const breakdown = extractFactorBreakdown(point);
  const hit = breakdown.find(
    (f) =>
      f.key === key ||
      f.key.endsWith(`.${key}`) ||
      f.label === key ||
      String(f.label || "").toLowerCase().includes(key.replace(/_/g, " "))
  );
  return hit?.score ?? hit?.value ?? null;
}

/** True if enough history points expose per-factor scores for this indicator. */
export function seriesHasFactorScores(series, indicatorKey) {
  if (!series?.length || !indicatorKey) return false;
  let hits = 0;
  const sample = series.length > 80 ? series.filter((_, i) => i % Math.ceil(series.length / 80) === 0) : series;
  for (const p of sample) {
    if (extractFactorScore(p, indicatorKey) != null) hits += 1;
    if (hits >= 8) return true;
  }
  return false;
}

function quartileBounds(values) {
  const xs = values.filter((v) => v != null && Number.isFinite(Number(v))).map(Number).sort((a, b) => a - b);
  if (xs.length < 8) return null;
  const q = (p) => {
    const idx = (xs.length - 1) * p;
    const lo = Math.floor(idx);
    const hi = Math.ceil(idx);
    if (lo === hi) return xs[lo];
    return xs[lo] + (xs[hi] - xs[lo]) * (idx - lo);
  };
  return { q25: q(0.25), q75: q(0.75) };
}

export function getChartXBounds(chart) {
  const scale = chart.scales?.x;
  if (!scale) return null;
  return { min: scale.min, max: scale.max };
}

export function emitRangeChange(chart) {
  if (!chartState.activeHistory?.series?.length || typeof chartState.chartCallbacks.onRangeChange !== "function") return;
  const bounds = getChartXBounds(chart);
  if (!bounds) return;
  const windowed = filterSeriesByWindow(chartState.activeHistory.series, bounds.min, bounds.max);
  chartState.chartCallbacks.onRangeChange({
    minTs: bounds.min,
    maxTs: bounds.max,
    start: tsToDate(bounds.min),
    end: tsToDate(bounds.max),
    summary: summarizeWindow(windowed),
    points: windowed,
    preset: chartState.activeRange,
  });
}

export function emitVisibilityChange() {
  if (typeof chartState.chartCallbacks.onSeriesVisibilityChange === "function") {
    chartState.chartCallbacks.onSeriesVisibilityChange({ ...chartState.seriesVisibility });
  }
}

export function getSeriesVisibility() {
  return { ...chartState.seriesVisibility };
}

export function getHideLowQuality() {
  return chartState.hideLowQuality;
}

/**
 * Toggle filtering of low-quality UMSI points (quality < 0.85).
 * Re-renders the active chart range when history is available.
 */
export function setHideLowQuality(on) {
  chartState.hideLowQuality = Boolean(on);
  if (chartState.activeHistory) {
    renderHistoryChart(chartState.activeHistory, chartState.activeRange, chartState.chartCallbacks);
  }
  const chip = document.getElementById("hideLowQChip");
  if (chip) {
    chip.classList.toggle("active", chartState.hideLowQuality);
    chip.setAttribute("aria-pressed", chartState.hideLowQuality ? "true" : "false");
  }
  return chartState.hideLowQuality;
}

/**
 * Toggle or set visibility for UMSI / Stress / Fragility / SPX line datasets.
 * @param {string} name - "UMSI" | "Stress" | "Fragility" | "SPX"
 * @param {boolean} [visible] - if omitted, toggles
 */
export function setSeriesVisibility(name, visible) {
  if (!(name in chartState.seriesVisibility)) return chartState.seriesVisibility;
  chartState.seriesVisibility[name] = visible == null ? !chartState.seriesVisibility[name] : Boolean(visible);

  if (chartState.historyChart) {
    chartState.historyChart.data.datasets.forEach((ds) => {
      if (ds.label === name && ds._seriesKey) {
        ds.hidden = !chartState.seriesVisibility[name];
      }
    });
    // Show/hide right axis with SPX
    if (name === "SPX" && chartState.historyChart.options?.scales?.y1) {
      chartState.historyChart.options.scales.y1.display = chartState.seriesVisibility.SPX;
    }
    chartState.historyChart.update("none");
  }

  emitVisibilityChange();
  syncSeriesChips();
  return { ...chartState.seriesVisibility };
}

export function syncSeriesChips() {
  document.querySelectorAll("[data-series]").forEach((chip) => {
    const key = chip.dataset.series;
    const on = Boolean(chartState.seriesVisibility[key]);
    chip.classList.toggle("active", on);
    chip.setAttribute("aria-pressed", on ? "true" : "false");
  });
}

export function getHistoryChart() {
  return chartState.historyChart;
}

export function getIndicatorFocus() {
  return chartState.indicatorFocus ? { ...chartState.indicatorFocus } : null;
}

export function clearIndicatorFocus() {
  chartState.indicatorFocus = null;
  applyIndicatorFocusVisuals();
  updateFocusBanner(null);
  if (typeof chartState.chartCallbacks.onIndicatorFocusChange === "function") {
    chartState.chartCallbacks.onIndicatorFocusChange(null);
  }
}

/**
 * Emphasize an indicator on the history chart.
 * If per-factor history exists, bump extreme quartile days; else banner-only.
 * @returns {{ mode: "factor-extremes"|"banner", count?: number }|null}
 */
export function setIndicatorFocus(focus) {
  if (!focus?.key) {
    clearIndicatorFocus();
    return null;
  }
  // Toggle off if same key re-selected
  if (chartState.indicatorFocus?.key === focus.key) {
    clearIndicatorFocus();
    return null;
  }

  const series = chartState.activeHistory?.series || [];
  const hasFactors = seriesHasFactorScores(series, focus.key);
  chartState.indicatorFocus = {
    key: focus.key,
    label: focus.label || focus.key,
    score: focus.score,
    percentile: focus.percentile,
    status: focus.status,
    hasFactors,
  };

  let result = { mode: "banner" };
  if (hasFactors && chartState.historyChart) {
    const filtered = chartState.historyChart.data?.datasets?.find((d) => d._seriesKey === "UMSI")?.data || [];
    const scores = filtered.map((p) => extractFactorScore(p.raw || p, focus.key));
    const bounds = quartileBounds(scores);
    const extremePts = [];
    if (bounds) {
      filtered.forEach((p, i) => {
        const s = scores[i];
        if (s == null) return;
        if (s <= bounds.q25 || s >= bounds.q75) {
          extremePts.push({
            x: p.x,
            y: p.y,
            date: p.date,
            umsi: p.umsi,
            stress: p.stress,
            fragility: p.fragility,
            sp500: p.sp500,
            quality: p.quality,
            factorScore: s,
            raw: p.raw || p,
          });
        }
      });
    }
    applyIndicatorFocusVisuals(extremePts);
    result = { mode: "factor-extremes", count: extremePts.length };
  } else {
    applyIndicatorFocusVisuals([]);
  }

  updateFocusBanner(chartState.indicatorFocus);
  if (typeof chartState.chartCallbacks.onIndicatorFocusChange === "function") {
    chartState.chartCallbacks.onIndicatorFocusChange(chartState.indicatorFocus);
  }
  return result;
}

function applyIndicatorFocusVisuals(extremePts = []) {
  if (!chartState.historyChart) return;
  const dsIdx = chartState.historyChart.data.datasets.findIndex((d) => d._focusOverlay);
  if (dsIdx >= 0) chartState.historyChart.data.datasets.splice(dsIdx, 1);

  if (extremePts.length) {
    chartState.historyChart.data.datasets.push({
      type: "scatter",
      label: "Factor extremes",
      _focusOverlay: true,
      data: extremePts,
      pointRadius: 3.5,
      pointHoverRadius: 6,
      borderColor: "rgba(158,231,255,.9)",
      borderWidth: 1,
      backgroundColor: "rgba(245,196,81,.75)",
      order: 0,
    });
  }

  // Subtle pointRadius bump on UMSI for focused state (flash feel without fabricating)
  chartState.historyChart.data.datasets.forEach((ds) => {
    if (ds._seriesKey === "UMSI") {
      ds.pointRadius = chartState.indicatorFocus ? 1.2 : 0;
      ds.pointBackgroundColor = chartState.indicatorFocus ? "rgba(158,231,255,.35)" : undefined;
    }
  });

  chartState.historyChart.update("none");
}

export function updateFocusBanner(focus) {
  const el = document.getElementById("chartFocusBanner");
  if (!el) return;
  if (!focus) {
    el.hidden = true;
    el.innerHTML = "";
    return;
  }
  const bits = [];
  if (focus.score != null && Number.isFinite(Number(focus.score))) bits.push(`score ${Number(focus.score).toFixed(1)}`);
  if (focus.percentile != null && Number.isFinite(Number(focus.percentile))) bits.push(`%ile ${Number(focus.percentile).toFixed(1)}`);
  if (focus.status) bits.push(focus.status);
  const modeNote = focus.hasFactors
    ? " · yellow markers = historical factor extremes (Q1/Q4)"
    : " · no per-factor history in series; current daily snapshot only";
  el.hidden = false;
  el.innerHTML = `
    <div class="focus-banner-inner">
      <span class="focus-banner-label">Focus:</span>
      <span class="focus-banner-name">${focus.label}</span>
      <span class="focus-banner-meta">${bits.join(" · ")}${modeNote}</span>
      <button type="button" class="ghost-btn focus-clear-btn" id="clearIndicatorFocusBtn" aria-label="Clear indicator focus">Clear</button>
    </div>
  `;
  document.getElementById("clearIndicatorFocusBtn")?.addEventListener("click", () => clearIndicatorFocus());
}

export function resetHistoryZoom() {
  if (!chartState.historyChart) return;
  if (typeof chartState.historyChart.resetZoom === "function") chartState.historyChart.resetZoom();
  emitRangeChange(chartState.historyChart);
}

export function shortEventLabel(name) {
  return name
    .replace("2009 ", "")
    .replace("2018 ", "")
    .replace("2020 ", "")
    .replace("2022 ", "")
    .replace("2025 ", "")
    .replace("Bear Market Low", "Bear Low")
    .replace("Q4 Selloff", "Q4 Selloff");
}

export function buildLinePoints(filtered, yKey) {
  return filtered.map((p) => ({
    x: toTs(p.date),
    y: p[yKey],
    date: p.date,
    umsi: p.umsi,
    stress: p.stress,
    fragility: p.fragility,
    sp500: p.sp500,
    quality: p.quality,
    raw: p,
  }));
}

export function firePointDetail(rawPoint, history) {
  if (!rawPoint || typeof chartState.chartCallbacks.onEventClick !== "function") return;
  const date = rawPoint.date;
  const nearest = findNearestSeriesPoint(history.series, date);
  const factors = extractFactorBreakdown(nearest);
  const isNamedEvent = Boolean(rawPoint.event && rawPoint.event !== "UMSI snapshot");
  const event = isNamedEvent
    ? (rawPoint.raw || rawPoint)
    : {
        date,
        event: "UMSI snapshot",
        umsi: nearest?.umsi ?? rawPoint.umsi ?? rawPoint.y,
        stress: nearest?.stress ?? rawPoint.stress,
        fragility: nearest?.fragility ?? rawPoint.fragility,
      };
  chartState.chartCallbacks.onEventClick({ event, nearest, factors });
}

// Deferred import: setHideLowQuality re-renders via charts.js (circular, OK at call time).
import { renderHistoryChart } from "./charts.js?v=20260906e";
