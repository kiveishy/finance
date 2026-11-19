// Market bar: a strip under the title bar with the major indexes, currencies, crypto and commodities.
// Each item opens a chart of that instrument in a modal window.
import { el, formatNumber, icon } from "./ui.js";

const LWC = window.LightweightCharts;
const RANGE_KEY = "owl.marketChartRange";
const RANGES = ["1M", "3M", "6M", "1Y", "2Y", "5Y", "ALL"];

/** Yahoo-style symbols the aggregator understands. digits: price decimals; unit: suffix shown after the price. */
export const MARKET_ITEMS = [
    { symbol: "^GSPC", name: "S&P 500", kind: "US index" },
    { symbol: "^IXIC", name: "Nasdaq", kind: "US index" },
    { symbol: "^DJI", name: "Dow Jones", kind: "US index" },
    { symbol: "^RUT", name: "Russell 2000", kind: "US index" },
    { symbol: "^VIX", name: "VIX", kind: "Volatility index" },
    { symbol: "TA35.TA", name: "TA-35", kind: "Tel Aviv index" },
    { symbol: "^TA125.TA", name: "TA-125", kind: "Tel Aviv index" },
    { symbol: "ILS=X", name: "USD/ILS", kind: "Currency", digits: 4 },
    { symbol: "EURILS=X", name: "EUR/ILS", kind: "Currency", digits: 4 },
    { symbol: "EURUSD=X", name: "EUR/USD", kind: "Currency", digits: 4 },
    { symbol: "DX-Y.NYB", name: "Dollar index", kind: "Currency index" },
    { symbol: "BTC-USD", name: "BTC/USD", kind: "Crypto", digits: 0 },
    { symbol: "ETH-USD", name: "ETH/USD", kind: "Crypto" },
    { symbol: "CL=F", name: "Oil WTI", kind: "Commodity (futures)" },
    { symbol: "BZ=F", name: "Oil Brent", kind: "Commodity (futures)" },
    { symbol: "NG=F", name: "Natural gas", kind: "Commodity (futures)", digits: 3 },
    { symbol: "GC=F", name: "Gold", kind: "Commodity (futures)" },
    { symbol: "SI=F", name: "Silver", kind: "Commodity (futures)" },
    { symbol: "^TNX", name: "US 10Y", kind: "Treasury yield", digits: 3, unit: "%" },
    { symbol: "^FTSE", name: "FTSE 100", kind: "UK index" },
    { symbol: "^GDAXI", name: "DAX", kind: "German index" },
    { symbol: "^N225", name: "Nikkei 225", kind: "Japan index" },
];

const signed = (value, digits = 2) => `${value >= 0 ? "+" : ""}${formatNumber(value, digits)}`;
const tone = (value) => (value > 0 ? "up" : value < 0 ? "down" : "");
const priceText = (item, value) => `${formatNumber(value, item.digits ?? 2)}${item.unit ?? ""}`;

export class MarketBar {
    /** options: {api, canLoad() -> bool, onOpen(symbol), onAdd(symbol)} */
    constructor(root, options) {
        this.root = root;
        this.options = options;
        this.quotes = {};
        this.loading = false;
        this.buttons = new Map();
        this.track = el("div", { class: "market-track" });
        for (const item of MARKET_ITEMS) {
            const button = el("button", { class: "market-item", onclick: () => this.showChart(item) });
            this.buttons.set(item.symbol, button);
            this.track.append(button);
            this.renderItem(item);
        }
        root.replaceChildren(this.track);
        // A mouse wheel scrolls the strip sideways.
        root.addEventListener("wheel", (event) => {
            if (Math.abs(event.deltaY) <= Math.abs(event.deltaX)) return;
            root.scrollLeft += event.deltaY;
            event.preventDefault();
        }, { passive: false });
    }

    async refresh() {
        if (this.loading || this.root.hidden || !this.options.canLoad()) return;
        this.loading = true;
        try {
            this.quotes = { ...this.quotes, ...await this.options.api.quotes(MARKET_ITEMS.map((i) => i.symbol)) };
            MARKET_ITEMS.forEach((item) => this.renderItem(item));
        } catch {
            /* optional; the status bar reports connection problems */
        } finally {
            this.loading = false;
        }
    }

    renderItem(item) {
        const quote = this.quotes[item.symbol];
        const button = this.buttons.get(item.symbol);
        const change = quote?.change_percent;
        button.replaceChildren(
            el("span", { class: "market-name" }, item.name),
            el("span", { class: "market-price" }, quote ? priceText(item, quote.close) : "…"),
            quote && change !== null && change !== undefined
                ? el("span", { class: `market-change ${tone(change)}` }, `${signed(change)}%`) : null,
        );
        button.title = quote
            ? `${item.name} (${item.symbol}) · ${item.kind}\n${priceText(item, quote.close)}  ${signed(quote.change ?? 0, item.digits ?? 2)} (${signed(change ?? 0)}%)\nClose of ${quote.date} · ${quote.source}\nClick for the chart`
            : `${item.name} (${item.symbol}) · ${item.kind}\nClick for the chart`;
    }

    showChart(item) {
        showMarketChart(item, this.options, this.quotes[item.symbol]);
    }
}

/** Modal window with a price chart of one market item. */
export function showMarketChart(item, { api, onOpen, onAdd }, quote = null) {
    let range = localStorage.getItem(RANGE_KEY);
    if (!RANGES.includes(range)) range = "6M";
    let request = 0;
    let chart = null;

    const header = el("div", { class: "market-modal-quote" });
    const legend = el("div", { class: "market-modal-legend" });
    const message = el("div", { class: "market-modal-message" }, "Loading…");
    const container = el("div", { class: "market-modal-chart" }, legend, message);
    const rangeButtons = RANGES.map((r) => el("button", {
        class: "segment", "data-range": r, onclick: () => { range = r; localStorage.setItem(RANGE_KEY, r); load(); },
    }, r));
    const close = () => {
        request += 1;
        chart?.remove();
        overlay.remove();
        document.removeEventListener("keydown", onKey, true);
    };
    const onKey = (event) => {
        if (event.key !== "Escape") return;
        event.preventDefault();
        event.stopPropagation();
        close();
    };
    const action = (label, iconName, run, title) => el("button", {
        class: "banner-button secondary", title, onclick: () => { close(); run(item.symbol); },
    }, icon(iconName), ` ${label}`);
    const dialog = el("div", { class: "form-dialog market-modal", role: "dialog", "aria-label": `${item.name} chart` },
        el("div", { class: "form-title market-modal-title" },
            el("span", {}, item.name),
            el("span", { class: "market-modal-symbol" }, `${item.symbol} · ${item.kind}`),
            el("div", { class: "toolbar-spacer" }),
            el("button", { class: "icon-button", title: "Close (Esc)", onclick: close }, icon("close"))),
        header,
        el("div", { class: "segmented" }, rangeButtons),
        container,
        el("div", { class: "form-buttons" },
            action("Add to watchlist", "add", onAdd, `Add ${item.symbol} to a watchlist group`),
            action("Open in editor", "go-to-file", onOpen, "Open the full chart (indicators, drawings) as a tab")));
    const overlay = el("div", { class: "form-overlay", onmousedown: (e) => { if (e.target === overlay) close(); } }, dialog);
    document.addEventListener("keydown", onKey, true);
    document.body.append(overlay);

    const renderHeader = (q) => {
        if (!q) return;
        header.replaceChildren(
            el("span", { class: "market-modal-price" }, priceText(item, q.close)),
            el("span", { class: `market-change ${tone(q.change)}` },
                `${signed(q.change ?? 0, item.digits ?? 2)} (${signed(q.change_percent ?? 0)}%)`),
            el("span", { class: "market-modal-date" }, `close of ${q.date}`));
    };
    renderHeader(quote);

    chart = LWC.createChart(container, {
        autoSize: true,
        layout: {
            background: { type: LWC.ColorType.Solid, color: "#1e1e1e" },
            textColor: "#b2b5be",
            fontSize: 11,
            fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif",
            attributionLogo: true,
        },
        grid: { vertLines: { color: "rgba(42, 46, 57, 0.6)" }, horzLines: { color: "rgba(42, 46, 57, 0.6)" } },
        crosshair: {
            mode: LWC.CrosshairMode.Normal,
            vertLine: { color: "#758696", labelBackgroundColor: "#2a2e39" },
            horzLine: { color: "#758696", labelBackgroundColor: "#2a2e39" },
        },
        rightPriceScale: { borderColor: "#2b2b2b", scaleMargins: { top: 0.12, bottom: 0.05 } },
        timeScale: { borderColor: "#2b2b2b", rightOffset: 2 },
        localization: { priceFormatter: (p) => priceText(item, p) },
    });
    const series = chart.addSeries(LWC.AreaSeries, { lineWidth: 2, priceLineVisible: false });
    let points = [];
    const renderLegend = (time) => {
        const point = (time && points.find((p) => p.time === time)) || points.at(-1);
        if (!point) { legend.replaceChildren(); return; }
        const first = points[0].value;
        const change = first ? ((point.value - first) / first) * 100 : 0;
        legend.replaceChildren(
            el("span", {}, point.time), " ",
            el("span", { class: "v" }, priceText(item, point.value)), " ",
            el("span", { class: `market-change ${tone(change)}` }, `${signed(change)}% since ${points[0].time}`));
    };
    chart.subscribeCrosshairMove((param) => renderLegend(param.time));

    async function load() {
        const id = ++request;
        rangeButtons.forEach((b) => b.classList.toggle("active", b.dataset.range === range));
        message.hidden = false;
        message.textContent = "Loading…";
        try {
            const payload = await api.chart(item.symbol, { range, interval: "", indicators: [] });
            if (id !== request) return;
            points = payload.bars
                .filter((b) => b.time >= payload.visible_from && b.close !== null && b.close !== undefined)
                .map((b) => ({ time: b.time, value: b.close }));
            if (!points.length) {
                message.textContent = "No data for this range";
                series.setData([]);
                legend.replaceChildren();
                return;
            }
            const up = points.at(-1).value >= points[0].value;
            const color = up ? "#26a69a" : "#ef5350";
            series.applyOptions({ lineColor: color, topColor: up ? "rgba(38, 166, 154, 0.35)" : "rgba(239, 83, 80, 0.35)", bottomColor: "rgba(0, 0, 0, 0)" });
            series.setData(points);
            chart.timeScale().fitContent();
            renderHeader(payload.quote);
            renderLegend(null);
            message.hidden = true;
        } catch (err) {
            if (id === request) message.textContent = `Loading the chart failed: ${err.message}`;
        }
    }
    load();
    return close;
}
