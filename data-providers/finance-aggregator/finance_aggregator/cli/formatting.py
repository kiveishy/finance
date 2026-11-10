"""Plain-text rendering helpers for CLI output."""

from __future__ import annotations

from typing import Any, Sequence

from ..core.models import PriceHistory, ServiceStatus, TickerInfo
from ..core.search import SymbolMatch


def format_table(headers: Sequence[str], rows: Sequence[Sequence[Any]], *, align_right: Sequence[bool] | None = None) -> str:
    cells = [[_cell(v) for v in row] for row in rows]
    widths = [len(h) for h in headers]
    for row in cells:
        for i, value in enumerate(row):
            widths[i] = max(widths[i], len(value))
    right = align_right or [False] * len(headers)

    def line(values: Sequence[str]) -> str:
        return "  ".join(v.rjust(w) if r else v.ljust(w) for v, w, r in zip(values, widths, right)).rstrip()

    output = [line(headers), line(["-" * w for w in widths])]
    output.extend(line(row) for row in cells)
    return "\n".join(output)


def _cell(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:,.2f}"
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)


def humanize_number(value: float | None) -> str:
    if value is None:
        return "-"
    for threshold, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(value) >= threshold:
            return f"{value / threshold:,.2f}{suffix}"
    return f"{value:,.2f}"


def render_ticker_info(info: TickerInfo, *, description_width: int = 300) -> str:
    description = info.description or "-"
    if len(description) > description_width:
        description = description[: description_width - 3].rstrip() + "..."
    fields = [
        ("Symbol", info.symbol),
        ("Name", info.display_name),
        ("Type", info.quote_type),
        ("Exchange", info.exchange),
        ("Currency", info.currency),
        ("Sector", info.sector),
        ("Industry", info.industry),
        ("Country", info.country),
        ("Market cap", humanize_number(info.market_cap)),
        ("Employees", f"{info.employees:,}" if info.employees is not None else None),
        ("Website", info.website),
        ("Description", description),
        ("Source", info.source),
    ]
    width = max(len(label) for label, _ in fields)
    return "\n".join(f"  {label.ljust(width)} : {value if value is not None else '-'}" for label, value in fields)


def render_history(history: PriceHistory, *, limit: int = 0) -> str:
    bars = history.bars[-limit:] if limit > 0 else history.bars
    table = format_table(
        ["Date", "Open", "High", "Low", "Close", "Adj Close", "Volume", "Source"],
        [(b.date.isoformat(), b.open, b.high, b.low, b.close, b.adj_close, b.volume, b.source) for b in bars],
        align_right=[False] + [True] * 6 + [False],
    )
    sources = ", ".join(f"{name} ({count} bars)" for name, count in history.source_counts.items())
    header = (
        f"  {history.symbol} {history.date_range}: {len(history.bars)} bars "
        f"({history.first_date} .. {history.last_date}), sources: {sources}"
    )
    if len(bars) < len(history.bars):
        header += f"\n  showing last {len(bars)} rows (use --limit 0 to show all)"
    return f"{header}\n{_indent(table)}"


def render_status(status: ServiceStatus) -> str:
    rows = [
        (i + 1, p.name, p.kind, p.state.value.upper(), f"{p.latency_ms:.0f} ms" if p.latency_ms is not None else "-", p.detail)
        for i, p in enumerate(status.providers)
    ]
    table = format_table(["#", "Provider", "Type", "State", "Latency", "Detail"], rows)
    return f"  Service {status.name}: {status.state.value.upper()} - {status.detail}\n\n{_indent(table)}"


def render_matches(matches: Sequence[SymbolMatch]) -> str:
    table = format_table(
        ["#", "Symbol", "Name", "Exchange", "Type", "Score", "Source"],
        [(i + 1, m.symbol, m.name, m.exchange, m.quote_type, f"{m.score:.0f}", m.source) for i, m in enumerate(matches)],
    )
    return _indent(table)


def _indent(text: str, prefix: str = "  ") -> str:
    return "\n".join(prefix + line for line in text.splitlines())
