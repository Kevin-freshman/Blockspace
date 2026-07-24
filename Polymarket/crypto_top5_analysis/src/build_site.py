#!/usr/bin/env python3
"""Build the deterministic, public-safe Phase 0C research website."""

from __future__ import annotations

import hashlib
import html
import json
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"
PUBLIC = ROOT / "public"
ACCOUNT_ORDER = ["0x8dxd", "Anon", "justdance", "k9Q2mX4L8A7ZP3R", "Bonereaper"]
SLUGS = {name: name.lower().replace("0x", "0x-") for name in ACCOUNT_ORDER}
INPUTS = {
    name: REPORTS / "phase0c" / f"{name}.json"
    for name in (
        "research_metrics",
        "market_coverage_audit",
        "failed_requests_audit",
        "coverage_audit",
        "raw_integrity_audit",
    )
}


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"), parse_float=Decimal)


def canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = text.encode("utf-8")
    if not path.exists() or path.read_bytes() != encoded:
        path.write_bytes(encoded)


def esc(value) -> str:
    return html.escape(str(value))


def integer(value) -> str:
    return f"{int(value):,}"


def decimal(value, places=2) -> str:
    if value is None:
        return "No data"
    d = Decimal(str(value))
    q = Decimal(1).scaleb(-places)
    return f"{d.quantize(q, rounding=ROUND_HALF_UP):,}"


def percent(value, places=1) -> str:
    if value is None:
        return "No data"
    return f"{decimal(Decimal(str(value)) * 100, places)}%"


def compact(value) -> str:
    d = Decimal(str(value))
    if abs(d) >= 1_000_000:
        return f"{decimal(d / 1_000_000, 2)}M"
    if abs(d) >= 1_000:
        return f"{decimal(d / 1_000, 1)}K"
    return decimal(d, 2)


def safe_public_data(source):
    metrics, market, failures, coverage, raw = source
    accounts = {}
    for name in ACCOUNT_ORDER:
        m = metrics["accounts"][name]
        accounts[name] = {
            key: m[key]
            for key in (
                "trade_count", "buy_count", "sell_count", "observed_notional",
                "size_p25", "size_median", "size_p75", "notional_p25",
                "notional_median", "notional_p75", "median_interval_seconds",
                "hhi", "top_10_markets", "current_position_count",
                "bounded_closed_position_count", "burst_trade_count_5m",
                "burst_ratio_5m", "reversal_churn_10m_count",
                "classification_trade_counts", "metadata_resolved_trade_ratio",
            )
        }
    failed_rows = [{
        "source_request_id": row["source_request_id"],
        "account": row["account"],
        "endpoint": row["endpoint"],
        "http_status": row["http_status"],
        "classification": row["classification"],
        "coverage_impact": row["coverage_impact"],
    } for row in failures["requests"]]
    market_safe = {
        key: market[key] for key in (
            "requested_unique_condition_ids", "response_market_rows",
            "resolved_unique_requested_ids", "unresolved_unique_ids",
            "duplicate_response_rows", "unexpected_response_ids", "identity_holds",
            "legacy_summary_reconciliation",
        )
    }
    return {
        "schema_version": "phase0c-public-site-v1",
        "run_id": metrics["run_id"],
        "accounts": accounts,
        "account_order": ACCOUNT_ORDER,
        "account_pairs": metrics["account_pairs"],
        "all_five_common_condition_ids": metrics["all_five_common_condition_ids"],
        "coverage": coverage["accounts"],
        "bonereaper_closed_positions_reproduction": coverage["bonereaper_closed_positions_reproduction"],
        "market_coverage": market_safe,
        "failed_requests": {
            "failed_request_count": failures["failed_request_count"],
            "transient_recovered_count": failures["transient_recovered_count"],
            "terminal_gap_count": failures["terminal_gap_count"],
            "requests": failed_rows,
        },
        "raw_integrity": {
            key: raw[key] for key in (
                "passed_requests", "failed_requests", "referenced_raw_files",
                "verified_passed_raw_files", "integrity_error_count", "orphan_raw_count",
            )
        },
        "input_sha256": {
            name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in INPUTS.items()
        },
    }


def nav(active):
    links = [
        ("overview", "/", "Overview"),
        ("cross", "/cross-account/", "Cross-account"),
        ("coverage", "/data-coverage/", "Data coverage"),
        ("glossary", "/glossary/", "Glossary"),
    ]
    rendered = []
    for key, url, label in links:
        current = ' aria-current="page"' if key == active else ""
        rendered.append(f'<a href="{url}"{current}>{label}</a>')
    return '<button class="nav-toggle" aria-expanded="false">Menu</button><nav aria-label="Primary">' + "".join(rendered) + "</nav>"


def shell(title, active, body):
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)} · Phase 0C Research</title><link rel="stylesheet" href="/assets/site.css">
<script src="/assets/site.js" defer></script></head>
<body><header class="site-header"><a class="brand" href="/">POLYMARKET / RESEARCH</a>{nav(active)}</header>
<div class="status-banner"><strong>PRELIMINARY PARTIAL-COVERAGE MVP</strong><span>This is not full account history.</span></div>
<main>{body}</main><footer><span>Phase 0C bounded MVP · UTC · deterministic static build</span>
<span>Research signals are exploratory, not proof of insider trading, wash trading, or collusion.</span></footer>
</body></html>"""


def heading(kicker, title, text):
    return f'<section class="hero"><p class="kicker">{esc(kicker)}</p><h1>{esc(title)}</h1><p>{esc(text)}</p></section>'


def card(label, value, note="", raw=None):
    raw_attr = f' data-raw="{esc(raw)}"' if raw is not None else ""
    return f'<article class="metric"><span>{esc(label)}</span><strong{raw_attr}>{esc(value)}</strong><small>{esc(note)}</small></article>'


def table(headers, rows, sortable=False):
    attrs = ' class="data-table" data-sortable="true"' if sortable else ' class="data-table"'
    head = "".join(f"<th scope=\"col\">{esc(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows)
    return f'<div class="table-wrap"><table{attrs}><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def chart_meta(question, axes, unit, formula, sample, coverage, limitation):
    items = [
        ("Question", question), ("Axes", axes), ("Unit", unit), ("Formula", formula),
        ("Timezone", "UTC"), ("Sample", sample), ("Coverage", coverage),
        ("Source", "Phase 0C bounded normalized data"), ("Read as", "Descriptive comparison only"),
        ("Limitation", limitation),
    ]
    return '<dl class="chart-meta" data-chart-audit="complete">' + "".join(
        f"<div><dt>{esc(k)}</dt><dd>{esc(v)}</dd></div>" for k, v in items
    ) + "</dl>"


def bars(items, maximum=None):
    vals = [Decimal(str(v)) for _, v, *_ in items]
    maxv = Decimal(str(maximum)) if maximum is not None else max(vals or [1])
    output = []
    for item in items:
        label, value, *extra = item
        width = (Decimal(str(value)) / maxv * 100) if maxv else Decimal(0)
        note = extra[0] if extra else integer(value)
        output.append(
            f'<div class="bar-row"><span>{esc(label)}</span><div class="bar-track">'
            f'<i style="width:{width.quantize(Decimal(".01"))}%"></i></div>'
            f'<b data-raw="{esc(value)}">{esc(note)}</b></div>'
        )
    return '<div class="bar-chart">' + "".join(output) + "</div>"


def endpoint_badge(c):
    complete = str(c["window_completeness"]).startswith("complete")
    return f'<span class="badge {"ok" if complete else "warn"}">{esc(c["window_completeness"])}</span>'


def overview(data):
    rows = []
    for name in ACCOUNT_ORDER:
        m, cov = data["accounts"][name], data["coverage"][name]
        endpoint_summary = sum(1 for e in cov.values() if str(e["window_completeness"]).startswith("complete"))
        rows.append([
            f'<a href="/accounts/{SLUGS[name]}/">{esc(name)}</a>',
            integer(m["trade_count"]), integer(m["buy_count"]), integer(m["sell_count"]),
            f'<span data-raw="{esc(m["observed_notional"])}">{compact(m["observed_notional"])}</span>',
            f'<span data-raw="{esc(m["hhi"])}">{decimal(m["hhi"], 4)}</span>',
            f'{endpoint_summary}/4 endpoints; {sum(1 for e in cov.values() if e["cap_reached"])} capped',
        ])
    max_trades = max(data["accounts"][n]["trade_count"] for n in ACCOUNT_ORDER)
    body = heading("Bounded evidence, comparable cutoff", "Five-account research overview",
                   "A research-quality view of the existing bounded MVP. Coverage differs by endpoint and account; every result should be read with its cap and metadata boundary.")
    body += '<section><h2>Core comparison</h2>' + table(
        ["Account", "Trades", "BUY", "SELL", "Observed notional", "HHI", "Coverage"], rows, True
    ) + '<p class="method">Observed notional = Σ(size × price), in market quote units. It is not realized P&amp;L, deposits, or account wealth. HHI = Σ market notional share².</p></section>'
    body += '<section class="chart-card"><h2>Bounded trade observations</h2>' + bars([
        (n, data["accounts"][n]["trade_count"], integer(data["accounts"][n]["trade_count"])) for n in ACCOUNT_ORDER
    ], max_trades) + chart_meta(
        "How many normalized trades are in each bounded sample?", "X: normalized trade rows; Y: account",
        "trade rows", "count(normalized trades)", "Five fixed accounts",
        "30 days, expanded to 180 days when needed; per-account cap 20,000",
        "Bars are not activity rankings: three accounts are capped or otherwise partial."
    ) + "</section>"
    capped = [n for n in ACCOUNT_ORDER if data["coverage"][n]["trades"]["cap_reached"]]
    no_sells = [n for n in ACCOUNT_ORDER if data["accounts"][n]["sell_count"] == 0]
    lowest_metadata = min(ACCOUNT_ORDER, key=lambda n: Decimal(str(data["accounts"][n]["metadata_resolved_trade_ratio"])))
    overlapping = sum(1 for p in data["account_pairs"] if p["common_condition_ids"] > 0)
    body += f"""<section><h2>Principal findings</h2><div class="finding-grid">
<article><h3>Sampling dominates some comparisons</h3><p>{esc(", ".join(capped))} reached their configured trade caps. Their apparent frequencies and market mixes describe retained bounded observations, not complete histories.</p></article>
<article><h3>Direction profiles differ sharply</h3><p>{esc(", ".join(no_sells))} contain no observed SELL rows, while the other samples contain both sides. API semantics, strategy lifecycle and the bounded window are viable alternative explanations.</p></article>
<article><h3>Metadata quality is uneven</h3><p>{esc(lowest_metadata)} has the lowest resolved metadata ratio ({percent(data["accounts"][lowest_metadata]["metadata_resolved_trade_ratio"])}). Unknown is kept separate from non-crypto, so sector comparisons remain visibly incomplete.</p></article>
<article><h3>Cross-account overlap is sparse</h3><p>{integer(overlapping)} of {integer(len(data["account_pairs"]))} account pairs share conditionIds in the retained samples; timing matches are exploratory co-occurrence, not evidence of coordination.</p></article>
</div></section>"""
    return shell("Overview", "overview", body)


def account_page(name, data):
    m, cov = data["accounts"][name], data["coverage"][name]
    total = m["trade_count"]
    classes = m["classification_trade_counts"]
    class_items = [(k.replace("_", " ").title(), classes.get(k, 0), integer(classes.get(k, 0)))
                   for k in ("crypto", "non_crypto", "unknown")]
    top_rows = [[str(i), f'<code>{esc(row["condition_id"])}</code>',
                 f'<span data-raw="{esc(row["notional"])}">{decimal(row["notional"], 2)}</span>',
                 percent(row["share"], 2)] for i, row in enumerate(m["top_10_markets"], 1)]
    pair_rows = []
    for p in data["account_pairs"]:
        if name not in (p["left"], p["right"]):
            continue
        other = p["right"] if p["left"] == name else p["left"]
        pair_rows.append([esc(other), integer(p["common_condition_ids"]),
                          f'{integer(p["same_direction_5m"])} / {integer(p["opposite_direction_5m"])}',
                          f'{integer(p["same_direction_30m"])} / {integer(p["opposite_direction_30m"])}',
                          f'{integer(p["same_direction_60m"])} / {integer(p["opposite_direction_60m"])}'])
    body = heading("Account profile", name, "Descriptive statistics from the configured Phase 0C bounded windows; not full account history.")
    body += '<section class="metrics">' + "".join([
        card("Trades", integer(total), "normalized rows", total),
        card("BUY / SELL", f'{integer(m["buy_count"])} / {integer(m["sell_count"])}', "direction labels"),
        card("Observed notional", compact(m["observed_notional"]), "Σ(size × price), quote units", m["observed_notional"]),
        card("HHI", decimal(m["hhi"], 4), "notional-weighted", m["hhi"]),
        card("Median interval", f'{decimal(m["median_interval_seconds"], 1)} s', "between ordered trades", m["median_interval_seconds"]),
        card("Metadata resolved", percent(m["metadata_resolved_trade_ratio"]), "unknown remains separate", m["metadata_resolved_trade_ratio"]),
    ]) + "</section>"
    body += '<section class="two-col"><article class="chart-card"><h2>BUY / SELL observations</h2>' + bars([
        ("BUY", m["buy_count"], integer(m["buy_count"])), ("SELL", m["sell_count"], integer(m["sell_count"]))
    ], total or 1) + chart_meta("What directions appear in the bounded trade sample?", "X: trade rows; Y: side",
        "trade rows", "count(side)", integer(total), cov["trades"]["window_completeness"],
        "Direction labels do not establish opening/closing intent.") + '</article>'
    body += '<article class="chart-card"><h2>Market classification</h2>' + bars(class_items, total or 1) + chart_meta(
        "How many trades map to crypto, non-crypto, or unresolved metadata?", "X: trade rows; Y: classification",
        "trade rows", "count(classification)", integer(total), percent(m["metadata_resolved_trade_ratio"]) + " metadata resolved",
        "Unknown is never imputed as non-crypto; missing metadata can materially bias sector shares.") + "</article></section>"
    dist_rows = [
        ["Size", decimal(m["size_p25"], 4), decimal(m["size_median"], 4), decimal(m["size_p75"], 4), "contract/share units"],
        ["Trade notional", decimal(m["notional_p25"], 4), decimal(m["notional_median"], 4), decimal(m["notional_p75"], 4), "quote units"],
        ["Inter-trade interval", "—", decimal(m["median_interval_seconds"], 1), "—", "seconds, UTC ordering"],
    ]
    body += '<section><h2>Scale and interval distribution</h2>' + table(["Measure", "P25", "Median", "P75", "Unit"], dist_rows) + \
        '<p class="method">Quantiles use retained normalized observations. Observed notional is Σ(size × price); quote-unit comparability depends on API field semantics.</p></section>'
    body += '<section><h2>Top 10 markets by observed notional</h2>' + table(
        ["Rank", "conditionId", "Observed notional", "Share"], top_rows, True
    ) + f'<p class="method">HHI = Σᵢ wᵢ² where wᵢ is market i share of observed notional. HHI here is {esc(decimal(m["hhi"], 6))}; concentration is bounded-sample descriptive.</p></section>'
    body += '<section class="metrics">' + "".join([
        card("Current positions", integer(m["current_position_count"]), cov["positions"]["window_completeness"], m["current_position_count"]),
        card("Bounded closed positions", integer(m["bounded_closed_position_count"]), cov["closed_positions"]["stop_reason"], m["bounded_closed_position_count"]),
        card("5-minute burst rows", integer(m["burst_trade_count_5m"]), percent(m["burst_ratio_5m"]), m["burst_trade_count_5m"]),
        card("10-minute reversal/churn", integer(m["reversal_churn_10m_count"]), "heuristic matches", m["reversal_churn_10m_count"]),
    ]) + "</section>"
    body += '<section><h2>Cross-account timing matches</h2>' + table(
        ["Other account", "Common conditionIds", "5m same / opposite", "30m same / opposite", "60m same / opposite"], pair_rows
    ) + '<p class="method">Pairs require a common conditionId and timestamps within the stated UTC window. Repeated rows may create many matches; counts are not independent events.</p></section>'
    body += """<section class="boundary"><h2>Interpretation boundary</h2>
<p>Burst and reversal/churn labels are deterministic heuristics, not intent classifiers. Shared markets and close timestamps can arise from public news, common market-making incentives, API timestamp granularity, popular markets, or correlated strategies.</p>
<p>These observations cannot establish insider trading, wash trading, or collusion. Capped trades, bounded closed positions, unresolved market metadata, and snapshot endpoint semantics constrain every inference.</p></section>"""
    return shell(name, "", body)


def cross_page(data):
    rows = []
    for p in data["account_pairs"]:
        rows.append([esc(p["left"]), esc(p["right"]), integer(p["common_condition_ids"]),
                     integer(p["same_direction_5m"]), integer(p["opposite_direction_5m"]),
                     integer(p["same_direction_30m"]), integer(p["opposite_direction_30m"]),
                     integer(p["same_direction_60m"]), integer(p["opposite_direction_60m"])])
    style_rows = []
    for name in ACCOUNT_ORDER:
        m = data["accounts"][name]
        style_rows.append([f'<a href="/accounts/{SLUGS[name]}/">{esc(name)}</a>', percent(Decimal(m["buy_count"]) / Decimal(m["trade_count"]) if m["trade_count"] else 0),
                           decimal(m["median_interval_seconds"], 1), decimal(m["hhi"], 4),
                           percent(m["burst_ratio_5m"]), integer(m["reversal_churn_10m_count"])])
    body = heading("Cross-account", "Overlap, timing and trading style",
                   "Pairwise comparisons use the same retained bounded samples. Select a timing horizon without changing the underlying counts.")
    body += '<section><h2>Common markets and directional timing</h2><div class="window-controls" role="group" aria-label="Timing window"><button data-window="5" aria-pressed="true">5 min</button><button data-window="30" aria-pressed="false">30 min</button><button data-window="60" aria-pressed="false">60 min</button></div>' + table(
        ["Left", "Right", "Common conditionIds", "5m same", "5m opposite", "30m same", "30m opposite", "60m same", "60m opposite"], rows, True
    ) + '<p class="method">All-five common conditionIds: <strong>' + integer(data["all_five_common_condition_ids"]) + \
        '</strong>. A match is pairwise timestamp proximity on a shared conditionId, split by observed direction.</p></section>'
    body += '<section><h2>Concentration and style comparison</h2>' + table(
        ["Account", "BUY share", "Median interval (s)", "HHI", "5m burst ratio", "10m reversal/churn"], style_rows, True
    ) + '</section><section class="boundary"><h2>What this can—and cannot—say</h2><p>Overlap and timing are exploratory co-occurrence signals. Public information arrival, shared strategy constraints, market popularity, bots reacting to the same price move, and data duplication are plausible alternatives.</p><p>They are not evidence of insider trading, wash trading, or collusion. No causal or identity linkage is inferred.</p></section>'
    return shell("Cross-account", "cross", body)


def coverage_page(data):
    rows = []
    for name in ACCOUNT_ORDER:
        for endpoint in ("trades", "activity", "positions", "closed_positions"):
            c = data["coverage"][name][endpoint]
            window = f'{c["configured_window_start_utc"] or "snapshot"} → {c["configured_window_end_utc"] or "snapshot"}'
            observed = f'{c["observed_timestamp_min_utc"] or "No timestamp"} → {c["observed_timestamp_max_utc"] or "No timestamp"}'
            rows.append([esc(name), esc(endpoint), esc(window), esc(observed), integer(c["source_row_count"]),
                         integer(c["normalized_row_count"]), integer(c["record_cap"]),
                         "Yes" if c["cap_reached"] else "No", "Yes" if c["saturation"] else "No",
                         esc(c["stop_reason"]), endpoint_badge(c)])
    market = data["market_coverage"]
    raw = data["raw_integrity"]
    failures = data["failed_requests"]
    identity = f'{integer(market["requested_unique_condition_ids"])} = {integer(market["resolved_unique_requested_ids"])} + {integer(market["unresolved_unique_ids"])}'
    failure_rows = [[esc(r["source_request_id"]), esc(r["account"]), esc(r["endpoint"]), esc(r["http_status"]),
                     esc(r["classification"]), esc(r["coverage_impact"])] for r in failures["requests"]]
    repro = data["bonereaper_closed_positions_reproduction"]
    body = heading("Evidence audit", "Data coverage and integrity",
                   "Endpoint-level completeness, caps and immutable-evidence checks for the bounded MVP.")
    body += '<section><h2>Account × endpoint coverage</h2>' + table(
        ["Account", "Endpoint", "Configured window (UTC)", "Observed timestamps (UTC)", "Source rows",
         "Normalized rows", "Cap", "Cap reached", "Saturated", "Stop reason", "Window completeness"], rows, True
    ) + '<p class="method">“Complete” means complete within the configured bounded window only. It never means complete account history. Snapshot endpoints do not accept the common end cutoff.</p></section>'
    body += '<section class="metrics">' + "".join([
        card("Passed requests", integer(raw["passed_requests"]), "raw verified", raw["passed_requests"]),
        card("Recovered failures", integer(failures["transient_recovered_count"]), "terminal gaps: " + integer(failures["terminal_gap_count"]), failures["transient_recovered_count"]),
        card("Referenced raw", integer(raw["referenced_raw_files"]), "immutable evidence", raw["referenced_raw_files"]),
        card("Integrity errors", integer(raw["integrity_error_count"]), "gzip + SHA-256", raw["integrity_error_count"]),
        card("Orphan raw", integer(raw["orphan_raw_count"]), "retained, not deleted", raw["orphan_raw_count"]),
    ]) + "</section>"
    body += '<section><h2>Market metadata reconciliation</h2><div class="identity">' + esc(identity) + '</div>' + table(
        ["Requested unique", "Response rows", "Resolved unique requested", "Unresolved unique", "Duplicate response rows", "Unexpected response IDs"],
        [[integer(market["requested_unique_condition_ids"]), integer(market["response_market_rows"]),
          integer(market["resolved_unique_requested_ids"]), integer(market["unresolved_unique_ids"]),
          integer(market["duplicate_response_rows"]), integer(market["unexpected_response_ids"])]]
    ) + f'<p>{esc(market["legacy_summary_reconciliation"]["difference_of_40_reason"])}</p><p class="method">Identity verified: {esc(market["identity_holds"])}. Unresolved metadata remains unknown and can weaken sector, title and market-group analyses.</p></section>'
    body += '<section><h2>Sixteen recovered request failures</h2>' + table(
        ["Source request ID", "Account", "Endpoint", "HTTP", "Classification", "Coverage impact"], failure_rows, True
    ) + '<p class="method">All 16 are classified transient_recovered; terminal gaps = 0. Replacement evidence is recorded in the private audit, while this public-safe view omits URLs and raw paths.</p></section>'
    body += f'<section><h2>Bonereaper closed-position cap</h2><p>{esc(repro.get("explanation", canonical(repro)))}</p><pre>{esc(canonical(repro))}</pre></section>'
    return shell("Data coverage", "coverage", body)


def glossary_page():
    terms = [
        ("Bounded window", "The configured time or row-limited slice analyzed; never the full account history."),
        ("Observed notional", "The sum of size × price in API quote units; not P&L or wealth."),
        ("HHI", "Σ market notional share². Higher values mean more concentrated observed notional."),
        ("Saturation", "A page/window hit its API page-size boundary and may require subdivision."),
        ("Burst", "A descriptive short-interval heuristic over retained trades, not an intent label."),
        ("Reversal/churn", "Opposite observed directions close in time under a fixed heuristic."),
        ("Unknown metadata", "A conditionId without resolved market metadata; never treated as non-crypto."),
    ]
    body = heading("Methods", "Glossary and reading guide", "Definitions used consistently across all pages.")
    body += '<section class="glossary">' + "".join(
        f'<article tabindex="0"><h2>{esc(term)}</h2><p>{esc(defn)}</p></article>' for term, defn in terms
    ) + '</section>'
    return shell("Glossary", "glossary", body)


def main():
    source = tuple(load(INPUTS[name]) for name in INPUTS)
    data = safe_public_data(source)
    write(PUBLIC / "data/site-data.json", canonical(data) + "\n")
    write(PUBLIC / "index.html", overview(data))
    for name in ACCOUNT_ORDER:
        write(PUBLIC / "accounts" / SLUGS[name] / "index.html", account_page(name, data))
    write(PUBLIC / "cross-account/index.html", cross_page(data))
    write(PUBLIC / "data-coverage/index.html", coverage_page(data))
    write(PUBLIC / "glossary/index.html", glossary_page())
    for asset in ("site.css", "site.js"):
        write(PUBLIC / "assets" / asset, (ROOT / "web" / asset).read_text(encoding="utf-8"))
    print(f"Built {len(ACCOUNT_ORDER) + 4} HTML pages and public/data/site-data.json")


if __name__ == "__main__":
    main()
