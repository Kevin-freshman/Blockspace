const form = document.querySelector("#filterForm");
const scanButton = document.querySelector("#scanButton");
const formError = document.querySelector("#formError");
const progressWrap = document.querySelector("#progressWrap");
const progressBar = document.querySelector("#progressBar");
const progressText = document.querySelector("#progressText");
const statusDot = document.querySelector("#statusDot");
const statusText = document.querySelector("#statusText");
const addressRows = document.querySelector("#addressRows");
const tradeRows = document.querySelector("#tradeRows");
const errorPanel = document.querySelector("#errorPanel");
const errorRows = document.querySelector("#errorRows");
const tailRows = document.querySelector("#tailRows");

let latestState = null;

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  formError.hidden = true;
  const data = new FormData(form);
  const lookbackHours = Number(data.get("lookback_hours"));
  const efficiencyPercent = data.get("min_return_efficiency_percent");
  const pnlPerActivity = data.get("min_estimated_pnl_per_activity");
  const payload = {
    time_period: lookbackHours === 24 ? "DAY" : lookbackHours === 720 ? "MONTH" : "WEEK",
    order_by: "PNL",
    candidate_limit: 1000,
    lookback_hours: lookbackHours,
    min_trades_per_day: Number(data.get("min_trades_per_day")),
    min_return_efficiency: efficiencyPercent === "" ? null : Number(efficiencyPercent) / 100,
    min_estimated_pnl_per_activity: pnlPerActivity === "" ? null : Number(pnlPerActivity),
    result_sort: data.get("result_sort"),
  };
  scanButton.disabled = true;
  try {
    const response = await fetch("/api/scan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "无法启动扫描");
    await refreshState();
  } catch (error) {
    formError.textContent = error.message;
    formError.hidden = false;
    scanButton.disabled = false;
  }
});

async function refreshState() {
  try {
    const response = await fetch("/api/state", { cache: "no-store" });
    if (!response.ok) throw new Error("状态请求失败");
    latestState = await response.json();
    render(latestState);
  } catch (error) {
    statusDot.className = "status-dot error";
    statusText.textContent = "连接中断";
  }
}

function render(state) {
  const labels = { idle: "等待扫描", scanning: "正在扫描", ready: "实时观察中", error: "扫描失败" };
  statusDot.className = `status-dot ${state.status}`;
  statusText.textContent = labels[state.status] || state.status;
  scanButton.disabled = state.status === "scanning";

  const progress = state.progress || {};
  const total = Number(progress.total || 0);
  const completed = Number(progress.completed || 0);
  const percent = total > 0 ? Math.min(100, Math.round(completed / total * 100)) : 0;
  progressWrap.hidden = state.status === "idle";
  progressBar.style.width = `${state.status === "ready" ? 100 : percent}%`;
  progressText.textContent = progress.message || "";

  document.querySelector("#candidateCount").textContent = formatInt((state.addresses || []).length);
  document.querySelector("#filteredCount").textContent = formatInt((state.filtered_addresses || []).length);
  document.querySelector("#tradeCount").textContent = formatInt((state.live_trades || []).length);
  document.querySelector("#lastUpdated").textContent = state.last_live_at ? formatClock(state.last_live_at) : "—";

  renderAddresses(state.filtered_addresses || []);
  renderTailStudy(state);
  renderTrades((state.live_trades || []).slice(0, 150));
  renderErrors(state.errors || []);
}

function renderTailStudy(state) {
  const summary = state.tail_analysis || {};
  const filtered = state.filtered_addresses || [];
  const scoped = filtered.filter((row) => row.tail_analysis_in_scope);
  const tailCandidates = scoped
    .filter((row) => Number(row.tail_60m_trade_count || 0) > 0)
    .sort((a, b) =>
      Number(b.tail_60m_trade_count || 0) - Number(a.tail_60m_trade_count || 0)
      || Number(b.tail_60m_share || 0) - Number(a.tail_60m_share || 0)
    );

  document.querySelector("#tailSampledAddresses").textContent = summary.sampled_addresses == null ? "—" : formatInt(summary.sampled_addresses);
  document.querySelector("#tailTradeCount").textContent = summary.tail_trade_count == null ? "—" : formatInt(summary.tail_trade_count);
  document.querySelector("#tailTradeShare").textContent = summary.tail_trade_share == null ? "占已知样本 —" : `占已知样本 ${formatPercent(summary.tail_trade_share)}`;
  document.querySelector("#tailActiveAddresses").textContent = summary.addresses_with_tail_trades == null ? "—" : formatInt(summary.addresses_with_tail_trades);
  document.querySelector("#tailHighConfidence").textContent = summary.high_confidence_tail_count == null ? "—" : formatInt(summary.high_confidence_tail_count);

  if (state.status === "ready") {
    const requested = Number(state.config?.leaderboard?.candidate_limit || 0);
    document.querySelector("#tailStepScope").textContent =
      `本次请求榜单前 ${formatInt(requested)} 名，实际取得 ${formatInt((state.addresses || []).length)} 个候选，${formatInt(filtered.length)} 个通过筛选；其中排序靠前的 ${formatInt(summary.sampled_addresses || 0)} 个进入尾盘详析。`;
    document.querySelector("#tailStepCoverage").textContent =
      `尾盘样本共 ${formatInt(summary.total_trade_count || 0)} 条成交，其中 ${formatInt(summary.known_trade_count || 0)} 条取得计划结束时间，覆盖率 ${formatPercent(summary.settlement_coverage || 0)}；${formatInt(summary.slug_inferred_trade_count || 0)} 条来自短周期 slug 推导，${formatInt(summary.market_end_trade_count || 0)} 条来自官方结束字段，${formatInt(summary.capped_addresses || 0)} 个地址触及活动读取上限。低覆盖率或存在截断时不做完整业绩结论。`;
    document.querySelector("#tailStepPattern").textContent =
      `发现 ${formatInt(summary.addresses_with_tail_trades || 0)} 个地址在计划结束前 60 分钟有成交，共 ${formatInt(summary.tail_trade_count || 0)} 条，占结束时间已知成交的 ${summary.tail_trade_share == null ? "—" : formatPercent(summary.tail_trade_share)}。`;
    document.querySelector("#tailStepPrice").textContent =
      `60 分钟样本中有 ${formatInt(summary.high_confidence_tail_count || 0)} 条成交价 ≥0.90 的 BUY；全部 60 分钟成交名义规模合计 ${formatMoney(summary.tail_usdc_volume || 0)}。这仍未扣除费用、滑点，也未验证最终输赢。`;
  }

  if (!tailCandidates.length) {
    const message = state.status === "ready"
      ? "当前详析样本未发现计划结束前 60 分钟成交，或结束时间数据不足。"
      : "运行筛选后生成尾盘行为候选。";
    tailRows.innerHTML = `<tr><td colspan="6" class="empty">${message}</td></tr>`;
    return;
  }
  tailRows.innerHTML = tailCandidates.slice(0, 30).map((row) => {
    const address = safeAddress(row.address);
    const name = escapeHtml(row.user_name || shortAddress(address));
    return `<tr>
      <td><a class="address-link" href="https://polymarket.com/profile/${address}" target="_blank" rel="noreferrer"><span class="cell-title">${name}</span><span class="cell-sub">${shortAddress(address)} · #${formatInt(row.rank)}</span></a></td>
      <td><span class="metric">${formatInt(row.tail_60m_trade_count)}</span><span class="cell-sub">${row.tail_60m_share == null ? "—" : formatPercent(row.tail_60m_share)} · ${formatInt(row.tail_60m_market_count)} markets</span></td>
      <td><span class="metric">${formatInt(row.tail_6h_trade_count)} / ${formatInt(row.tail_24h_trade_count)}</span><span class="cell-sub">6h / 24h</span></td>
      <td><span class="metric">${row.tail_60m_avg_price == null ? "—" : formatNumber(row.tail_60m_avg_price, 3)}</span><span class="cell-sub">${formatInt(row.tail_60m_high_confidence_count)} at ≥0.90</span></td>
      <td><span class="metric">${formatInt(row.tail_60m_buy_count)} / ${formatInt(row.tail_60m_sell_count)}</span><span class="cell-sub">BUY / SELL</span></td>
      <td><span class="metric">${formatPercent(row.settlement_coverage || 0)}</span><span class="cell-sub">${formatInt(row.settlement_trade_count)} / ${formatInt(row.trade_count)} records${row.activity_truncated ? " · capped" : ""}</span></td>
    </tr>`;
  }).join("");
}

function renderAddresses(rows) {
  if (!rows.length) {
    const message = latestState?.status === "ready" ? "当前条件下没有地址通过筛选。" : "运行一次筛选后，结果会显示在这里。";
    addressRows.innerHTML = `<tr><td colspan="6" class="empty">${message}</td></tr>`;
    return;
  }
  addressRows.innerHTML = rows.map((row) => {
    const address = safeAddress(row.address);
    const name = escapeHtml(row.user_name || shortAddress(address));
    const pnlClass = Number(row.leaderboard_pnl) >= 0 ? "positive" : "negative";
    const efficiencyClass = Number(row.return_efficiency) >= 0 ? "positive" : "negative";
    const truncated = row.activity_truncated ? " · capped" : "";
    return `<tr>
      <td><div class="primary-cell"><span class="rank">#${formatInt(row.rank)}</span><span>
        <a class="cell-title address-link" href="https://polymarket.com/profile/${address}" target="_blank" rel="noreferrer">${name}</a>
        <span class="cell-sub">${shortAddress(address)}</span></span></div></td>
      <td><span class="metric ${pnlClass}">${formatMoney(row.leaderboard_pnl)}</span><span class="cell-sub ${efficiencyClass}">${row.return_efficiency == null ? "—" : formatPercent(row.return_efficiency)} · PNL / 成交量</span></td>
      <td><span class="metric">${row.estimated_pnl_per_activity == null ? "—" : formatMoney(row.estimated_pnl_per_activity)}</span><span class="cell-sub">估算 / 成交记录</span></td>
      <td><span class="metric">${formatInt(row.trade_count)} / ${formatInt(row.transaction_count)}</span><span class="cell-sub">records / tx${truncated}</span></td>
      <td><span class="metric">${formatNumber(row.trades_per_day, 1)}</span><span class="cell-sub">次 / 天</span></td>
      <td><span class="metric">${row.open_position_count == null ? "—" : formatInt(row.open_position_count)}</span><span class="cell-sub">${row.open_position_value == null ? "" : formatMoney(row.open_position_value)}</span></td>
    </tr>`;
  }).join("");
}

function renderTrades(rows) {
  if (!rows.length) {
    tradeRows.innerHTML = '<tr><td colspan="8" class="empty">通过筛选后，排序靠前的 100 个地址会自动进入轮询。</td></tr>';
    return;
  }
  tradeRows.innerHTML = rows.map((row) => {
    const address = safeAddress(row.address);
    const tx = safeHash(row.transaction_hash);
    const side = String(row.side || "").toUpperCase();
    const slug = encodeURIComponent(String(row.event_slug || row.slug || ""));
    const marketTitle = escapeHtml(row.title || "Unknown market");
    const marketLink = slug ? `https://polymarket.com/event/${slug}` : "https://polymarket.com";
    const distance = row.minutes_to_settlement == null ? "—" : formatDurationMinutes(row.minutes_to_settlement);
    return `<tr>
      <td><span class="metric">${formatClock(row.timestamp_utc)}</span><span class="cell-sub">${formatDate(row.timestamp_utc)}</span></td>
      <td><a class="address-link" href="https://polymarket.com/profile/${address}" target="_blank" rel="noreferrer"><span class="cell-title">${escapeHtml(row.user_name || shortAddress(address))}</span><span class="cell-sub">${shortAddress(address)}</span></a></td>
      <td><span class="badge ${side.toLowerCase()}">${escapeHtml(side || "—")}</span></td>
      <td><a class="address-link" href="${marketLink}" target="_blank" rel="noreferrer"><span class="cell-title">${marketTitle}</span><span class="cell-sub">${escapeHtml(row.outcome || "")}</span></a></td>
      <td><span class="metric">${formatNumber(row.size, 2)}</span><span class="cell-sub">${row.usdc_size ? formatMoney(row.usdc_size) : "shares"}</span></td>
      <td><span class="metric">${formatNumber(row.price, 4)}</span></td>
      <td><span class="metric ${Number(row.minutes_to_settlement) < 0 ? "negative" : ""}">${distance}</span></td>
      <td><span class="badge ${row.onchain_status === "confirmed" ? "buy" : ""}">${row.onchain_status === "confirmed" ? "CHAIN ✓" : escapeHtml(row.onchain_status || "pending")}</span>${tx ? ` <a class="evidence-link" href="https://polygonscan.com/tx/${tx}" target="_blank" rel="noreferrer">TX ↗</a>` : ""}</td>
    </tr>`;
  }).join("");
}

function renderErrors(errors) {
  errorPanel.hidden = !errors.length;
  if (!errors.length) return;
  errorRows.innerHTML = errors.slice(-8).reverse().map((item) =>
    `<li><strong>${escapeHtml(item.scope || "request")}</strong> — ${escapeHtml(item.message || "unknown error")}</li>`
  ).join("");
}

function formatMoney(value) {
  const number = Number(value || 0);
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: Math.abs(number) < 100 ? 2 : 0 }).format(number);
}

function formatInt(value) { return new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 }).format(Number(value || 0)); }
function formatNumber(value, digits = 2) { return new Intl.NumberFormat("en-US", { maximumFractionDigits: digits }).format(Number(value || 0)); }
function formatPercent(value) { return `${formatNumber(Number(value || 0) * 100, 1)}%`; }

function formatDurationHours(hours) {
  const number = Number(hours);
  if (number < 1) return `${formatNumber(number * 60, 0)}m`;
  if (number < 48) return `${formatNumber(number, 1)}h`;
  return `${formatNumber(number / 24, 1)}d`;
}

function formatDurationMinutes(minutes) {
  const number = Number(minutes);
  const sign = number < 0 ? "-" : "";
  return sign + formatDurationHours(Math.abs(number) / 60);
}

function formatClock(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleTimeString("zh-CN", { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit", timeZone: "UTC" });
}

function formatDate(value) {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleDateString("zh-CN", { month: "2-digit", day: "2-digit", timeZone: "UTC" }) + " UTC";
}

function shortAddress(value) { return value ? `${value.slice(0, 6)}…${value.slice(-4)}` : "unknown"; }
function safeAddress(value) { return /^0x[a-fA-F0-9]{40}$/.test(String(value || "")) ? String(value).toLowerCase() : ""; }
function safeHash(value) { return /^0x[a-fA-F0-9]{64}$/.test(String(value || "")) ? String(value).toLowerCase() : ""; }

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[char]));
}

refreshState();
setInterval(refreshState, 5000);
