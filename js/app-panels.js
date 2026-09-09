import { fmt, scoreClass, signedPct, freshness } from "./calculations.js?v=20260909a";
import { extractFactorBreakdown, findNearestSeriesPoint, getIndicatorFocus, resetHistoryZoom, setIndicatorFocus } from "./charts.js?v=20260909a";
import { appState, computeForwardTable, detectDegradation, isShortSampleIndicator, regimeSubline, setScoreCard, setText } from "./app-lib.js?v=20260909a";

export function renderTop(daily, intraday, history) {
  setScoreCard("umsiValue", daily.umsi?.value);
  setText("umsiRegime", daily.umsi?.regime || "—");

  setText("stressValue", fmt(daily.stress?.value, 1));
  setText("stressStatus", daily.stress?.status || "—");
  setText("fragilityValue", fmt(daily.fragility?.value, 1));
  setText("fragilityStatus", daily.fragility?.status || "—");

  const liveSP = intraday.sp500?.value ?? daily.market?.sp500?.value;
  const liveChg = intraday.sp500?.change_pct ?? daily.market?.sp500?.change_pct;
  setText("sp500Value", fmt(liveSP, 2));
  setText("sp500Change", signedPct(liveChg, 2));
  setText("sp500State", daily.market?.sp500?.status || "—");

  setText("regimeValue", regimeSubline(daily, history));
  setText("qualityValue", `${fmt((daily.umsi?.calculation_quality || 0) * 100, 0)}% inputs`);
  setText(
    "lastUpdated",
    daily.generated_at ? new Date(daily.generated_at).toLocaleString() : "Awaiting first update"
  );

  const stale =
    Object.values(daily.indicators || {}).some((x) => x.stale) ||
    Boolean(intraday.sp500?.stale);

  const badge = document.getElementById("freshnessBadge");
  if (badge) {
    badge.textContent = freshness(stale);
    badge.classList.toggle("stale", Boolean(stale));
  }

  renderDecision(daily);
}

export function renderDecision(daily) {
  const decision = daily.decision || {};
  setText("decisionState", decision.label || decision.state || "—");
  setText("decisionTrendScore", fmt(decision.trend_score, 1));
  setText(
    "decisionExposure",
    decision.research_exposure_score != null
      ? `Research exposure score ${fmt(decision.research_exposure_score, 0)}/100`
      : "Research score only"
  );
  setText("decisionNotice", decision.notice || decision.confirmation_rule || "Research only");

  const state = document.getElementById("decisionState");
  if (state) state.dataset.state = decision.state || "";

  const assets = document.getElementById("trendAssets");
  if (assets) {
    assets.innerHTML = Object.values(daily.market?.trend_assets || {}).map((item) => `
      <div class="trend-asset">
        <strong>${item.symbol || "—"}</strong>
        <span>Trend ${fmt(item.trend_score, 0)}</span>
        <span class="${item.above_ma200 ? "positive" : "negative"}">${item.above_ma200 ? "Above" : "Below"} MA200</span>
        <span>RS63 ${signedPct(item.relative_strength_63d, 1)}</span>
      </div>`).join("");
  }

  const reasons = document.getElementById("decisionReasons");
  if (reasons) {
    reasons.innerHTML = (decision.reasons || []).slice(0, 5).map((reason) => `<li>${reason}</li>`).join("");
  }
}

export function renderValidation(history) {
  const validation = history?.validation || {};
  const sim = validation.out_of_sample_simulation || {};
  const model = sim.model || {};
  const hold = sim.buy_and_hold || {};
  const summary = document.getElementById("validationSummary");
  if (summary) {
    const cards = [
      ["Model CAGR", signedPct(model.cagr, 1)],
      ["Model max drawdown", signedPct(model.max_drawdown, 1)],
      ["Model volatility", `${fmt(model.annualized_volatility, 1)}%`],
      ["Buy & hold CAGR", signedPct(hold.cagr, 1)],
      ["Buy & hold max drawdown", signedPct(hold.max_drawdown, 1)],
      ["Annual turnover", `${fmt(sim.annual_turnover, 1)}×`],
    ];
    summary.innerHTML = cards.map(([label, value]) => `
      <div class="validation-card"><span>${label}</span><strong>${value}</strong></div>`).join("");
  }

  const tbody = document.querySelector("#validationTable tbody");
  if (!tbody) return;
  tbody.innerHTML = "";
  (validation.out_of_sample?.forward_3m || []).forEach((row) => {
    const tr = document.createElement("tr");
    if ((row.observations || 0) < 30) tr.classList.add("small-sample");
    tr.innerHTML = `
      <td>${row.range}</td><td>${row.observations ?? 0}</td>
      <td>${signedPct(row.return_3m, 1)}</td><td>${signedPct(row.median_3m, 1)}</td>
      <td>${row.hit_3m == null ? "—" : `${fmt(row.hit_3m, 0)}%`}</td>
      <td>${signedPct(row.worst_3m, 1)}</td>`;
    tbody.appendChild(tr);
  });
}

export function formatRaw(key, item) {
  const v = item?.raw_value;
  if (v == null) return "—";

  if (key === "drawdown") return `${(Number(v) * 100).toFixed(2)}%`;
  if (key === "term") return Number(v).toFixed(3);
  if (key === "credit") return `${Number(v).toFixed(2)}%`;
  if (key === "vix") return Number(v).toFixed(2);
  if (key === "put_call") return Number(v).toFixed(2);
  if (key === "aaii") return Number(v).toFixed(1);
  if (key === "breadth") return Number(v).toFixed(4);
  return fmt(v, 4);
}

export function syncIndicatorRowHighlight() {
  const focus = getIndicatorFocus();
  document.querySelectorAll("#indicatorTable tbody tr.indicator-row").forEach((tr) => {
    const on = focus && tr.dataset.indicatorKey === focus.key;
    tr.classList.toggle("indicator-focused", Boolean(on));
    tr.setAttribute("aria-pressed", on ? "true" : "false");
  });
}

export function openIndicatorDayDetail(key, item) {
  if (!appState.fullHistory?.series?.length) return;
  const latest = appState.fullHistory.series.at(-1);
  const nearest = findNearestSeriesPoint(appState.fullHistory.series, latest?.date);
  const factors = extractFactorBreakdown(nearest);
  showEventDetail({
    event: {
      date: nearest?.date || latest?.date || "—",
      event: `Focus · ${item?.label || key}`,
      umsi: nearest?.umsi,
      stress: nearest?.stress,
      fragility: nearest?.fragility,
    },
    nearest,
    factors,
    history: appState.fullHistory,
    focusNote:
      item
        ? `Indicator ${item.label}: score ${fmt(item.score, 1)} · %ile ${fmt(item.percentile, 1)} · ${item.status || "—"} (from daily.json)`
        : null,
  });
}

export function renderIndicators(daily, degradation = null) {
  const tbody = document.querySelector("#indicatorTable tbody");
  tbody.innerHTML = "";
  appState.activeDaily = daily;
  const deg = degradation || detectDegradation(daily);

  Object.entries(daily.indicators || {}).forEach(([key, item]) => {
    const tr = document.createElement("tr");
    tr.className = "indicator-row";
    tr.tabIndex = 0;
    tr.setAttribute("role", "button");
    tr.dataset.indicatorKey = key;
    tr.setAttribute(
      "aria-label",
      `Focus chart on ${item.label}; score ${fmt(item.score, 1)}`
    );
    const source = item.source_date || "No source date";
    const stale = item.stale ? " · STALE" : "";
    const effectiveWeight =
      item.effective_weight != null
        ? ` title="Effective weight after re-normalization: ${(item.effective_weight * 100).toFixed(1)}%"`
        : "";

    const breadthDegraded =
      key === "breadth" &&
      (deg.breadthUnavailable ||
        item.score == null ||
        Number.isNaN(Number(item.score)) ||
        String(item.status || "").toLowerCase().includes("unavailable"));
    if (breadthDegraded) tr.classList.add("indicator-degraded");

    const shortSample = isShortSampleIndicator(key, item, daily);
    const nameTags =
      (breadthDegraded
        ? `<span class="degrade-pill" title="Breadth unavailable or null — model re-normalized">degraded</span>`
        : "") +
      (shortSample
        ? `<span class="short-sample-tag" title="Percentile not from long-run 5Y calendar window">short sample</span>`
        : "");

    tr.innerHTML = `
      <td>
        <div class="indicator-name">${item.label}${nameTags}</div>
        <div class="subtle">${source}${stale}</div>
      </td>
      <td>${formatRaw(key, item)}</td>
      <td>${fmt(item.percentile, 1, "%")}</td>
      <td><span class="score-pill ${scoreClass(item.score)}">${fmt(item.score, 1)}</span></td>
      <td${effectiveWeight}>${fmt((item.weight || 0) * 100, 0, "%")}</td>
      <td>${fmt(item.contribution, 2)}</td>
      <td>${item.status || "—"}</td>`;

    const activate = () => {
      const result = setIndicatorFocus({
        key,
        label: item.label || key,
        score: item.score,
        percentile: item.percentile,
        status: item.status,
      });
      syncIndicatorRowHighlight();
      if (result) {
        tr.classList.add("indicator-flash");
        setTimeout(() => tr.classList.remove("indicator-flash"), 450);
        openIndicatorDayDetail(key, item);
        document.getElementById("chartFocusBanner")?.scrollIntoView({
          behavior: "smooth",
          block: "nearest",
        });
      }
    };

    tr.addEventListener("click", activate);
    tr.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter" || ev.key === " ") {
        ev.preventDefault();
        activate();
      }
    });
    tbody.appendChild(tr);
  });
  syncIndicatorRowHighlight();
}

export function retCell(v) {
  if (v == null) return "—";
  const cls = Number(v) >= 0 ? "positive" : "negative";
  return `<span class="${cls}">${signedPct(v, 1)}</span>`;
}

export function paintForwardRows(rows, noteText) {
  const tbody = document.querySelector("#forwardTable tbody");
  tbody.innerHTML = "";
  const noteEl = document.getElementById("forwardRangeNote");
  if (noteEl) {
    noteEl.hidden = !noteText;
    if (noteText) noteEl.textContent = noteText;
  }
  rows.forEach((r) => {
    const tr = document.createElement("tr");
    if (r.small_sample || (r.observations != null && r.observations < 30)) {
      tr.classList.add("small-sample");
      tr.title = "Small sample (n < 30); treat as illustrative only";
    }
    const note = r.sampling ? " entries" : "";
    const cell = (mean, std, hit, excess) => {
      if (mean == null) return "—";
      const extra = [];
      if (std != null) extra.push(`σ ${Number(std).toFixed(1)}`);
      if (hit != null) extra.push(`${Number(hit).toFixed(0)}%+`);
      if (excess != null && Number.isFinite(Number(excess))) {
        extra.push(`xs ${Number(excess) >= 0 ? "+" : ""}${Number(excess).toFixed(1)}`);
      }
      return `${retCell(mean)}${extra.length ? `<div class="subtle">${extra.join(" · ")}</div>` : ""}`;
    };
    tr.innerHTML =
      `<td>${r.range}</td><td>${r.observations}${note}${r.small_sample ? " · n small" : ""}</td>` +
      `<td>${cell(r.return_1m, r.std_1m, r.hit_1m, r.excess_1m)}</td>` +
      `<td>${cell(r.return_3m, r.std_3m, r.hit_3m, r.excess_3m)}</td>` +
      `<td>${cell(r.return_6m, r.std_6m, r.hit_6m, r.excess_6m)}</td>` +
      `<td>${cell(r.return_12m, r.std_12m, r.hit_12m, r.excess_12m)}</td>`;
    tbody.appendChild(tr);
  });
}

export function fullSampleNote(computed) {
  const bh = computed.baseline || {};
  const bits = ["1M", "3M", "6M", "12M"].map((lab, i) => {
    const v = [bh["1m"], bh["3m"], bh["6m"], bh["12m"]][i];
    return v == null ? null : `${lab} BH ${v >= 0 ? "+" : ""}${v.toFixed(1)}%`;
  }).filter(Boolean);
  return (
    "Measured from the fifth confirming close after ≥5 sessions in zone vs buy-and-hold. n<30 greyed. Full-sample (not filtered by chart brush). " +
    (bits.length ? `Unconditional BH: ${bits.join(" · ")}.` : "")
  );
}

export function windowScopedNote(computed, payload) {
  const start = payload?.summary?.start || payload?.start || "—";
  const end = payload?.summary?.end || payload?.end || "—";
  const n = payload?.summary?.count ?? payload?.points?.length ?? "—";
  const bh = computed.baseline || {};
  const bits = ["1M", "3M", "6M", "12M"].map((lab, i) => {
    const v = [bh["1m"], bh["3m"], bh["6m"], bh["12m"]][i];
    return v == null ? null : `${lab} BH ${v >= 0 ? "+" : ""}${v.toFixed(1)}%`;
  }).filter(Boolean);
  return (
    `Window-scoped forward returns · ${start} → ${end} · ${n} obs. ` +
    "Measured from the fifth confirming close after ≥5 sessions in zone vs window buy-and-hold. n<30 greyed. " +
    (bits.length ? `Window BH: ${bits.join(" · ")}.` : "")
  );
}

export function renderForward(history, windowPoints = null) {
  const series =
    appState.syncForwardToWindow && windowPoints?.length
      ? windowPoints
      : history?.series || [];
  const computed = computeForwardTable(series);
  const rows =
    computed.rows.length
      ? computed.rows
      : !appState.syncForwardToWindow
        ? history?.forward_returns || []
        : [];
  let note;
  if (appState.syncForwardToWindow && windowPoints?.length) {
    note = windowScopedNote(computed, appState.lastRangePayload || { points: windowPoints });
  } else if (computed.rows.length) {
    note = fullSampleNote(computed);
  } else {
    note = null;
  }
  paintForwardRows(rows, note);
}

export function refreshForwardFromState() {
  if (!appState.fullHistory) return;
  if (appState.syncForwardToWindow && appState.lastRangePayload?.points?.length) {
    renderForward(appState.fullHistory, appState.lastRangePayload.points);
  } else {
    renderForward(appState.fullHistory, null);
  }
}

export function renderEvents(history) {
  const tbody = document.querySelector("#eventsTable tbody");
  tbody.innerHTML = "";
  (history.events || []).forEach((e) => {
    const tr = document.createElement("tr");
    tr.className = "event-row";
    tr.tabIndex = 0;
    tr.setAttribute("role", "button");
    tr.setAttribute("aria-label", `Show details for ${e.event} on ${e.date}`);
    tr.innerHTML =
      `<td>${e.date}</td><td>${e.event}</td><td>${fmt(e.umsi, 1)}</td>` +
      `<td>${fmt(e.stress, 1)}</td><td>${fmt(e.fragility, 1)}</td>` +
      `<td>${retCell(e.return_1m)}</td><td>${retCell(e.return_3m)}</td>` +
      `<td>${retCell(e.return_6m)}</td>`;
    const open = () => showEventDetail({ event: e, history });
    tr.addEventListener("click", open);
    tr.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter" || ev.key === " ") {
        ev.preventDefault();
        open();
      }
    });
    tbody.appendChild(tr);
  });
}

export function renderSources(daily) {
  const list = document.getElementById("sourceList");
  list.innerHTML = "";
  (daily.sources || []).forEach((s) => {
    const a = document.createElement("a");
    a.href = s.url;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    a.textContent = `${s.name} — ${s.use}`;
    list.appendChild(a);
  });
}

export function statTriple(s) {
  if (!s) return "—";
  return `avg ${fmt(s.avg, 1)} · min ${fmt(s.min, 1)} · max ${fmt(s.max, 1)}`;
}

export function updateRangeSummary(payload) {
  const el = document.getElementById("rangeSummary");
  if (!el || !payload?.summary) return;
  const s = payload.summary;
  appState.lastRangePayload = payload;

  el.hidden = false;
  el.innerHTML = `
    <div class="range-summary-head">
      <div>
        <div class="range-summary-title">Selected window</div>
        <div class="range-summary-dates">${s.start || "—"} → ${s.end || "—"} · ${s.count} obs · preset ${payload.preset || "—"}</div>
      </div>
      <button type="button" id="resetZoomBtn" class="ghost-btn" title="Reset brush / zoom to preset range">Reset zoom</button>
    </div>
    <div class="range-summary-grid">
      <div><span class="k">UMSI</span><span class="v">${statTriple(s.umsi)}</span></div>
      <div><span class="k">Stress</span><span class="v">${statTriple(s.stress)}</span></div>
      <div><span class="k">Fragility</span><span class="v">${statTriple(s.fragility)}</span></div>
      <div><span class="k">SPX Δ</span><span class="v ${s.spxChange == null ? "" : s.spxChange >= 0 ? "positive" : "negative"}">${s.spxChange == null ? "—" : signedPct(s.spxChange, 2)}</span></div>
    </div>
  `;

  const resetBtn = document.getElementById("resetZoomBtn");
  if (resetBtn) resetBtn.addEventListener("click", () => resetHistoryZoom());

  if (appState.syncForwardToWindow) {
    refreshForwardFromState();
  } else {
    const note = document.getElementById("forwardRangeNote");
    if (note && note.hidden) {
      note.hidden = false;
      note.textContent =
        "Forward-return table remains full-sample (not filtered by chart brush). Enable “Sync to chart window” to filter.";
  }
  }
}

export function showEventDetail({ event, nearest, factors, history, focusNote }) {
  const panel = document.getElementById("eventDetail");
  if (!panel || !event) return;

  const series = history?.series || [];
  const point = nearest || findNearestSeriesPoint(series, event.date);
  const factorList = factors || extractFactorBreakdown(point);
  const isSnapshot = !event.event || event.event === "UMSI snapshot" || String(event.event).startsWith("Focus ·");

  const core = [
    ["UMSI", event.umsi ?? point?.umsi],
    ["Stress", event.stress ?? point?.stress],
    ["Fragility", event.fragility ?? point?.fragility],
    ["S&P 500", point?.sp500],
    ["Model quality", point?.quality != null ? `${Math.round(point.quality * 100)}%` : null],
  ];

  const returns = [
    ["1M fwd", event.return_1m],
    ["3M fwd", event.return_3m],
    ["6M fwd", event.return_6m],
  ];

  const factorHtml =
    factorList.length > 0
      ? factorList
          .map(
            (f) =>
              `<div class="event-factor"><span>${f.label}</span><span>${fmt(f.value, 1)}${
                f.contribution != null ? ` · contrib ${fmt(f.contribution, 2)}` : ""
              }</span></div>`
          )
          .join("")
      : `<div class="event-factor muted">No per-factor scores in history.json for this date. Showing series / event fields only.</div>`;

  const returnsBlock = isSnapshot && !event.return_1m
    ? ""
    : `
      <div class="event-detail-section">Forward returns</div>
      <div class="event-detail-grid compact">
        ${returns
          .map(
            ([k, v]) =>
              `<div><span class="k">${k}</span><span class="v">${v == null ? "—" : signedPct(v, 1)}</span></div>`
          )
          .join("")}
      </div>`;

  const focusBlock = focusNote
    ? `<div class="event-detail-section">Indicator focus</div><div class="event-factor muted">${focusNote}</div>`
    : "";

  panel.hidden = false;
  panel.setAttribute("aria-hidden", "false");
  panel.innerHTML = `
    <div class="event-detail-card" role="dialog" aria-labelledby="eventDetailTitle">
      <div class="event-detail-head">
        <div>
          <div id="eventDetailTitle" class="event-detail-title${isSnapshot ? " snapshot" : ""}">${
            event.event || "UMSI snapshot"
          }</div>
          <div class="event-detail-sub">${event.date}${
            point?.date && point.date !== event.date ? ` · nearest series ${point.date}` : ""
          }</div>
        </div>
        <button type="button" class="ghost-btn" id="closeEventDetail" aria-label="Close event detail">Close</button>
      </div>
      <div class="event-detail-grid">
        ${core
          .map(
            ([k, v]) =>
              `<div><span class="k">${k}</span><span class="v">${typeof v === "number" ? fmt(v, 1) : v ?? "—"}</span></div>`
          )
          .join("")}
      </div>
      ${focusBlock}
      ${returnsBlock}
      <div class="event-detail-section">Factor breakdown</div>
      <div class="event-factors">${factorHtml}</div>
    </div>
  `;

  const close = () => {
    panel.hidden = true;
    panel.setAttribute("aria-hidden", "true");
    panel.innerHTML = "";
  };
  document.getElementById("closeEventDetail")?.addEventListener("click", close);
  panel.querySelector(".event-detail-card")?.focus?.();
}
