import * as Mod from "./charts-model.js?v=20260906e";
import { chartState, LOW_QUALITY_THRESHOLD, updateFocusBanner } from "./charts-model.js?v=20260906e";
export {
  chartState,
  clearIndicatorFocus,
  extractFactorBreakdown,
  filterSeriesByWindow,
  findNearestSeriesPoint,
  getHideLowQuality,
  getHistoryChart,
  getIndicatorFocus,
  getSeriesVisibility,
  resetHistoryZoom,
  setHideLowQuality,
  setIndicatorFocus,
  setSeriesVisibility,
  summarizeWindow,
} from "./charts-model.js?v=20260906e";

export function renderHistoryChart(history, range = "5Y", callbacks = {}) {
  const canvas = document.getElementById("historyChart");
  if (!canvas || !history?.series?.length) return;

  chartState.chartCallbacks = callbacks || {};
  chartState.activeHistory = history;
  chartState.activeRange = range;

  const filtered = Mod.filterRange(history.series, range);
  if (!filtered.length) return;

  const filteredStart = Mod.toTs(filtered[0].date);
  const filteredEnd = Mod.toTs(filtered.at(-1).date);
  const visibleEvents = (history.events || []).filter(e => {
    const x = Mod.toTs(e.date);
    return x >= filteredStart && x <= filteredEnd;
  });

  let umsiData = Mod.buildLinePoints(filtered, "umsi");
  const stressData = Mod.buildLinePoints(filtered, "stress");
  const fragilityData = Mod.buildLinePoints(filtered, "fragility");
  const spxData = Mod.buildLinePoints(filtered, "sp500");

  // P0: optionally filter low-quality UMSI points (Hide low-q chip)
  if (chartState.hideLowQuality) {
    umsiData = umsiData.filter(
      (p) => p.quality == null || !Number.isFinite(Number(p.quality)) || Number(p.quality) >= LOW_QUALITY_THRESHOLD
    );
  }

  const eventPoints = visibleEvents.map(e => ({
    x: Mod.toTs(e.date),
    y: e.umsi,
    date: e.date,
    event: e.event,
    stress: e.stress,
    fragility: e.fragility,
    return_1m: e.return_1m,
    return_3m: e.return_3m,
    return_6m: e.return_6m,
    raw: e,
  }));

  const latest = umsiData.at(-1) || Mod.buildLinePoints(filtered, "umsi").at(-1);
  if (!latest) return;

  const annotations = {
    fearZone: {
      type: "box",
      yMin: 0,
      yMax: 15,
      backgroundColor: "rgba(255, 71, 87, .08)",
      borderWidth: 0,
    },
    complacencyZone: {
      type: "box",
      yMin: 90,
      yMax: 100,
      backgroundColor: "rgba(165, 94, 234, .10)",
      borderWidth: 0,
    },
    fearLine: {
      type: "line",
      yMin: 15,
      yMax: 15,
      borderColor: "rgba(255,71,87,.45)",
      borderDash: [5, 5],
      borderWidth: 1,
    },
    complacencyLine: {
      type: "line",
      yMin: 90,
      yMax: 90,
      borderColor: "rgba(165,94,234,.55)",
      borderDash: [5, 5],
      borderWidth: 1,
    },
    currentLine: {
      type: "line",
      xMin: latest.x,
      xMax: latest.x,
      borderColor: "rgba(51,209,122,.55)",
      borderWidth: 1,
      borderDash: [3, 4],
      label: {
        display: true,
        content: `CURRENT ${Number(latest.y).toFixed(1)}`,
        position: "start",
        backgroundColor: "rgba(8,20,16,.92)",
        color: "#33d17a",
        borderColor: "#33d17a",
        borderWidth: 1,
        padding: 5,
        font: { size: 10, weight: "bold" },
      },
    },
  };

  visibleEvents.forEach((e, i) => {
    const x = Mod.toTs(e.date);
    annotations[`eventLine${i}`] = {
      type: "line",
      xMin: x,
      xMax: x,
      borderColor: "rgba(255,179,71,.23)",
      borderWidth: 1,
      borderDash: [2, 4],
      label: {
        display: range !== "1Y" && range !== "3Y",
        content: Mod.shortEventLabel(e.event),
        position: i % 2 === 0 ? "start" : "end",
        rotation: 0,
        backgroundColor: "rgba(25,20,12,.88)",
        color: "#ffb347",
        padding: 3,
        font: { size: 9 },
      },
    };
  });

  // Preserve focus across re-renders
  const preservedFocus = chartState.indicatorFocus ? { ...chartState.indicatorFocus } : null;

  if (chartState.historyChart) chartState.historyChart.destroy();

  const lineDataset = (label) => {
    const meta = Mod.SERIES_META[label];
    const data =
      label === "UMSI" ? umsiData
        : label === "Stress" ? stressData
          : label === "Fragility" ? fragilityData
            : spxData;
    const ds = {
      label,
      _seriesKey: label,
      data,
      yAxisID: meta.yAxisID,
      borderColor: meta.borderColor,
      backgroundColor: meta.backgroundColor,
      borderWidth: label === "UMSI" ? 1.8 : label === "SPX" ? 1.15 : 1.4,
      pointRadius: 0,
      pointHoverRadius: 4,
      tension: 0.08,
      fill: false,
      hidden: !chartState.seriesVisibility[label],
      borderDash:
        label === "UMSI" || label === "SPX"
          ? undefined
          : label === "Stress"
            ? [4, 3]
            : [2, 3],
    };
    // P0: dim UMSI segments touching low-quality points (when not filtered out)
    if (label === "UMSI" && !chartState.hideLowQuality) {
      ds.segment = {
        borderColor: (ctx) => {
          const q0 = ctx.p0?.raw?.quality;
          const q1 = ctx.p1?.raw?.quality;
          const low0 = q0 != null && Number.isFinite(Number(q0)) && Number(q0) < LOW_QUALITY_THRESHOLD;
          const low1 = q1 != null && Number.isFinite(Number(q1)) && Number(q1) < LOW_QUALITY_THRESHOLD;
          if (low0 || low1) return "rgba(158,231,255,.28)";
          return undefined;
        },
      };
    }
    return ds;
  };

  chartState.historyChart = new Chart(canvas, {
    type: "line",
    data: {
      datasets: [
        lineDataset("UMSI"),
        lineDataset("Stress"),
        lineDataset("Fragility"),
        lineDataset("SPX"),
        {
          type: "scatter",
          label: "Historical Events",
          data: eventPoints,
          pointRadius: 5,
          pointHoverRadius: 8,
          borderColor: "#ffffff",
          borderWidth: 1,
          backgroundColor: "#ffb347",
        },
        {
          type: "scatter",
          label: "Current",
          data: [latest],
          pointRadius: 5,
          pointHoverRadius: 7,
          borderColor: "#ffffff",
          borderWidth: 1,
          backgroundColor: "#33d17a",
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      parsing: false,
      normalized: true,
      animation: false,
      interaction: { mode: "nearest", intersect: false },
      onClick(evt, elements) {
        // Ignore brush/pan releases that moved more than a few pixels
        if (chartState.pointerDown) {
          const dx = Math.abs((evt.x ?? 0) - chartState.pointerDown.x);
          const dy = Math.abs((evt.y ?? 0) - chartState.pointerDown.y);
          chartState.pointerDown = null;
          if (dx > 6 || dy > 6) return;
        }
        // Prefer Historical Events hit when present
        if (elements?.length) {
          const eventHit = elements.find((el) => {
            const ds = chartState.historyChart.data.datasets[el.datasetIndex];
            return ds?.label === "Historical Events";
          });
          if (eventHit) {
            const ds = chartState.historyChart.data.datasets[eventHit.datasetIndex];
            const raw = ds.data[eventHit.index];
            Mod.firePointDetail(raw, history);
            return;
          }

          // Any visible line series (UMSI / Stress / Fragility / SPX) or Current / focus overlay
          const lineHit = elements.find((el) => {
            const ds = chartState.historyChart.data.datasets[el.datasetIndex];
            return ds && (ds._seriesKey || ds.label === "Current" || ds._focusOverlay);
          });
          if (lineHit) {
            const ds = chartState.historyChart.data.datasets[lineHit.datasetIndex];
            const raw = ds.data[lineHit.index];
            Mod.firePointDetail(raw, history);
            return;
          }
        }

        // Fallback: nearest UMSI point by x from click (works even with pointRadius 0)
        const xScale = chartState.historyChart.scales?.x;
        if (!xScale || !umsiData.length) return;
        const xVal = xScale.getValueForPixel(evt.x);
        if (xVal == null || Number.isNaN(xVal)) return;
        let best = umsiData[0];
        let bestDist = Math.abs(best.x - xVal);
        for (let i = 1; i < umsiData.length; i++) {
          const d = Math.abs(umsiData[i].x - xVal);
          if (d < bestDist) {
            best = umsiData[i];
            bestDist = d;
          }
        }
        // Ignore clicks far from any point (~45 calendar days)
        if (bestDist > 45 * 86400000) return;
        Mod.firePointDetail(best, history);
      },
      scales: {
        x: {
          type: "time",
          min: filteredStart,
          max: filteredEnd,
          time: {
            unit: range === "1Y" ? "month" : range === "3Y" ? "quarter" : "year",
            tooltipFormat: "yyyy-MM-dd",
          },
          grid: { color: "rgba(255,255,255,.05)" },
          ticks: {
            color: "#8d9aa9",
            maxRotation: 0,
            autoSkipPadding: 18,
          },
        },
        y: {
          min: 0,
          max: 100,
          position: "left",
          grid: { color: "rgba(255,255,255,.07)" },
          ticks: { color: "#8d9aa9", stepSize: 20 },
          title: {
            display: true,
            text: "UMSI / Stress / Fragility",
            color: "#6a7a8a",
            font: { size: 10 },
          },
        },
        y1: {
          position: "right",
          display: chartState.seriesVisibility.SPX,
          grid: { drawOnChartArea: false },
          ticks: {
            color: "rgba(120,180,140,.9)",
            callback(v) {
              return Number(v).toLocaleString(undefined, { maximumFractionDigits: 0 });
            },
          },
          title: {
            display: true,
            text: "S&P 500",
            color: "rgba(120,180,140,.85)",
            font: { size: 10 },
          },
        },
      },
      plugins: {
        legend: {
          labels: {
            color: "#c9d2dc",
            boxWidth: 11,
            usePointStyle: true,
            filter(item) {
              // Hide Current / focus overlay from legend clutter
              return item.text !== "Current" && item.text !== "Factor extremes";
            },
          },
          onClick(e, legendItem, legend) {
            const chart = legend.chart;
            const index = legendItem.datasetIndex;
            const ds = chart.data.datasets[index];
            if (!ds) return;

            if (ds._seriesKey) {
              Mod.setSeriesVisibility(ds._seriesKey, ds.hidden);
              return;
            }

            // Default Chart.js toggle for Historical Events
            ds.hidden = !ds.hidden;
            chart.update("none");
          },
        },
        tooltip: {
          displayColors: true,
          callbacks: {
            title(items) {
              const raw = items?.[0]?.raw;
              return raw?.date || new Date(raw?.x || 0).toISOString().slice(0, 10);
            },
            label(ctx) {
              const r = ctx.raw || {};
              if (ctx.dataset.label === "Historical Events") {
                return [
                  `${r.event}`,
                  `UMSI: ${r.y ?? "—"}`,
                  `Stress: ${r.stress ?? "—"}`,
                  `Fragility: ${r.fragility ?? "—"}`,
                  "Click for detail",
                ];
              }
              if (ctx.dataset.label === "Current") {
                return `Current UMSI: ${Number(r.y).toFixed(1)}`;
              }
              if (ctx.dataset._focusOverlay) {
                return [
                  `Factor extreme: ${r.factorScore != null ? Number(r.factorScore).toFixed(1) : "—"}`,
                  `UMSI: ${r.umsi ?? r.y ?? "—"}`,
                  "Click for day detail",
                ];
              }
              if (ctx.dataset._seriesKey === "SPX") {
                return [
                  `S&P 500: ${r.y != null ? Number(r.y).toLocaleString(undefined, { maximumFractionDigits: 2 }) : "—"}`,
                  `UMSI: ${r.umsi ?? "—"}`,
                  "Click for day detail",
                ];
              }
              if (ctx.dataset._seriesKey === "Stress") {
                return [
                  `Stress: ${r.y ?? "—"}`,
                  `UMSI: ${r.umsi ?? "—"}`,
                  `Fragility: ${r.fragility ?? "—"}`,
                  "Click for day detail",
                ];
              }
              if (ctx.dataset._seriesKey === "Fragility") {
                return [
                  `Fragility: ${r.y ?? "—"}`,
                  `UMSI: ${r.umsi ?? "—"}`,
                  `Stress: ${r.stress ?? "—"}`,
                  "Click for day detail",
                ];
              }
              return [
                `UMSI: ${r.y ?? "—"}`,
                `Stress: ${r.stress ?? "—"}`,
                `Fragility: ${r.fragility ?? "—"}`,
                `S&P 500: ${r.sp500 ?? "—"}`,
                `Model quality: ${r.quality != null ? `${Math.round(r.quality * 100)}%` : "—"}`,
                "Click for day detail",
              ];
            },
          },
        },
        annotation: { annotations },
        zoom: {
          limits: {
            x: { min: filteredStart, max: filteredEnd, minRange: 14 * 86400000 },
            y: { min: 0, max: 100 },
            // y1 auto-scales with visible data (no hard limits)
          },
          pan: {
            enabled: true,
            mode: "x",
            modifierKey: "shift",
            onPanComplete({ chart }) { Mod.emitRangeChange(chart); },
          },
          zoom: {
            mode: "x",
            drag: {
              enabled: true,
              backgroundColor: "rgba(158,231,255,.14)",
              borderColor: "rgba(158,231,255,.65)",
              borderWidth: 1,
              threshold: 8,
            },
            wheel: { enabled: true, modifierKey: "ctrl" },
            pinch: { enabled: true },
            onZoomComplete({ chart }) { Mod.emitRangeChange(chart); },
          },
        },
      },
    },
  });

  Mod.emitRangeChange(chartState.historyChart);
  Mod.syncSeriesChips();
  Mod.emitVisibilityChange();

  // Re-apply indicator focus after chart rebuild
  if (preservedFocus) {
    chartState.indicatorFocus = null; // allow Mod.setIndicatorFocus to re-apply
    Mod.setIndicatorFocus(preservedFocus);
  } else {
    updateFocusBanner(null);
  }

  if (!canvas._umsiPointerBound) {
    canvas._umsiPointerBound = true;
    canvas.addEventListener("pointerdown", (e) => {
      const rect = canvas.getBoundingClientRect();
      chartState.pointerDown = { x: e.clientX - rect.left, y: e.clientY - rect.top };
    });
  }
}
