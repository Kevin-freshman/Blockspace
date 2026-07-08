"""画像表生成：输出供人工审阅的账户画像 markdown 表 + csv。

设计原则（来自 updated_prompt.md）：
- AI/代码只负责整理素材和给出规则化的"待验证假设"，模式发现由人完成
- 每个数字都能追溯到链上记录（fills/ctf_events 里有 tx_hash）
"""
import csv
import datetime
import os

from .db import connect
from .util import setup_logging

log = setup_logging()


def _fmt(v, spec=",.0f", dash="-"):
    if v is None:
        return dash
    try:
        return format(v, spec)
    except (TypeError, ValueError):
        return str(v)


def _hypotheses(s: dict) -> str:
    """基于统计特征的规则化初步假设（仅供人工审阅时参考，非结论）。"""
    hs = []
    wr = s["win_rate"]
    aep = s["avg_entry_price"]
    late = s["late_buy_share"]
    lead = s["median_entry_lead_h"]
    if wr is not None and s["n_resolved"] >= 10:
        if wr >= 0.8 and (aep or 0) >= 0.85:
            hs.append("高胜率但建仓价高：偏好买接近确定的结果，收益靠规模")
        elif wr >= 0.8 and (aep or 1) < 0.65:
            hs.append("高胜率且建仓赔率好：信息优势或判断力强，值得深挖")
    if late is not None and late >= 0.5:
        hs.append(f"超过一半买入发生在截止前{24}h内：临场型/事件驱动")
    if lead is not None and lead > 24 * 30:
        hs.append("建仓极早（中位提前>30天）：长线判断型")
    if s["sell_volume"] > 0 and s["buy_volume"] > 0 and \
            min(s["sell_volume"], s["buy_volume"]) / max(s["sell_volume"], s["buy_volume"]) > 0.7:
        hs.append("买卖量接近：高频交易/做市/套利特征")
    if s["official_pnl"] is not None and s["pnl_diff"] is not None and \
            abs(s["pnl_diff"]) > max(abs(s["official_pnl"]) * 0.3, 10000):
        hs.append("与官方 PnL 偏差大：链上覆盖可能不完整（回填窗口外/负风险市场），数字仅供参考")
    return "；".join(hs) if hs else "模式不明"


def run_profile(cfg: dict) -> str:
    """生成 output/profiles.md 与 profiles.csv，返回 md 路径。"""
    conn = connect(cfg)
    outdir = cfg["storage"]["output_dir"]
    os.makedirs(outdir, exist_ok=True)
    top_detail = cfg["profile"]["top_detail"]

    rows = conn.execute("""
        SELECT s.*, c.username FROM address_stats s
        LEFT JOIN candidates c ON c.address = s.address
        ORDER BY s.total_pnl DESC
    """).fetchall()
    if not rows:
        raise RuntimeError("address_stats 为空：先跑 sync 和 pnl")

    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    md = [
        f"# Polymarket 聪明钱账户画像表",
        "",
        f"生成时间：{now}　地址数：{len(rows)}",
        "",
        "> 口径说明：PnL 为链上现金流法（卖出+merge+赔付-买入-split）+ 当前持仓市值；",
        "> 胜率只统计已揭晓市场；数据窗口受 `ingest.backfill_days` 限制，",
        "> 与官方全期 PnL 偏差大的地址已在假设列标注。",
        "> 「初步假设」为规则生成，仅供审阅参考，不是结论。",
        "",
        "| # | 地址 | 用户名 | 总PnL | 已实现 | 持仓值 | 胜率 | 已结算市场 | 均价 | 中位提前(h) | 临场买入占比 | 官方PnL | 初步假设 |",
        "|---|------|--------|-------|--------|--------|------|-----------|------|-------------|--------------|---------|----------|",
    ]
    csv_rows = []
    for i, r in enumerate(rows, 1):
        s = dict(r)
        hyp = _hypotheses(s)
        md.append(
            f"| {i} | `{s['address']}` | {s['username'] or '-'} "
            f"| {_fmt(s['total_pnl'])} | {_fmt(s['realized_pnl'])} | {_fmt(s['position_value'])} "
            f"| {_fmt(s['win_rate'], '.0%')} | {s['n_resolved']} "
            f"| {_fmt(s['avg_entry_price'], '.2f')} | {_fmt(s['median_entry_lead_h'], ',.0f')} "
            f"| {_fmt(s['late_buy_share'], '.0%')} | {_fmt(s['official_pnl'])} | {hyp} |")
        csv_rows.append({**{k: s[k] for k in s.keys() if not k.startswith("_")},
                         "hypotheses": hyp})

    # 前 N 名的明细小节：最赚/最亏市场，供人工深挖
    md += ["", "---", "", f"## 前 {min(top_detail, len(rows))} 名明细", ""]
    for r in rows[:top_detail]:
        addr = r["address"]
        md += [f"### `{addr}`（{r['username'] or '匿名'}）", ""]
        mkts = conn.execute("""
            SELECT p.*, m.question, m.category, m.end_date FROM address_market_pnl p
            LEFT JOIN markets m ON m.condition_id = p.condition_id
            WHERE p.address=? ORDER BY p.cash_net DESC
        """, (addr,)).fetchall()
        top = [m for m in mkts if m["cash_net"] > 0][:5]
        bottom = [m for m in reversed(mkts) if m["cash_net"] < 0][:3]
        if top:
            md.append("最赚的市场：")
            for m in top:
                q = m["question"] or m["condition_id"][:18]
                md.append(f"- +{_fmt(m['cash_net'])} | {q}（{m['category'] or '?'}，截止 {m['end_date'] or '?'}）")
        if bottom:
            md.append("")
            md.append("最亏的市场：")
            for m in bottom:
                q = m["question"] or m["condition_id"][:18]
                md.append(f"- {_fmt(m['cash_net'])} | {q}")
        md.append("")

    md_path = os.path.join(outdir, "profiles.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md))

    csv_path = os.path.join(outdir, "profiles.csv")
    if csv_rows:
        with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(csv_rows[0].keys()))
            w.writeheader()
            w.writerows(csv_rows)

    log.info("画像表已生成: %s（%d 个地址）", md_path, len(rows))
    conn.close()
    return md_path
