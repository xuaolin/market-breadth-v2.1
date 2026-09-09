import { fmt, scoreClass, signedPct, freshness } from "./calculations.js?v=20260906e";
export async function getJSON(path) {
  const r = await fetch(`${path}?v=${Date.now()}`, { cache: "no-store" });
  if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`);
  return r.json();
}

export function setText(id, text) {
  const el = document.getElementById(id);
  if (el) el.textContent = text;
}

export function setScoreCard(id, value) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = value == null ? "—" : Number(value).toFixed(1);
  el.className = `metric-value ${scoreClass(value)}`;
}


export const UMSI_BINS = [
  [0, 15],
  [15, 30],
  [30, 45],
  [45, 65],
  [65, 80],
  [80, 90],
  [90, 101],
];

/** App state for brush-synced forward returns + indicator focus (shared across modules) */
export const appState = {
  fullHistory: null,
  lastRangePayload: null,
  syncForwardToWindow: false,
  activeDaily: null,
};

export function zoneIndex(v) {
  if (v == null || Number.isNaN(Number(v))) return null;
  const n = Number(v);
  for (let i = 0; i < UMSI_BINS.length; i++) {
    const [lo, hi] = UMSI_BINS[i];
    if (n >= lo && n < hi) return i;
  }
  return null;
}

export function regimeHeadline(daily) {
  return daily.umsi?.regime || "—";
}

export function regimeSubline(daily, history) {
  const parts = [];
  const dd = daily.market?.sp500?.drawdown_52w;
  if (dd != null && Number.isFinite(Number(dd))) {
    const v = Number(dd);
    parts.push(`52W ${v >= 0 ? "+" : ""}${v.toFixed(1)}%`);
  }
  const series = history?.series || [];
  const umsi = daily.umsi?.value;
  if (series.length > 5 && umsi != null) {
    const prev = series[series.length - 6]?.umsi;
    if (prev != null && Number.isFinite(Number(prev))) {
      const d = Number(umsi) - Number(prev);
      parts.push(`1W ${d >= 0 ? "+" : ""}${d.toFixed(1)}`);
    }
  }
  const pc = daily.indicators?.put_call?.status;
  if (pc) parts.push(`P/C ${pc}`);
  return parts.join(" · ") || "—";
}


/** P0: detect model degradation from missing factors / low calculation_quality */
export function detectDegradation(daily) {
  const indicators = daily?.indicators || {};
  const quality = Number(daily?.umsi?.calculation_quality);
  const anyScoreNull = Object.values(indicators).some(
    (item) => item && (item.score == null || Number.isNaN(Number(item.score)))
  );
  const breadth = indicators.breadth;
  const breadthUnavailable =
    !breadth ||
    breadth.score == null ||
    Number.isNaN(Number(breadth.score)) ||
    String(breadth.status || "").toLowerCase().includes("unavailable");
  const lowQuality = Number.isFinite(quality) && quality < 0.85;
  const degraded = Boolean(lowQuality || anyScoreNull || breadthUnavailable);
  return {
    degraded,
    lowQuality,
    anyScoreNull,
    breadthUnavailable,
    quality,
    qualityPct: Number.isFinite(quality) ? Math.round(quality * 100) : null,
  };
}

/**
 * P0 short-sample heuristic for put_call / aaii (no explicit sample size in daily.json).
 * True when:
 *  - status text matches unavailable|stale|bootstrap|thin|short|sparse|limited
 *  - score is null
 *  - sources text mentions bootstrap (put/call Cboe bootstrap)
 *  - methodology_note describes sparse put/call or AAII (percentiles over last N obs, not 5Y calendar)
 */
export function isShortSampleIndicator(key, item, daily) {
  if (key !== "put_call" && key !== "aaii") return false;
  const status = String(item?.status || "").toLowerCase();
  if (/unavailable|stale|bootstrap|thin|short|sparse|limited/.test(status)) return true;
  if (item?.score == null || Number.isNaN(Number(item.score))) return true;

  const sourcesBlob = (daily?.sources || [])
    .map((s) => `${s.name || ""} ${s.use || ""}`)
    .join(" ")
    .toLowerCase();
  if (key === "put_call" && /bootstrap/.test(sourcesBlob) && /put.?call|cboe/.test(sourcesBlob)) {
    return true;
  }

  const note = String(daily?.methodology_note || "").toLowerCase();
  if (/sparse|last n actual|not forward-filled|not long-run/.test(note)) {
    if (key === "put_call" && /put.?call/.test(note)) return true;
    if (key === "aaii" && /aaii/.test(note)) return true;
  }

  // source_date very recent-only vs generated_at (>14d gap is normal for weekly AAII;
  // flag only if source_date is missing while other dense indicators have dates)
  if (!item?.source_date) {
    const othersHaveDates = Object.entries(daily?.indicators || {}).some(
      ([k, v]) => k !== key && v?.source_date
    );
    if (othersHaveDates) return true;
  }

  return false;
}

export function formatQualityChip(q) {
  if (q == null || !Number.isFinite(Number(q))) return null;
  return `q ${Math.round(Number(q) * 100)}%`;
}

export function applyCredibilityUX(daily) {
  const deg = detectDegradation(daily);

  const badge = document.getElementById("degradeBadge");
  const umsiMetric = document.getElementById("umsiMetric");
  if (badge) {
    badge.hidden = !deg.degraded;
    if (deg.degraded) {
      const bits = [];
      if (deg.breadthUnavailable) bits.push("breadth missing");
      if (deg.anyScoreNull) bits.push("null factor score");
      if (deg.lowQuality) bits.push(`quality ${deg.qualityPct ?? "n/a"}%`);
      badge.title = `DEGRADED MODEL · 模型降级 — ${bits.join("; ") || "incomplete inputs"}`;
    }
  }
  if (umsiMetric) umsiMetric.classList.toggle("degraded", deg.degraded);

  // Stress / Fragility quality chips
  const stressQ = daily?.stress?.quality;
  const fragQ = daily?.fragility?.quality;
  const stressChip = document.getElementById("stressQuality");
  const fragChip = document.getElementById("fragilityQuality");
  if (stressChip) {
    const t = formatQualityChip(stressQ);
    stressChip.hidden = !t;
    stressChip.textContent = t || "";
    stressChip.classList.toggle("low", stressQ != null && Number(stressQ) < 0.85);
  }
  if (fragChip) {
    const t = formatQualityChip(fragQ);
    fragChip.hidden = !t;
    fragChip.textContent = t || "";
    fragChip.classList.toggle("low", fragQ != null && Number(fragQ) < 0.85);
  }

  // Fragility depends on breadth — subtle note when breadth degraded
  const fragStatus = document.getElementById("fragilityStatus");
  if (fragStatus) {
    const base = daily?.fragility?.status || "—";
    if (deg.breadthUnavailable) {
      fragStatus.innerHTML =
        `${base}<span class="fragility-breadth-note" title="Fragility includes narrow-breadth input">breadth input degraded — fragility less reliable</span>`;
    } else {
      fragStatus.textContent = base;
    }
  }

  // Methodology live note + short-sample line
  const live = document.getElementById("methodologyNoteLive");
  if (live && daily?.methodology_note) {
    live.hidden = false;
    live.textContent = daily.methodology_note;
  }

  const shortKeys = ["put_call", "aaii"].filter((k) =>
    isShortSampleIndicator(k, daily?.indicators?.[k], daily)
  );
  const shortNote = document.getElementById("shortSampleNote");
  if (shortNote) {
    if (shortKeys.length) {
      shortNote.hidden = false;
      const labels = shortKeys.map((k) =>
        k === "put_call" ? "Equity Put/Call" : "AAII Bull-Bear"
      );
      shortNote.textContent =
        `Short-sample note: ${labels.join(" and ")} percentiles are computed over the available observation history (sparse / bootstrap series), not a long-run 5-year calendar window. Treat ranks as provisional until the sample deepens.`;
    } else {
      shortNote.hidden = true;
      shortNote.textContent = "";
    }
  }

  return deg;
}

export function fwdFrom(series, i, days) {
  if (!series || i + days >= series.length) return null;
  const a = Number(series[i]?.sp500);
  const b = Number(series[i + days]?.sp500);
  if (!Number.isFinite(a) || !Number.isFinite(b) || a === 0) return null;
  return ((b / a) - 1) * 100;
}

export function stats(arr) {
  const xs = arr.filter((v) => v != null && Number.isFinite(Number(v))).map(Number);
  if (!xs.length) return { mean: null, std: null, hit: null };
  const mean = xs.reduce((s, v) => s + v, 0) / xs.length;
  const std = xs.length > 1
    ? Math.sqrt(xs.reduce((s, v) => s + (v - mean) ** 2, 0) / (xs.length - 1))
    : null;
  const hit = (xs.filter((v) => v > 0).length / xs.length) * 100;
  return { mean, std, hit };
}

export function computeForwardTable(series) {
  if (!series?.length) return { rows: [], baseline: {} };
  const horizons = [
    ["1m", 21],
    ["3m", 63],
    ["6m", 126],
    ["12m", 252],
  ];
  const baseline = {};
  horizons.forEach(([key, days]) => {
    baseline[key] = stats(series.map((_, i) => fwdFrom(series, i, days))).mean;
  });

  const entries = [];
  let i = 0;
  while (i < series.length) {
    const z = zoneIndex(series[i].umsi);
    if (z == null) {
      i += 1;
      continue;
    }
    let j = i + 1;
    while (j < series.length && zoneIndex(series[j].umsi) === z) j += 1;
    // A five-session stay is only knowable at the fifth close. Measuring from
    // day one would leak four future sessions into the backtest.
    if (j - i >= 5) entries.push({ index: i + 4, zone: z });
    i = j;
  }

  const rows = UMSI_BINS.map(([lo, hi], zi) => {
    const sample = entries.filter((e) => e.zone === zi);
    const row = {
      range: `${lo}–${hi === 101 ? 100 : hi}`,
      observations: sample.length,
      sampling: "confirmation_day_after_5_consecutive_sessions",
      small_sample: sample.length < 30,
    };
    horizons.forEach(([key, days]) => {
      const s = stats(sample.map((e) => fwdFrom(series, e.index, days)));
      const bh = baseline[key];
      row[`return_${key}`] = s.mean;
      row[`std_${key}`] = s.std;
      row[`hit_${key}`] = s.hit;
      row[`excess_${key}`] = s.mean != null && bh != null ? s.mean - bh : null;
    });
    return row;
  });
  return { rows, baseline };
}
