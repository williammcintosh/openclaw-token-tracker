const fmtInt = new Intl.NumberFormat("en-NZ");
const fmtMoney = new Intl.NumberFormat("en-NZ", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 2,
});
const fmtDateTime = new Intl.DateTimeFormat("en-NZ", {
  dateStyle: "medium",
  timeStyle: "short",
});

const tokenShort = (value) => {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}k`;
  return fmtInt.format(value);
};

const percent = (value) => `${(value * 100).toFixed(1)}%`;

function statCard(label, value, sub = "") {
  return `
    <article class="stat-card">
      <span class="stat-label">${label}</span>
      <div class="stat-value">${value}</div>
      <div class="stat-sub">${sub}</div>
    </article>
  `;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function renderOverview(data) {
  document.getElementById("generated-at").textContent = fmtDateTime.format(new Date(data.generatedAt));
  document.getElementById("date-range").textContent = `${data.window.from ?? "—"} → ${data.window.to ?? "—"}`;
  document.getElementById("event-count").textContent = fmtInt.format(data.source.assistantUsageEventsStored ?? 0);

  const totals = data.totals;
  document.getElementById("overview-cards").innerHTML = [
    statCard("Total tokens", fmtInt.format(totals.totalTokens), `${fmtInt.format(totals.assistantMessages)} assistant messages`),
    statCard("Input tokens", fmtInt.format(totals.inputTokens), `${percent(totals.totalTokens ? totals.inputTokens / totals.totalTokens : 0)} of total`),
    statCard("Output tokens", fmtInt.format(totals.outputTokens), `${percent(totals.totalTokens ? totals.outputTokens / totals.totalTokens : 0)} of total`),
    statCard("Cache read", fmtInt.format(totals.cacheReadTokens), `${percent(totals.totalTokens ? totals.cacheReadTokens / totals.totalTokens : 0)} of total`),
    statCard("Estimated cost", fmtMoney.format(totals.costTotal || 0), "From recorded assistant usage"),
    statCard("Days covered", fmtInt.format(data.window.days || 0), "Distinct active dates"),
  ].join("");
}

function emptyRollup() {
  return {
    assistantMessages: 0,
    inputTokens: 0,
    outputTokens: 0,
    cacheReadTokens: 0,
    cacheWriteTokens: 0,
    totalTokens: 0,
    costTotal: 0,
    activeDays: 0,
  };
}

function getBillingCycleStart(dayString, cutoffDay = 26) {
  const [year, month, day] = dayString.split("-").map(Number);
  const startMonth = day >= cutoffDay ? month : month - 1;
  const start = new Date(Date.UTC(year, startMonth - 1, cutoffDay));
  return start.toISOString().slice(0, 10);
}

function addMonthsUtc(date, months) {
  return new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth() + months, date.getUTCDate()));
}

function buildBillingCycles(data, cutoffDay = 26) {
  const cycles = new Map();

  for (const row of data.daily || []) {
    const cycleStart = getBillingCycleStart(row.day, cutoffDay);
    if (!cycles.has(cycleStart)) {
      const startDate = new Date(`${cycleStart}T00:00:00Z`);
      const endDate = addMonthsUtc(startDate, 1);
      endDate.setUTCDate(endDate.getUTCDate() - 1);
      cycles.set(cycleStart, {
        start: cycleStart,
        end: endDate.toISOString().slice(0, 10),
        ...emptyRollup(),
      });
    }

    const cycle = cycles.get(cycleStart);
    cycle.assistantMessages += row.assistantMessages || 0;
    cycle.inputTokens += row.inputTokens || 0;
    cycle.outputTokens += row.outputTokens || 0;
    cycle.cacheReadTokens += row.cacheReadTokens || 0;
    cycle.cacheWriteTokens += row.cacheWriteTokens || 0;
    cycle.totalTokens += row.totalTokens || 0;
    cycle.costTotal += row.costTotal || 0;
    cycle.activeDays += 1;
  }

  return Array.from(cycles.values()).sort((a, b) => a.start.localeCompare(b.start));
}

function renderBillingCycles(data) {
  const cycles = buildBillingCycles(data);
  const current = cycles.at(-1);
  const previous = cycles.length > 1 ? cycles.at(-2) : null;
  const maxTokens = Math.max(...cycles.map((cycle) => cycle.totalTokens), 1);

  document.getElementById("billing-cycle-cards").innerHTML = current
    ? [
        statCard("Current cycle", fmtInt.format(current.totalTokens), `${current.start} → ${current.end}`),
        statCard("Current messages", fmtInt.format(current.assistantMessages), `${fmtInt.format(current.activeDays)} active days`),
        statCard(
          "Previous cycle",
          previous ? fmtInt.format(previous.totalTokens) : "—",
          previous ? `${previous.start} → ${previous.end}` : "Not enough history yet"
        ),
        statCard(
          "Cycle change",
          previous && previous.totalTokens
            ? `${previous.totalTokens === 0 ? "—" : `${current.totalTokens >= previous.totalTokens ? "+" : ""}${percent((current.totalTokens - previous.totalTokens) / previous.totalTokens)}`}`
            : "—",
          previous ? "Current vs previous cycle" : "Need at least two cycles"
        ),
      ].join("")
    : `<p class="muted">No billing-cycle data yet.</p>`;

  document.getElementById("billing-chart").innerHTML = cycles
    .slice()
    .reverse()
    .map(
      (cycle) => `
        <div class="cycle-row">
          <div class="cycle-meta">
            <strong>${escapeHtml(cycle.start)} → ${escapeHtml(cycle.end)}</strong>
            <span>${fmtInt.format(cycle.totalTokens)} tokens · ${fmtInt.format(cycle.assistantMessages)} messages</span>
          </div>
          <div class="progress cycle-progress"><span style="width:${Math.max(4, (cycle.totalTokens / maxTokens) * 100)}%"></span></div>
          <div class="muted">${fmtInt.format(cycle.activeDays)} active days · ${fmtMoney.format(cycle.costTotal || 0)}</div>
        </div>
      `
    )
    .join("");
}

function renderDailyChart(data) {
  const chart = document.getElementById("daily-chart");
  const rows = (data.daily || []).slice(-30);
  const maxTokens = Math.max(...rows.map((row) => row.totalTokens), 1);
  chart.innerHTML = rows
    .map((row) => {
      const height = Math.max(6, Math.round((row.totalTokens / maxTokens) * 170));
      return `
        <div class="bar-col" title="${row.day}: ${fmtInt.format(row.totalTokens)} tokens">
          <div class="bar-value">${tokenShort(row.totalTokens)}</div>
          <div class="bar-wrap"><div class="bar" style="height:${height}px"></div></div>
          <div class="bar-label">${escapeHtml(row.day.slice(5))}</div>
        </div>
      `;
    })
    .join("");
}

function renderBreakdown(id, rows) {
  const maxTokens = Math.max(...rows.map((row) => row.totalTokens), 1);
  document.getElementById(id).innerHTML = rows
    .slice(0, 8)
    .map(
      (row) => `
        <div class="breakdown-row">
          <div class="breakdown-meta">
            <strong>${escapeHtml(row.label)}</strong>
            <span>${fmtInt.format(row.totalTokens)} · ${percent(row.shareOfTotalTokens || 0)}</span>
          </div>
          <div class="progress"><span style="width:${Math.max(4, (row.totalTokens / maxTokens) * 100)}%"></span></div>
          <div class="muted">${fmtInt.format(row.assistantMessages)} assistant messages · ${fmtMoney.format(row.costTotal || 0)}</div>
        </div>
      `
    )
    .join("");
}

function renderDailyTable(data) {
  const body = document.getElementById("daily-table");
  body.innerHTML = (data.daily || [])
    .slice()
    .reverse()
    .slice(0, 14)
    .map(
      (row) => `
        <tr>
          <td>${escapeHtml(row.day)}</td>
          <td>${fmtInt.format(row.assistantMessages)}</td>
          <td>${fmtInt.format(row.totalTokens)}</td>
          <td>${fmtInt.format(row.inputTokens)}</td>
          <td>${fmtInt.format(row.outputTokens)}</td>
          <td>${fmtInt.format(row.cacheReadTokens)}</td>
          <td>${fmtMoney.format(row.costTotal || 0)}</td>
        </tr>
      `
    )
    .join("");
}

async function loadDashboard() {
  const response = await fetch(`data/summary.json?t=${Date.now()}`, { cache: "no-store" });
  if (!response.ok) throw new Error(`Failed to load data: ${response.status}`);
  const data = await response.json();
  renderOverview(data);
  renderBillingCycles(data);
  renderDailyChart(data);
  renderBreakdown("breakdown-category", data.breakdowns.category || []);
  renderBreakdown("breakdown-model", data.breakdowns.model || []);
  renderBreakdown("breakdown-sessionKind", data.breakdowns.sessionKind || []);
  renderBreakdown("breakdown-provider", data.breakdowns.provider || []);
  renderDailyTable(data);
}

async function refreshDashboard() {
  const button = document.getElementById("update-button");
  if (button) {
    button.disabled = true;
    button.textContent = "Updating…";
  }

  try {
    await loadDashboard();
    if (button) button.textContent = "Updated";
  } catch (error) {
    if (button) button.textContent = "Retry update";
    throw error;
  } finally {
    if (button) {
      window.setTimeout(() => {
        button.disabled = false;
        button.textContent = "Update";
      }, 900);
    }
  }
}

async function main() {
  document.getElementById("update-button")?.addEventListener("click", () => {
    refreshDashboard().catch((error) => {
      console.error(error);
    });
  });

  await loadDashboard();
}

main().catch((error) => {
  document.body.innerHTML = `<main class="shell"><section class="card"><h1>Dashboard unavailable</h1><p class="muted">${escapeHtml(error.message)}</p></section></main>`;
});
