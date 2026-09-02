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
const researchTopK = document.querySelector("#researchTopK");
const researchExport = document.querySelector("#researchExport");
const researchDropoutRows = document.querySelector("#researchDropoutRows");
const researchComparisonRows = document.querySelector("#researchComparisonRows");
const researchHypotheses = document.querySelector("#researchHypotheses");

let latestState = null;
let latestResearch = null;

document.querySelectorAll(".view-tab").forEach((button) => {
  button.addEventListener("click", () => {
    const view = button.dataset.view;
    document.querySelector("#filterView").hidden = view !== "filter";
    document.querySelector("#researchView").hidden = view !== "research";
    document.querySelectorAll(".view-tab").forEach((item) => {
      const active = item === button;
      item.classList.toggle("active", active);
      item.setAttribute("aria-selected", active ? "true" : "false");
    });
    if (view === "research") refreshResearch();
  });
});

researchTopK.addEventListener("change", () => {
  researchExport.href = `/api/export/research.csv?top_k=${encodeURIComponent(researchTopK.value)}`;
  refreshResearch();
});

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

async function refreshResearch() {
  try {
    const topK = Number(researchTopK.value || 100);
    const response = await fetch(`/api/research?top_k=${topK}`, { cache: "no-store" });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "研究状态请求失败");
    latestResearch = data;
    renderResearch(data);
  } catch (error) {
    document.querySelector("#researchStatusBadge").textContent = "ERROR";
    document.querySelector("#researchStatusText").textContent = error.message;
  }
}

function renderResearch(data) {
  const collection = data.collection || {};
  const current = data.current || {};
  const transition = data.transition;
  document.querySelector("#researchSnapshotCount").textContent = formatInt(collection.snapshot_count || 0);
  document.querySelector("#researchPnlCount").textContent = current.pnl_candidate_count == null ? "—" : formatInt(current.pnl_candidate_count);
  document.querySelector("#researchLoserCount").textContent = current.volume_loser_count == null ? "—" : formatInt(current.volume_loser_count);
  document.querySelector("#researchLatest").textContent = collection.latest_slot_at ? formatDateTimeSlot(collection.latest_slot_at) : "—";

  const statusBadge = document.querySelector("#researchStatusBadge");
  statusBadge.textContent = String(data.status || "collecting").toUpperCase();
  statusBadge.className = `badge ${data.status === "ready" ? "buy" : ""}`;
  const needed = Math.max(0, Math.ceil(Number(collection.comparison_hours || 24) / Number(collection.cadence_hours || 6)) + 1 - Number(collection.snapshot_count || 0));
  document.querySelector("#researchStatusText").textContent = transition
    ? `${transition.previous_slot_at || "—"} → ${transition.current_slot_at || "—"}，按官方 CRYPTO DAY PNL Top ${formatInt(transition.top_k)} 比较。`
    : `正在积累 24 小时配对快照；至少还需要 ${formatInt(needed)} 个采样槽。服务重启不会伪造历史回填。`;

  renderResearchTransition(transition);
  renderResearchAnalysis(data.latest_analysis);
  renderHypotheses(data.hypotheses || []);
}

function renderResearchTransition(transition) {
  document.querySelector("#researchEntered").textContent = transition ? formatInt(transition.entered_count) : "—";
  document.querySelector("#researchRetained").textContent = transition ? formatInt(transition.retained_count) : "—";
  document.querySelector("#researchDropped").textContent = transition ? formatInt(transition.dropped_count) : "—";
  document.querySelector("#researchRetention").textContent = transition?.retention_rate == null ? "—" : formatPercent(transition.retention_rate);
  if (!transition) {
    document.querySelector("#researchTransitionNote").textContent = "正在积累 24 小时配对快照。";
    researchDropoutRows.innerHTML = '<tr><td colspan="7" class="empty">有完整 24 小时比较后显示掉榜地址。</td></tr>';
    return;
  }
  const reasons = transition.reason_counts || {};
  document.querySelector("#researchTransitionNote").textContent =
    `可见解释：${formatInt(reasons.current_pnl_non_positive || 0)} 个当前 PNL 非正，${formatInt(reasons.official_window_pnl_lower || 0)} 个官方窗口 PNL 值下降，${formatInt(reasons.relative_rank_competition || 0)} 个更符合相对排名竞争；这些是分类证据，不是因果结论。`;
  const dropped = (transition.rows || []).filter((row) => row.state === "dropped");
  if (!dropped.length) {
    researchDropoutRows.innerHTML = '<tr><td colspan="7" class="empty">当前比较没有掉出所选 Top K 的地址。</td></tr>';
    return;
  }
  researchDropoutRows.innerHTML = dropped.slice(0, 50).map((row) => {
    const address = safeAddress(row.address);
    const rank = row.current_rank == null ? `&gt; ${formatInt(transition.top_k)}` : `#${formatInt(row.current_rank)}`;
    return `<tr>
      <td><a class="address-link" href="https://polymarket.com/profile/${address}" target="_blank" rel="noreferrer"><span class="cell-title">${escapeHtml(row.user_name || shortAddress(address))}</span><span class="cell-sub">${shortAddress(address)}</span></a></td>
      <td><span class="metric">#${formatInt(row.previous_rank)}</span></td>
      <td><span class="metric">${rank}</span></td>
      <td><span class="metric ${Number(row.previous_pnl) >= 0 ? "positive" : "negative"}">${formatMoney(row.previous_pnl)}</span></td>
      <td><span class="metric ${Number(row.current_pnl) >= 0 ? "positive" : "negative"}">${row.current_pnl == null ? "—" : formatMoney(row.current_pnl)}</span></td>
      <td><span class="badge">${escapeHtml(researchReason(row.visible_reason))}</span></td>
      <td><span class="cell-sub">${row.current_rank_exact ? "官方 user 排名" : "榜单边界/对照池"}</span></td>
    </tr>`;
  }).join("");
}

function renderResearchAnalysis(analysis) {
  const note = document.querySelector("#researchAnalysisNote");
  if (!analysis) {
    note.textContent = "等待第一个 00:00 UTC 每日富集样本；不会把 6 小时重叠窗口当作独立样本。";
    researchComparisonRows.innerHTML = '<tr><td colspan="6" class="empty">积累数据后显示参数指纹。</td></tr>';
    return;
  }
  const quality = analysis.data_quality || {};
  note.textContent = `${analysis.slot_at || "—"}：请求 ${formatInt(quality.requested_addresses || 0)} 个地址，成功 ${formatInt(quality.successful_addresses || 0)}，截断 ${formatInt(quality.truncated_addresses || 0)}，有界交易哈希回执全部确认 ${formatInt(quality.receipt_verified_addresses || 0)}。${analysis.eligible_for_insight ? "数据质量可进入 Insight 累计。" : "该日数据质量不足，只展示、不进入 Insight 累计。"}这里展示的是公开行为指纹，不是机器人的私有参数。`;
  const rows = (analysis.comparisons || []).flatMap((comparison) =>
    (comparison.metrics || []).map((metric) => ({ comparison, metric }))
  );
  if (!rows.length) {
    researchComparisonRows.innerHTML = '<tr><td colspan="6" class="empty">该样本没有足够的可比参数。</td></tr>';
    return;
  }
  researchComparisonRows.innerHTML = rows.map(({ comparison, metric }) => `<tr>
    <td><span class="cell-title">${escapeHtml(comparison.label || comparison.id)}</span><span class="cell-sub">第一组 − 第二组</span></td>
    <td>${escapeHtml(metric.label || metric.field)}</td>
    <td><span class="metric">${formatResearchMetric(metric.field, metric.first_median)}</span></td>
    <td><span class="metric">${formatResearchMetric(metric.field, metric.second_median)}</span></td>
    <td><span class="metric ${Number(metric.difference) >= 0 ? "positive" : "negative"}">${metric.difference == null ? "—" : formatResearchMetric(metric.field, metric.difference)}</span></td>
    <td><span class="cell-sub">${formatInt(metric.first_count)} / ${formatInt(metric.second_count)}</span></td>
  </tr>`).join("");
}

function renderHypotheses(rows) {
  if (!rows.length) {
    researchHypotheses.innerHTML = '<article><span class="badge">COLLECTING</span><h3>正在建立研究基线</h3></article>';
    return;
  }
  const statusLabels = {
    collecting: "COLLECTING",
    finding: "FINDING",
    insight: "INSIGHT",
    counter_signal: "反直觉信号",
    inconclusive: "证据不一致",
    not_testable_v1: "隐藏变量",
  };
  researchHypotheses.innerHTML = rows.map((row) => {
    const classes = row.status === "insight" ? "insight" : row.status === "counter_signal" ? "counter-signal" : "";
    const interval = row.bootstrap_95_low == null ? "区间待积累" : `bootstrap 95% [${formatNumber(row.bootstrap_95_low, 4)}, ${formatNumber(row.bootstrap_95_high, 4)}]`;
    const evidence = row.status === "not_testable_v1"
      ? "第一版明确不检验"
      : `${formatInt(row.days || 0)}/${formatInt(row.minimum_days || 7)} 日 · 唯一地址 ${formatInt(row.first_unique_addresses || 0)}/${formatInt(row.second_unique_addresses || 0)} · 方向一致 ${formatPercent(row.direction_consistency || 0)} · ${interval}`;
    return `<article class="${classes}">
      <span class="badge ${row.status === "insight" ? "buy" : ""}">${escapeHtml(statusLabels[row.status] || row.status)}</span>
      <h3>${escapeHtml(row.title || row.id)}</h3>
      <p>${escapeHtml(row.statement || "")}</p>
      <small>${escapeHtml(evidence)}</small>
    </article>`;
  }).join("");
}

function researchReason(value) {
  return ({
    current_pnl_non_positive: "当前 PNL 非正",
    official_window_pnl_lower: "官方窗口 PNL 值下降",
    relative_rank_competition: "相对排名竞争",
    current_metric_unknown: "当前指标未知",
  })[value] || "待解释";
}

function formatResearchMetric(field, value) {
  if (value == null) return "—";
  if (["market_concentration", "buy_share", "high_price_buy_share", "tail_60m_share"].includes(field)) return formatPercent(value);
  if (["median_trade_usdc"].includes(field)) return formatMoney(value);
  return formatNumber(value, 3);
}

function formatDateTimeSlot(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return `${date.toISOString().slice(5, 10)} ${date.toISOString().slice(11, 16)}`;
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
refreshResearch();
setInterval(refreshState, 5000);
setInterval(refreshResearch, 60000);
