// TradingView-style chart built on TradingView Lightweight Charts (vendored, Apache-2.0).
import { el, formatNumber, formatVolume } from "./ui.js";

const LWC = window.LightweightCharts;
const UP = "#26a69a";
const DOWN = "#ef5350";
const MIN_VISIBLE_BARS = 3;

export const DEFAULT_INDICATORS = [
    { type: "sma", period: 20, color: "#2962ff", enabled: true },
    { type: "sma", period: 50, color: "#ff9800", enabled: true },
    { type: "sma", period: 200, color: "#e91e63", enabled: true },
];
export const INDICATOR_COLORS = ["#2962ff", "#ff9800", "#e91e63", "#00bcd4", "#9c27b0", "#ffeb3b", "#4caf50", "#f44336"];

export const indicatorKey = (indicator) => `${indicator.type}:${indicator.period}`;
export const indicatorLabel = (indicator) => `${indicator.type === "sma" ? "MA" : indicator.type.toUpperCase()} ${indicator.period}`;

export class ChartView {
    constructor(container, legend) {
        this.container = container;
        this.legend = legend;
        this.payload = null;
        this.lines = new Map(); // key -> {series, color, label, values: Map(time -> value)}
        this.chart = LWC.createChart(container, {
            autoSize: true,
            layout: {
                background: { type: LWC.ColorType.Solid, color: "#1e1e1e" },
                textColor: "#b2b5be",
                fontSize: 11,
                fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif",
                attributionLogo: true,
                panes: { separatorColor: "#2b2b2b", separatorHoverColor: "rgba(0, 122, 204, 0.4)", enableResize: true },
            },
            grid: {
                vertLines: { color: "rgba(42, 46, 57, 0.6)" },
                horzLines: { color: "rgba(42, 46, 57, 0.6)" },
            },
            crosshair: {
                mode: LWC.CrosshairMode.Normal,
                vertLine: { color: "#758696", labelBackgroundColor: "#2a2e39" },
                horzLine: { color: "#758696", labelBackgroundColor: "#2a2e39" },
            },
            rightPriceScale: { borderColor: "#2b2b2b", scaleMargins: { top: 0.08, bottom: 0.05 } },
            timeScale: { borderColor: "#2b2b2b", rightOffset: 5, barSpacing: 8, minBarSpacing: 0.5 },
        });
        this.candles = this.chart.addSeries(LWC.CandlestickSeries, {
            upColor: UP, downColor: DOWN, borderVisible: false, wickUpColor: UP, wickDownColor: DOWN,
        });
        // Volume lives in its own pane below the price pane (pane index 1).
        this.volume = this.chart.addSeries(LWC.HistogramSeries, {
            priceFormat: { type: "volume" },
            lastValueVisible: false,
            priceLineVisible: false,
        }, 1);
        this.chart.panes()[0].setStretchFactor(4);
        this.chart.panes()[1].setStretchFactor(1);
        this.chart.subscribeCrosshairMove((param) => this.renderLegend(param.time));
    }

    setData(payload, indicatorSettings) {
        this.payload = payload;
        this.barsByTime = new Map(payload.bars.map((bar, index) => [bar.time, { ...bar, index }]));
        this.candles.setData(payload.bars.map(({ time, open, high, low, close }) => ({ time, open, high, low, close })));
        this.volume.setData(payload.bars.map((bar) => ({
            time: bar.time,
            value: bar.volume ?? 0,
            color: bar.close >= bar.open ? "rgba(38, 166, 154, 0.5)" : "rgba(239, 83, 80, 0.5)",
        })));
        for (const line of this.lines.values()) this.chart.removeSeries(line.series);
        this.lines.clear();
        const colors = new Map(indicatorSettings.map((i) => [indicatorKey(i), i.color]));
        for (const indicator of payload.indicators) {
            const color = colors.get(indicator.key) || "#ffffff";
            const series = this.chart.addSeries(LWC.LineSeries, {
                color,
                lineWidth: 1.5,
                priceLineVisible: false,
                lastValueVisible: false,
                crosshairMarkerVisible: false,
            });
            series.setData(indicator.values);
            this.lines.set(indicator.key, {
                series, color, label: indicator.label, values: new Map(indicator.values.map((v) => [v.time, v.value])),
            });
        }
        this.resetZoom();
        this.renderLegend(null);
    }

    resetZoom() {
        if (!this.payload || !this.payload.bars.length) return;
        // Dragging/scrolling a price axis turns auto scale off; a new chart (e.g. 700 -> 20) must fit again.
        this.candles.priceScale().applyOptions({ autoScale: true });
        this.volume.priceScale().applyOptions({ autoScale: true });
        const last = this.payload.bars.length - 1;
        const fromIndex = this.payload.bars.findIndex((bar) => bar.time >= this.payload.visible_from);
        const from = Math.min(fromIndex < 0 ? 0 : fromIndex, last - MIN_VISIBLE_BARS + 1);
        this.chart.timeScale().setVisibleLogicalRange({ from: from - 0.5, to: last + 5 });
    }

    zoom(factor) {
        const timeScale = this.chart.timeScale();
        const range = timeScale.getVisibleLogicalRange();
        if (!range) return;
        // Anchor at the right edge like TradingView, so the latest bars stay in view.
        const width = Math.max((range.to - range.from) * factor, MIN_VISIBLE_BARS);
        timeScale.setVisibleLogicalRange({ from: range.to - width, to: range.to });
    }

    renderLegend(time) {
        if (!this.payload || !this.payload.bars.length) {
            this.legend.replaceChildren();
            return;
        }
        const bar = (time && this.barsByTime.get(time)) || this.barsByTime.get(this.payload.bars.at(-1).time);
        const previous = bar.index > 0 ? this.payload.bars[bar.index - 1] : null;
        const change = previous ? bar.close - previous.close : null;
        const changePct = previous && previous.close ? (change / previous.close) * 100 : null;
        const tone = bar.close >= bar.open ? "up" : "down";
        const value = (label, text) => [el("span", { class: "k" }, label), el("span", { class: `v ${tone}` }, text)];
        const lines = [
            el("div", {},
                el("span", { class: "legend-title" }, `${this.payload.symbol} · ${this.payload.interval}`),
                value("O", formatNumber(bar.open)), value("H", formatNumber(bar.high)),
                value("L", formatNumber(bar.low)), value("C", formatNumber(bar.close)),
                change === null ? null : el("span", { class: `v ${change >= 0 ? "up" : "down"}` },
                    ` ${change >= 0 ? "+" : ""}${formatNumber(change)} (${change >= 0 ? "+" : ""}${formatNumber(changePct)}%)`),
                el("span", { class: "k" }, "Vol"), el("span", { class: "v" }, formatVolume(bar.volume)),
                el("span", { class: "src" }, bar.time, " · ", bar.source || "?")),
        ];
        for (const line of this.lines.values()) {
            const v = line.values.get(bar.time);
            lines.push(el("div", {},
                el("span", { class: "k", style: `color:${line.color}` }, line.label),
                el("span", { class: "v", style: `color:${line.color}` }, v === undefined ? " —" : ` ${formatNumber(v)}`)));
        }
        this.legend.replaceChildren(...lines);
    }

    dispose() {
        this.chart.remove();
    }
}
