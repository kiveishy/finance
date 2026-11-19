// Drawings on the price pane: trend lines, arrows (click start, click end) and comment bubbles (click the
// point, type the text). Select, move, stretch an end, edit a comment, delete.
// Points are anchored to (date, price), so drawings survive zooming, scrolling and D/W/M interval changes.
// Stored shape: {id, type: "line" | "arrow" | "note" | "trade", a, b, text?}; a missing type means "line" (older data).
// For a note or trade, `a` is the point the bubble points to and `b` the bubble centre.
// A trade is {side: "buy" | "sell", amount, price} or {side: "dividend", amount (cash received), tax?, price (close
// of that day, where the label points)}.

const DAY_MS = 86_400_000;
const LINE_COLOR = "#2962ff";
const LINE_WIDTH = 2;
const ARROW_HEAD = 12;
const HANDLE_RADIUS = 5;
const HIT_TOLERANCE = 6;
const NOTE = {
    font: '12px "Segoe UI", system-ui, sans-serif',
    lineHeight: 16,
    padding: 8,
    maxWidth: 240,
    fill: "#252526",
    border: "#d7ba7d",
    text: "#e0e0e0",
    tailWidth: 8,
};
export const MAX_DRAWINGS_PER_SYMBOL = 100;
export const MAX_NOTE_LENGTH = 500;
export const DRAWING_TYPES = ["line", "arrow", "note", "trade"];
const MODES = ["line", "arrow", "note"];
const TRADE_STYLE = {
    buy: { label: "BUY", fill: "#0d3b33", border: "#26a69a", text: "#ffffff" },
    sell: { label: "SELL", fill: "#4a1d1f", border: "#ef5350", text: "#ffffff" },
    dividend: { label: "DIVIDEND", fill: "#0f2d4a", border: "#42a5f5", text: "#ffffff" },
};
export const TRADE_SIDES = Object.keys(TRADE_STYLE);

const dayOf = (iso) => Date.parse(`${iso}T00:00:00Z`) / DAY_MS;
/** "YYYY-MM-DD" of a stored day number. */
export const isoOf = (day) => new Date(Math.round(day) * DAY_MS).toISOString().slice(0, 10);
const round = (value, digits) => Number(value.toFixed(digits));
const typeOf = (drawing) => drawing.type ?? "line";
const isBubble = (drawing) => typeOf(drawing) === "note" || typeOf(drawing) === "trade";
/** Label category that can be hidden: "note" (comments), "trade" (BUY / SELL), "dividend"; null for lines and arrows. */
export const labelCategory = (drawing) => {
    if (typeOf(drawing) === "note") return "note";
    if (typeOf(drawing) !== "trade") return null;
    return drawing.side === "dividend" ? "dividend" : "trade";
};
export const LABEL_CATEGORIES = ["note", "trade", "dividend"];
let nextId = Date.now();
const newId = () => `l${(nextId++).toString(36)}`;

const formatAmount = (value) => value.toLocaleString("en-US", { maximumFractionDigits: 8 });
const formatPrice = (value) => value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: value < 1 ? 8 : 2 });
/** A clicked price rounded the way prices are quoted: cents, or 4 significant digits below 1. */
export const roundPrice = (price) => (price >= 1 ? round(price, 2) : Number(price.toPrecision(4)));

export function tradeText(trade) {
    const { label } = TRADE_STYLE[trade.side];
    if (trade.side === "dividend") {
        const tax = trade.tax ? ` · tax ${formatPrice(trade.tax)}` : "";
        return `${label} ${formatPrice(trade.amount)}\n${isoOf(trade.a.day)}${tax}`;
    }
    const total = `Total ${formatPrice(trade.amount * trade.price)}`;
    return `${label} ${formatAmount(trade.amount)} @ ${formatPrice(trade.price)}\n${isoOf(trade.a.day)} · ${total}`;
}

/**
 * A trade drawing built without a chart on screen (e.g. imported). `date` is "YYYY-MM-DD"; for a dividend `price` is
 * where the label points (that day's close). It is marked `auto`: the first time its day is on screen, its bubble is
 * placed next to the tip where it doesn't cover other bubbles (until then it sits 5% below a BUY, 5% above a SELL
 * and 8% above a DIVIDEND).
 */
export function buildTrade(side, { amount, price, date, tax = 0 }) {
    const trade = { id: newId(), type: "trade", side, price, amount, a: { day: dayOf(date), price }, auto: true };
    if (side === "dividend" && tax > 0) trade.tax = tax;
    const offset = { buy: 0.95, sell: 1.05, dividend: 1.08 }[side];
    trade.b = { day: trade.a.day, price: Number((price * offset).toPrecision(8)) };
    return trade;
}

const overlaps = (r, s) => r.x < s.x + s.w && s.x < r.x + r.w && r.y < s.y + s.h && s.y < r.y + r.h;

/** Whether two trades record the same action (used to skip duplicates when importing). */
export function sameTrade(x, y) {
    const close = (p, q) => Math.abs(p - q) <= 1e-6 * Math.max(1, Math.abs(p));
    if (typeOf(x) !== "trade" || typeOf(y) !== "trade" || x.side !== y.side) return false;
    if (Math.round(x.a.day) !== Math.round(y.a.day) || !close(x.amount, y.amount)) return false;
    return x.side === "dividend" || close(x.price, y.price);
}

export { dayOf };

const bubbleText = (drawing) => (typeOf(drawing) === "trade" ? tradeText(drawing) : drawing.text);

let measureContext = null;
const wrapCache = new Map();

/** The comment split into lines that fit NOTE.maxWidth, and the widest line's width. */
function wrapText(text) {
    let wrapped = wrapCache.get(text);
    if (wrapped) return wrapped;
    measureContext ??= document.createElement("canvas").getContext("2d");
    measureContext.font = NOTE.font;
    const width = (s) => measureContext.measureText(s).width;
    const lines = [];
    for (const paragraph of text.split("\n")) {
        let line = "";
        for (const word of paragraph.split(/\s+/).filter(Boolean)) {
            const candidate = line ? `${line} ${word}` : word;
            if (width(candidate) <= NOTE.maxWidth) {
                line = candidate;
                continue;
            }
            if (line) lines.push(line);
            line = word;
            while (width(line) > NOTE.maxWidth) { // a single word longer than a line
                let cut = line.length - 1;
                while (cut > 1 && width(line.slice(0, cut)) > NOTE.maxWidth) cut--;
                lines.push(line.slice(0, cut));
                line = line.slice(cut);
            }
        }
        lines.push(line);
    }
    wrapped = { lines, width: Math.max(...lines.map(width)) };
    if (wrapCache.size > 500) wrapCache.clear();
    wrapCache.set(text, wrapped);
    return wrapped;
}

/** Maps calendar days to fractional bar indexes (logical) and back, extrapolating past the first/last bar. */
class TimeIndex {
    constructor(bars) {
        this.days = bars.map((bar) => dayOf(bar.time));
        this.closes = bars.map((bar) => bar.close);
        const n = this.days.length;
        const k = Math.min(20, n - 1);
        this.startStep = k > 0 ? (this.days[k] - this.days[0]) / k : 1;
        this.endStep = k > 0 ? (this.days[n - 1] - this.days[n - 1 - k]) / k : 1;
    }

    toLogical(day) {
        const days = this.days;
        const last = days.length - 1;
        if (day <= days[0]) return (day - days[0]) / this.startStep;
        if (day >= days[last]) return last + (day - days[last]) / this.endStep;
        let lo = 0;
        let hi = last;
        while (hi - lo > 1) {
            const mid = (lo + hi) >> 1;
            if (days[mid] <= day) lo = mid;
            else hi = mid;
        }
        return lo + (day - days[lo]) / (days[hi] - days[lo]);
    }

    toDay(logical) {
        const days = this.days;
        const last = days.length - 1;
        if (logical <= 0) return days[0] + logical * this.startStep;
        if (logical >= last) return days[last] + (logical - last) * this.endStep;
        const i = Math.floor(logical);
        return days[i] + (logical - i) * (days[i + 1] - days[i]);
    }
}

function distanceToSegment(p, a, b) {
    const dx = b.x - a.x;
    const dy = b.y - a.y;
    const lengthSq = dx * dx + dy * dy;
    const t = lengthSq ? Math.max(0, Math.min(1, ((p.x - a.x) * dx + (p.y - a.y) * dy) / lengthSq)) : 0;
    return Math.hypot(p.x - (a.x + t * dx), p.y - (a.y + t * dy));
}

function pointInRect(p, r) {
    return p.x >= r.x && p.x <= r.x + r.w && p.y >= r.y && p.y <= r.y + r.h;
}

export class ChartDrawings {
    /**
     * handlers: onChange(drawings) after a drawing is created, moved, stretched, edited or deleted;
     *           onState() when the mode or selection change; requestText(initial) -> Promise<string | null> for comments;
     *           requestTrade(side, {price, amount, date}) -> Promise<{price, amount, date} | null> to edit a BUY / SELL label
     */
    constructor(view, handlers) {
        this.view = view;
        this.handlers = handlers;
        // all drawings of the symbol: {id, type, a: {day, price}, b: {day, price}, text?, side?, price?, amount?}
        this.lines = [];
        this.index = null;
        this.selectedId = null;
        this.mode = null; // "line" | "arrow" | "note" while creating, else null
        this.draft = null; // line/arrow being drawn: its end follows the pointer
        this.pendingLabel = null; // {mode, anchor} of a clicked comment point; its text is asked for on pointer up
        this.pendingEdit = null; // id of a double-clicked comment/trade, edited on pointer up
        this.lastPress = null; // {id, time} of the last press on a drawing, to detect double-clicks
        this.drag = null; // {id, part: "a" | "b" | "line" | "body", start: point, original: drawing}
        this.points = new Map(); // id -> {a, b, rect?, lines?} in pane coordinates, refreshed on every chart update
        this.requestUpdate = () => {};
        this.hidden = new Set(); // label categories not shown (see labelCategory)
        view.candles.attachPrimitive(this.primitive());
        this.bindEvents();
    }

    get drawing() {
        return this.mode !== null;
    }

    isVisible(drawing) {
        return !this.hidden.has(labelCategory(drawing));
    }

    /** Hides the labels of the given categories ("note", "trade", "dividend") and shows the others. */
    setHidden(categories) {
        const next = new Set(categories);
        if ([...next].join() === [...this.hidden].join()) return;
        this.hidden = next;
        if (this.selected && !this.isVisible(this.selected)) this.selectedId = null;
        this.requestUpdate();
        this.handlers.onState?.();
    }

    get selected() {
        return this.lines.find((line) => line.id === this.selectedId) ?? null;
    }

    setBars(bars) {
        this.index = bars.length ? new TimeIndex(bars) : null;
        this.requestUpdate();
    }

    setLines(lines) {
        this.lines = Array.isArray(lines) ? lines.filter(isValidDrawing).map((line) => structuredClone(line)) : [];
        this.selectedId = null;
        this.pendingLabel = null;
        this.cancelDrawing();
        this.requestUpdate();
        this.handlers.onState?.();
    }

    startDrawing(type = "line") {
        if (!MODES.includes(type) || this.lines.length >= MAX_DRAWINGS_PER_SYMBOL) return false;
        this.draft = null;
        this.mode = type;
        this.selectedId = null;
        this.view.container.classList.add("drawing");
        this.handlers.onState?.();
        this.requestUpdate();
        return true;
    }

    cancelDrawing() {
        const wasActive = this.mode || this.draft;
        this.mode = null;
        this.draft = null;
        this.view.container.classList.remove("drawing");
        if (wasActive) {
            this.requestUpdate();
            this.handlers.onState?.();
        }
    }

    async createNote(target) {
        const lines = this.lines;
        const text = await this.handlers.requestText?.("");
        // The symbol may have changed (or everything been cleared) while the prompt was open.
        if (!text || this.lines !== lines || this.lines.length >= MAX_DRAWINGS_PER_SYMBOL) return;
        const tip = this.toPoint(target);
        const body = tip && this.bubbleCentre(tip, text, { beside: true });
        if (!body) return;
        const note = { id: newId(), type: "note", a: target, b: body, text };
        this.lines.push(note);
        this.selectedId = note.id;
        this.changed();
    }

    /**
     * Adds a BUY / SELL / DIVIDEND label at `date` ("YYYY-MM-DD") pointing at `price` (for a dividend: that day's
     * close when the bar is loaded); false when the symbol is full.
     */
    addTrade(side, { amount, price, date, tax = 0 }) {
        if (!Object.hasOwn(TRADE_STYLE, side) || this.lines.length >= MAX_DRAWINGS_PER_SYMBOL) return false;
        if (side === "dividend") price = this.closeAt(dayOf(date)) ?? price;
        if (!(price > 0)) return false;
        // Without a loaded chart there are no pixels: buildTrade's default bubble position is kept.
        const trade = buildTrade(side, { amount, price, date, tax });
        delete trade.auto;
        const tip = this.toPoint(trade.a);
        const body = tip && this.bubbleCentre(tip, tradeText(trade), { below: side === "buy" });
        if (body) trade.b = body;
        this.lines.push(trade);
        this.selectedId = trade.id;
        this.changed();
        return true;
    }

    /**
     * Anchor of a bubble centre near `tip`: above it (below when `below` is set), or on the other side where it
     * would leave the pane; `beside` also moves it to the right (left near the right edge).
     */
    bubbleCentre(tip, text, { below = false, beside = false } = {}) {
        const { lines: rows, width } = wrapText(text);
        const w = width + 2 * NOTE.padding;
        const h = rows.length * NOTE.lineHeight + 2 * NOTE.padding;
        const gap = beside ? 40 : 30;
        const fitsAbove = tip.y - gap - h >= 0;
        const fitsBelow = tip.y + gap + h <= this.view.chart.panes()[0].getHeight();
        const down = below ? fitsBelow || !fitsAbove : !fitsAbove;
        let dx = 0;
        if (beside) dx = tip.x + 20 + w <= this.view.chart.timeScale().width() ? 20 + w / 2 : -(20 + w / 2);
        return this.toAnchor({ x: tip.x + dx, y: tip.y + (down ? gap + h / 2 : -(gap + h / 2)) });
    }

    /** The day of the bar nearest to `day` (unchanged outside the loaded bars). */
    snapDay(day) {
        const days = this.index?.days;
        if (!days) return day;
        const i = Math.round(this.index.toLogical(day));
        return i >= 0 && i < days.length ? days[i] : day;
    }

    /**
     * Places the bubbles of imported trades (`auto`) whose day is on screen: beside the tip (below a BUY, above the
     * others), moved further out or to the other side where it would cover another bubble or leave the pane.
     */
    layoutAuto() {
        this.layoutQueued = false;
        const range = this.view.chart.timeScale().getVisibleLogicalRange();
        if (!this.index || !range || this.drag) return;
        const paneWidth = this.view.chart.timeScale().width();
        const paneHeight = this.view.chart.panes()[0].getHeight();
        const placed = this.lines.filter((l) => !l.auto && this.isVisible(l)).map((l) => this.points.get(l.id)?.rect).filter(Boolean);
        let moved = false;
        for (const trade of this.lines.filter((l) => l.auto && this.isVisible(l)).sort((x, y) => x.a.day - y.a.day)) {
            const logical = this.index.toLogical(trade.a.day);
            const tip = this.toPoint(trade.a);
            if (logical < range.from || logical > range.to || !tip || tip.y < 0 || tip.y > paneHeight) continue;
            const { lines: rows, width } = wrapText(tradeText(trade));
            const w = width + 2 * NOTE.padding;
            const h = rows.length * NOTE.lineHeight + 2 * NOTE.padding;
            const x = Math.max(0, Math.min(tip.x - w / 2, paneWidth - w));
            const rectAt = (down, level) => {
                const dy = 30 + h / 2 + level * (h + 6);
                return { x, y: tip.y + (down ? dy : -dy) - h / 2, w, h };
            };
            const down = trade.side === "buy";
            let best = null;
            for (let level = 0; level < 8 && !best; level++) {
                best = [rectAt(down, level), rectAt(!down, level)]
                    .find((r) => r.y >= 0 && r.y + h <= paneHeight && !placed.some((p) => overlaps(p, r))) ?? null;
            }
            best ??= rectAt(down, 0);
            const anchor = this.toAnchor({ x: best.x + w / 2, y: best.y + h / 2 });
            if (!anchor) continue;
            trade.b = anchor;
            delete trade.auto;
            placed.push(best);
            moved = true;
        }
        if (moved) this.changed();
    }

    /** Close of the bar on `day` (or the nearest loaded bar before it); null when no bars are loaded or it is earlier. */
    closeAt(day) {
        const days = this.index?.days;
        if (!days?.length || day < days[0] - 7) return null;
        let lo = 0;
        let hi = days.length - 1;
        if (day >= days[hi]) return day - days[hi] <= 7 ? roundPrice(this.index.closes[hi]) : null;
        if (day <= days[0]) return roundPrice(this.index.closes[0]);
        while (hi - lo > 1) {
            const mid = (lo + hi) >> 1;
            if (days[mid] <= day) lo = mid;
            else hi = mid;
        }
        return roundPrice(this.index.closes[days[hi] === day ? hi : lo]);
    }

    /** Edits the selected (or given) comment's text, or a trade's amount, price and date. */
    async editLabel(id = this.selectedId) {
        const item = this.lines.find((line) => line.id === id);
        if (!item || !isBubble(item)) return;
        const lines = this.lines;
        const unchanged = () => this.lines !== lines || !lines.includes(item);
        if (typeOf(item) === "note") {
            const text = await this.handlers.requestText?.(item.text);
            if (!text || text === item.text || unchanged()) return;
            item.text = text;
        } else {
            const answer = await this.handlers.requestTrade?.(item.side,
                { price: item.price, amount: item.amount, date: isoOf(item.a.day), tax: item.tax ?? 0 });
            if (!answer || unchanged()) return;
            // Keep the bubble where it was relative to its tip.
            const tip = this.toPoint(item.a);
            const body = this.toPoint(item.b);
            const day = answer.date === isoOf(item.a.day) ? item.a.day : dayOf(answer.date);
            // A dividend points at the close of its day; it has no price of its own.
            const price = item.side === "dividend" ? (this.closeAt(day) ?? item.price) : answer.price;
            Object.assign(item, { price, amount: answer.amount, a: { day, price } });
            if (item.side === "dividend") {
                if (answer.tax > 0) item.tax = answer.tax;
                else delete item.tax;
            }
            const newTip = this.toPoint(item.a);
            const moved = tip && body && newTip && this.toAnchor({ x: newTip.x + body.x - tip.x, y: newTip.y + body.y - tip.y });
            if (moved) item.b = moved;
        }
        this.changed();
    }

    deleteSelected() {
        if (!this.selectedId) return;
        this.lines = this.lines.filter((line) => line.id !== this.selectedId);
        this.selectedId = null;
        this.changed();
    }

    deleteAll() {
        this.lines = [];
        this.selectedId = null;
        this.changed();
    }

    changed() {
        this.requestUpdate();
        this.handlers.onState?.();
        this.handlers.onChange?.(this.lines.map((line) => structuredClone(line)));
    }

    // ---- coordinates -------------------------------------------------------------------------

    /** Pane-local pixel position of a (day, price) anchor, or null when the chart has no data/scale yet. */
    toPoint(anchor) {
        const timeScale = this.view.chart.timeScale();
        const x0 = timeScale.logicalToCoordinate(0);
        const x1 = timeScale.logicalToCoordinate(1);
        const y = this.view.candles.priceToCoordinate(anchor.price);
        if (!this.index || x0 === null || x1 === null || y === null) return null;
        return { x: x0 + this.index.toLogical(anchor.day) * (x1 - x0), y };
    }

    toAnchor(point) {
        const timeScale = this.view.chart.timeScale();
        const x0 = timeScale.logicalToCoordinate(0);
        const x1 = timeScale.logicalToCoordinate(1);
        const price = this.view.candles.coordinateToPrice(point.y);
        if (!this.index || x0 === null || x1 === null || x1 === x0 || price === null) return null;
        return { day: round(this.index.toDay((point.x - x0) / (x1 - x0)), 4), price: Number(price.toPrecision(8)) };
    }

    /** Pointer position relative to the price pane; null outside it (price scale, volume pane, time axis). */
    panePoint(event) {
        const rect = this.view.chart.chartElement().getBoundingClientRect();
        const x = event.clientX - rect.left;
        const y = event.clientY - rect.top;
        const width = this.view.chart.timeScale().width();
        const height = this.view.chart.panes()[0].getHeight();
        return x >= 0 && y >= 0 && x <= width && y <= height ? { x, y } : null;
    }

    /** Drawings in paint order: lines and arrows, then comments and trades (on top), then the draft. */
    paintOrder() {
        const visible = this.lines.filter((line) => this.isVisible(line));
        const labels = visible.filter(isBubble);
        const others = visible.filter((line) => !isBubble(line));
        return [...others, ...labels, ...(this.draft ? [this.draft] : [])];
    }

    /** The handle, line or bubble under `point` (selected drawing first, then topmost; handles before bodies). */
    hit(point) {
        const ordered = this.paintOrder().filter((line) => line !== this.draft).reverse()
            .sort((a, b) => (b.id === this.selectedId) - (a.id === this.selectedId));
        const near = (p) => Math.hypot(point.x - p.x, point.y - p.y) <= HANDLE_RADIUS + 3;
        for (const line of ordered) {
            const p = this.points.get(line.id);
            if (!p) continue;
            if (p.rect) {
                if (near(p.a)) return { id: line.id, part: "a" };
                if (pointInRect(point, p.rect)) return { id: line.id, part: "body" };
                continue;
            }
            if (near(p.a)) return { id: line.id, part: "a" };
            if (near(p.b)) return { id: line.id, part: "b" };
            if (distanceToSegment(point, p.a, p.b) <= HIT_TOLERANCE) return { id: line.id, part: "line" };
        }
        return null;
    }

    // ---- pointer interaction -----------------------------------------------------------------

    bindEvents() {
        const container = this.view.container;
        // Capture phase: a press on a line must not also start panning the chart.
        container.addEventListener("pointerdown", (event) => this.onPointerDown(event), true);
        for (const type of ["mousedown", "touchstart"]) {
            container.addEventListener(type, (event) => {
                if (!this.consumed) return;
                event.stopPropagation();
                if (type === "touchstart") event.preventDefault();
            }, { capture: true, passive: false });
        }
        container.addEventListener("pointermove", (event) => {
            if (!this.draft || this.drag) return;
            const point = this.panePoint(event);
            const anchor = point && this.toAnchor(point);
            if (anchor) {
                this.draft.b = anchor;
                this.requestUpdate();
            }
        });
        window.addEventListener("pointermove", (event) => this.onDrag(event));
        window.addEventListener("pointerup", () => {
            this.endDrag();
            // Prompt only after the click is over, so the quick input doesn't take it as an outside click.
            const label = this.pendingLabel;
            this.pendingLabel = null;
            if (label) this.createNote(label.anchor);
            const editId = this.pendingEdit;
            this.pendingEdit = null;
            if (editId) this.editLabel(editId);
        });
        window.addEventListener("pointercancel", () => {
            this.pendingLabel = null;
            this.pendingEdit = null;
            this.endDrag();
        });
    }

    onPointerDown(event) {
        this.consumed = false;
        if (event.button !== 0) return;
        const point = this.panePoint(event);
        if (!point) return;
        if (this.mode) {
            const anchor = this.toAnchor(point);
            if (!anchor) return;
            this.consume(event);
            if (this.mode === "note") {
                this.pendingLabel = { mode: this.mode, anchor };
                this.cancelDrawing();
            } else if (!this.draft) {
                this.draft = { id: newId(), type: this.mode, a: anchor, b: { ...anchor } };
            } else {
                this.draft.b = anchor;
                const line = this.draft;
                this.cancelDrawing();
                const a = this.toPoint(line.a);
                const b = this.toPoint(line.b);
                if (a && b && Math.hypot(a.x - b.x, a.y - b.y) > 3) { // ignore a double click on one spot
                    this.lines.push(line);
                    this.selectedId = line.id;
                    this.changed();
                }
            }
            this.requestUpdate();
            return;
        }
        const target = this.hit(point);
        if (!target) {
            if (this.selectedId) {
                this.selectedId = null;
                this.requestUpdate();
                this.handlers.onState?.();
            }
            return; // the chart pans as usual
        }
        this.consume(event);
        this.selectedId = target.id;
        const line = this.lines.find((l) => l.id === target.id);
        // A second press on the same comment/trade within 400 ms (double-click / double-tap) edits it.
        const now = event.timeStamp;
        if (isBubble(line) && this.lastPress?.id === line.id && now - this.lastPress.time < 400) {
            this.lastPress = null;
            this.pendingEdit = line.id;
            this.handlers.onState?.();
            return;
        }
        this.lastPress = { id: line.id, time: now };
        this.drag = { ...target, start: point, original: structuredClone(line), moved: false };
        this.handlers.onState?.();
        this.requestUpdate();
    }

    consume(event) {
        this.consumed = true;
        event.stopPropagation();
        event.preventDefault();
    }

    onDrag(event) {
        if (!this.drag) return;
        const rect = this.view.chart.chartElement().getBoundingClientRect();
        const point = { x: event.clientX - rect.left, y: event.clientY - rect.top };
        const line = this.lines.find((l) => l.id === this.drag.id);
        if (!line) return;
        const { original, part, start } = this.drag;
        if (part === "line" || part === "body") {
            // Move both ends of a line, or a comment's bubble (its tip stays), by the same pixel offset.
            const dx = point.x - start.x;
            const dy = point.y - start.y;
            for (const end of part === "line" ? ["a", "b"] : ["b"]) {
                const from = this.toPoint(original[end]);
                const anchor = from && this.toAnchor({ x: from.x + dx, y: from.y + dy });
                if (anchor) line[end] = anchor;
            }
        } else {
            const anchor = this.toAnchor(point);
            // A trade's tip stays on its price and moves only between bars; a dividend's follows the close.
            if (anchor && typeOf(line) === "trade") {
                const day = this.snapDay(anchor.day);
                if (line.side === "dividend") line.price = this.closeAt(day) ?? line.price;
                line.a = { day, price: line.price };
            }
            else if (anchor) line[part] = anchor;
        }
        this.drag.moved = true;
        this.requestUpdate();
    }

    endDrag() {
        this.consumed = false;
        if (!this.drag) return;
        const { moved, id } = this.drag;
        this.drag = null;
        if (!moved) return;
        const line = this.lines.find((l) => l.id === id);
        if (line) delete line.auto; // placed by hand
        this.changed();
    }

    // ---- rendering (Lightweight Charts series primitive) -------------------------------------

    primitive() {
        const drawings = this;
        const renderer = {
            draw(target) {
                target.useMediaCoordinateSpace(({ context: ctx }) => drawings.paint(ctx));
            },
        };
        const paneView = { zOrder: () => "top", renderer: () => renderer };
        return {
            attached({ requestUpdate }) {
                drawings.requestUpdate = requestUpdate;
            },
            detached() {
                drawings.requestUpdate = () => {};
            },
            updateAllViews() {
                drawings.points.clear();
                for (const line of drawings.paintOrder()) {
                    const a = drawings.toPoint(line.a);
                    const b = drawings.toPoint(line.b);
                    if (!a || !b) continue;
                    if (!isBubble(line)) {
                        drawings.points.set(line.id, { a, b });
                        continue;
                    }
                    const { lines, width } = wrapText(bubbleText(line));
                    const w = width + 2 * NOTE.padding;
                    const h = lines.length * NOTE.lineHeight + 2 * NOTE.padding;
                    drawings.points.set(line.id, { a, b, lines, rect: { x: b.x - w / 2, y: b.y - h / 2, w, h } });
                }
                // Imported labels are laid out after this pass, when their pixel positions are known.
                if (!drawings.layoutQueued && drawings.index && drawings.lines.some((l) => l.auto && drawings.isVisible(l))) {
                    drawings.layoutQueued = true;
                    setTimeout(() => drawings.layoutAuto());
                }
            },
            paneViews: () => [paneView],
            hitTest(x, y) {
                if (drawings.mode) return { cursorStyle: "crosshair", externalId: "draw", zOrder: "top" };
                const target = drawings.hit({ x, y });
                if (!target) return null;
                const move = target.part === "line" || target.part === "body";
                return { cursorStyle: move ? "move" : "nwse-resize", externalId: target.id, zOrder: "top" };
            },
        };
    }

    paint(ctx) {
        ctx.save();
        ctx.lineCap = "round";
        ctx.lineJoin = "round";
        for (const line of this.paintOrder()) {
            const p = this.points.get(line.id);
            if (!p) continue;
            const selected = line.id === this.selectedId || line === this.draft;
            if (p.rect) this.paintNote(ctx, p, selected, typeOf(line) === "trade" ? TRADE_STYLE[line.side] : NOTE);
            else this.paintLine(ctx, p, typeOf(line) === "arrow", selected);
            if (selected) this.paintHandles(ctx, p.rect ? [p.a] : [p.a, p.b]);
        }
        ctx.restore();
    }

    paintLine(ctx, { a, b }, arrow, selected) {
        const angle = Math.atan2(b.y - a.y, b.x - a.x);
        const length = Math.hypot(b.x - a.x, b.y - a.y);
        // The shaft stops inside the head, so the round line cap doesn't stick out of the tip.
        const shaft = arrow ? Math.max(0, length - ARROW_HEAD * 0.6) : length;
        ctx.strokeStyle = LINE_COLOR;
        ctx.lineWidth = selected ? LINE_WIDTH + 1 : LINE_WIDTH;
        ctx.beginPath();
        ctx.moveTo(a.x, a.y);
        ctx.lineTo(a.x + shaft * Math.cos(angle), a.y + shaft * Math.sin(angle));
        ctx.stroke();
        if (!arrow || length < 1) return;
        const spread = Math.PI / 7;
        ctx.beginPath();
        ctx.moveTo(b.x, b.y);
        ctx.lineTo(b.x - ARROW_HEAD * Math.cos(angle - spread), b.y - ARROW_HEAD * Math.sin(angle - spread));
        ctx.lineTo(b.x - ARROW_HEAD * Math.cos(angle + spread), b.y - ARROW_HEAD * Math.sin(angle + spread));
        ctx.closePath();
        ctx.fillStyle = LINE_COLOR;
        ctx.fill();
    }

    paintNote(ctx, { a, b, rect, lines }, selected, style) {
        const body = new Path2D();
        body.roundRect(rect.x, rect.y, rect.w, rect.h, 6);
        let tail = null;
        if (!pointInRect(a, rect)) {
            const angle = Math.atan2(a.y - b.y, a.x - b.x);
            const nx = -Math.sin(angle) * NOTE.tailWidth;
            const ny = Math.cos(angle) * NOTE.tailWidth;
            tail = new Path2D();
            tail.moveTo(b.x + nx, b.y + ny);
            tail.lineTo(a.x, a.y);
            tail.lineTo(b.x - nx, b.y - ny);
            tail.closePath();
        }
        // Stroke, then fill: the fills hide the seam between tail and bubble and keep the outer half of the border.
        ctx.strokeStyle = selected ? LINE_COLOR : style.border;
        ctx.lineWidth = selected ? 3 : 2;
        ctx.fillStyle = style.fill;
        for (const path of [tail, body]) if (path) ctx.stroke(path);
        for (const path of [tail, body]) if (path) ctx.fill(path);
        if (style !== NOTE) { // trade: mark the exact price
            ctx.beginPath();
            ctx.arc(a.x, a.y, 3, 0, Math.PI * 2);
            ctx.fillStyle = style.border;
            ctx.fill();
        }
        ctx.fillStyle = style.text;
        ctx.font = NOTE.font;
        ctx.textBaseline = "middle";
        lines.forEach((text, i) => {
            ctx.fillText(text, rect.x + NOTE.padding, rect.y + NOTE.padding + (i + 0.5) * NOTE.lineHeight);
        });
    }

    paintHandles(ctx, ends) {
        ctx.strokeStyle = LINE_COLOR;
        ctx.lineWidth = 2;
        ctx.fillStyle = "#1e1e1e";
        for (const end of ends) {
            ctx.beginPath();
            ctx.arc(end.x, end.y, HANDLE_RADIUS, 0, Math.PI * 2);
            ctx.fill();
            ctx.stroke();
        }
    }
}

function isValidDrawing(line) {
    const anchor = (a) => a && Number.isFinite(a.day) && Number.isFinite(a.price);
    if (!line || typeof line.id !== "string" || !anchor(line.a) || !anchor(line.b)) return false;
    const type = typeOf(line);
    if (type === "note") return typeof line.text === "string" && line.text.length > 0 && line.text.length <= MAX_NOTE_LENGTH;
    if (type === "trade") {
        const positive = (n) => Number.isFinite(n) && n > 0;
        const tax = line.tax === undefined || (Number.isFinite(line.tax) && line.tax >= 0);
        return Object.hasOwn(TRADE_STYLE, line.side) && positive(line.price) && positive(line.amount) && tax;
    }
    return DRAWING_TYPES.includes(type);
}
