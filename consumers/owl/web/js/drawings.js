// Drawings on the price pane: trend lines, arrows (click start, click end) and comment bubbles (click the
// point, type the text). Select, move, stretch an end, edit a comment, delete.
// Points are anchored to (date, price), so drawings survive zooming, scrolling and D/W/M interval changes.
// Stored shape: {id, type: "line" | "arrow" | "note", a, b, text?}; a missing type means "line" (older data).
// For a note, `a` is the point the bubble points to and `b` the bubble centre.

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
export const DRAWING_TYPES = ["line", "arrow", "note"];

const dayOf = (iso) => Date.parse(`${iso}T00:00:00Z`) / DAY_MS;
const round = (value, digits) => Number(value.toFixed(digits));
const typeOf = (drawing) => drawing.type ?? "line";
let nextId = Date.now();
const newId = () => `l${(nextId++).toString(36)}`;

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
     *           onState() when the mode or selection change; requestText(initial) -> Promise<string | null> for comments
     */
    constructor(view, handlers) {
        this.view = view;
        this.handlers = handlers;
        this.lines = []; // all drawings of the symbol: {id, type, a: {day, price}, b: {day, price}, text?}
        this.index = null;
        this.selectedId = null;
        this.mode = null; // the type being created, or null
        this.draft = null; // line/arrow being drawn: its end follows the pointer
        this.pendingNote = null; // comment target clicked; the text is asked for on pointer up
        this.pendingEdit = null; // id of a double-clicked comment, edited on pointer up
        this.lastPress = null; // {id, time} of the last press on a drawing, to detect double-clicks
        this.drag = null; // {id, part: "a" | "b" | "line" | "body", start: point, original: drawing}
        this.points = new Map(); // id -> {a, b, rect?, lines?} in pane coordinates, refreshed on every chart update
        this.requestUpdate = () => {};
        view.candles.attachPrimitive(this.primitive());
        this.bindEvents();
    }

    get drawing() {
        return this.mode !== null;
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
        this.pendingNote = null;
        this.cancelDrawing();
        this.requestUpdate();
        this.handlers.onState?.();
    }

    startDrawing(type = "line") {
        if (!DRAWING_TYPES.includes(type) || this.lines.length >= MAX_DRAWINGS_PER_SYMBOL) return false;
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
        if (!tip) return;
        // Put the bubble up-right of the point, or to the other side where it would leave the pane.
        const { lines: rows, width } = wrapText(text);
        const w = width + 2 * NOTE.padding;
        const h = rows.length * NOTE.lineHeight + 2 * NOTE.padding;
        const dx = tip.x + 20 + w <= this.view.chart.timeScale().width() ? 20 + w / 2 : -(20 + w / 2);
        const dy = tip.y - 40 - h >= 0 ? -(40 + h / 2) : 40 + h / 2;
        const body = this.toAnchor({ x: tip.x + dx, y: tip.y + dy });
        if (!body) return;
        const note = { id: newId(), type: "note", a: target, b: body, text };
        this.lines.push(note);
        this.selectedId = note.id;
        this.changed();
    }

    /** Asks for a new text for the selected (or given) comment. */
    async editNote(id = this.selectedId) {
        const note = this.lines.find((line) => line.id === id);
        if (!note || typeOf(note) !== "note") return;
        const lines = this.lines;
        const text = await this.handlers.requestText?.(note.text);
        if (!text || text === note.text || this.lines !== lines || !lines.includes(note)) return;
        note.text = text;
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

    /** Drawings in paint order: lines and arrows, then comments (on top), then the draft. */
    paintOrder() {
        const notes = this.lines.filter((line) => typeOf(line) === "note");
        const others = this.lines.filter((line) => typeOf(line) !== "note");
        return [...others, ...notes, ...(this.draft ? [this.draft] : [])];
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
            const target = this.pendingNote;
            this.pendingNote = null;
            if (target) this.createNote(target);
            const editId = this.pendingEdit;
            this.pendingEdit = null;
            if (editId) this.editNote(editId);
        });
        window.addEventListener("pointercancel", () => {
            this.pendingNote = null;
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
                this.pendingNote = anchor;
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
        // A second press on the same comment within 400 ms (double-click / double-tap) edits it.
        const now = event.timeStamp;
        if (typeOf(line) === "note" && this.lastPress?.id === line.id && now - this.lastPress.time < 400) {
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
            if (anchor) line[part] = anchor;
        }
        this.drag.moved = true;
        this.requestUpdate();
    }

    endDrag() {
        this.consumed = false;
        if (!this.drag) return;
        const moved = this.drag.moved;
        this.drag = null;
        if (moved) this.changed();
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
                    if (typeOf(line) !== "note") {
                        drawings.points.set(line.id, { a, b });
                        continue;
                    }
                    const { lines, width } = wrapText(line.text);
                    const w = width + 2 * NOTE.padding;
                    const h = lines.length * NOTE.lineHeight + 2 * NOTE.padding;
                    drawings.points.set(line.id, { a, b, lines, rect: { x: b.x - w / 2, y: b.y - h / 2, w, h } });
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
            if (p.rect) this.paintNote(ctx, p, selected);
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

    paintNote(ctx, { a, b, rect, lines }, selected) {
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
        ctx.strokeStyle = selected ? LINE_COLOR : NOTE.border;
        ctx.lineWidth = selected ? 3 : 2;
        ctx.fillStyle = NOTE.fill;
        for (const path of [tail, body]) if (path) ctx.stroke(path);
        for (const path of [tail, body]) if (path) ctx.fill(path);
        ctx.fillStyle = NOTE.text;
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
    return DRAWING_TYPES.includes(type);
}
