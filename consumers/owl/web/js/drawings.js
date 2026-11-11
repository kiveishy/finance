// Trend lines drawn on the price pane: create (click, click), select, move, stretch an end, delete.
// Ends are anchored to (date, price), so lines survive zooming, scrolling and D/W/M interval changes.

const DAY_MS = 86_400_000;
const LINE_COLOR = "#2962ff";
const LINE_WIDTH = 2;
const HANDLE_RADIUS = 5;
const HIT_TOLERANCE = 6;
export const MAX_LINES_PER_SYMBOL = 100;

const dayOf = (iso) => Date.parse(`${iso}T00:00:00Z`) / DAY_MS;
const round = (value, digits) => Number(value.toFixed(digits));
let nextId = Date.now();

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

export class LineDrawings {
    /** handlers: onChange(lines) after a line is created, moved, stretched or deleted; onState() when mode/selection change */
    constructor(view, handlers) {
        this.view = view;
        this.handlers = handlers;
        this.lines = []; // {id, a: {day, price}, b: {day, price}}
        this.index = null;
        this.selectedId = null;
        this.drawing = false;
        this.draft = null; // line being drawn: its end follows the pointer
        this.drag = null; // {id, part: "a" | "b" | "line", start: point, original: line}
        this.points = new Map(); // id -> {a: {x, y}, b: {x, y}} in pane coordinates, refreshed on every chart update
        this.requestUpdate = () => {};
        view.candles.attachPrimitive(this.primitive());
        this.bindEvents();
    }

    get selected() {
        return this.lines.find((line) => line.id === this.selectedId) ?? null;
    }

    setBars(bars) {
        this.index = bars.length ? new TimeIndex(bars) : null;
        this.requestUpdate();
    }

    setLines(lines) {
        this.lines = Array.isArray(lines) ? lines.filter(isValidLine).map((line) => structuredClone(line)) : [];
        this.selectedId = null;
        this.cancelDrawing();
        this.requestUpdate();
        this.handlers.onState?.();
    }

    startDrawing() {
        if (this.lines.length >= MAX_LINES_PER_SYMBOL) return false;
        this.drawing = true;
        this.selectedId = null;
        this.view.container.classList.add("drawing");
        this.handlers.onState?.();
        this.requestUpdate();
        return true;
    }

    cancelDrawing() {
        const wasActive = this.drawing || this.draft;
        this.drawing = false;
        this.draft = null;
        this.view.container.classList.remove("drawing");
        if (wasActive) {
            this.requestUpdate();
            this.handlers.onState?.();
        }
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

    /** The handle or line under `point` (selected line first, handles before bodies). */
    hit(point) {
        const ordered = [...this.lines].sort((a, b) => (b.id === this.selectedId) - (a.id === this.selectedId));
        for (const line of ordered) {
            const p = this.points.get(line.id);
            if (!p) continue;
            if (Math.hypot(point.x - p.a.x, point.y - p.a.y) <= HANDLE_RADIUS + 3) return { id: line.id, part: "a" };
            if (Math.hypot(point.x - p.b.x, point.y - p.b.y) <= HANDLE_RADIUS + 3) return { id: line.id, part: "b" };
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
        window.addEventListener("pointerup", () => this.endDrag());
        window.addEventListener("pointercancel", () => this.endDrag());
    }

    onPointerDown(event) {
        this.consumed = false;
        if (event.button !== 0) return;
        const point = this.panePoint(event);
        if (!point) return;
        if (this.drawing) {
            const anchor = this.toAnchor(point);
            if (!anchor) return;
            this.consume(event);
            if (!this.draft) {
                this.draft = { id: `l${(nextId++).toString(36)}`, a: anchor, b: { ...anchor } };
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
        if (part === "line") {
            // Move both ends by the same pixel offset.
            const dx = point.x - start.x;
            const dy = point.y - start.y;
            for (const end of ["a", "b"]) {
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
                for (const line of [...drawings.lines, ...(drawings.draft ? [drawings.draft] : [])]) {
                    const a = drawings.toPoint(line.a);
                    const b = drawings.toPoint(line.b);
                    if (a && b) drawings.points.set(line.id, { a, b });
                }
            },
            paneViews: () => [paneView],
            hitTest(x, y) {
                if (drawings.drawing) return { cursorStyle: "crosshair", externalId: "draw", zOrder: "top" };
                const target = drawings.hit({ x, y });
                if (!target) return null;
                return { cursorStyle: target.part === "line" ? "move" : "nwse-resize", externalId: target.id, zOrder: "top" };
            },
        };
    }

    paint(ctx) {
        ctx.save();
        ctx.lineCap = "round";
        for (const line of [...this.lines, ...(this.draft ? [this.draft] : [])]) {
            const p = this.points.get(line.id);
            if (!p) continue;
            const selected = line.id === this.selectedId || line === this.draft;
            ctx.strokeStyle = LINE_COLOR;
            ctx.lineWidth = selected ? LINE_WIDTH + 1 : LINE_WIDTH;
            ctx.beginPath();
            ctx.moveTo(p.a.x, p.a.y);
            ctx.lineTo(p.b.x, p.b.y);
            ctx.stroke();
            if (!selected) continue;
            for (const end of [p.a, p.b]) {
                ctx.beginPath();
                ctx.arc(end.x, end.y, HANDLE_RADIUS, 0, Math.PI * 2);
                ctx.fillStyle = "#1e1e1e";
                ctx.fill();
                ctx.lineWidth = 2;
                ctx.stroke();
            }
        }
        ctx.restore();
    }
}

function isValidLine(line) {
    const anchor = (a) => a && Number.isFinite(a.day) && Number.isFinite(a.price);
    return line && typeof line.id === "string" && anchor(line.a) && anchor(line.b);
}
