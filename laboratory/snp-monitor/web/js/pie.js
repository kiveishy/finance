// Dependency-free SVG donut chart with a legend.
const SVG_NS = "http://www.w3.org/2000/svg";
const SIZE = 260;
const RADIUS = 120;
const HOLE = 64;

export const PALETTE = [
    "#4f9cf9", "#2fbf71", "#f5a524", "#e5484d", "#8e6cf1", "#13b5c8", "#f76b15", "#d6409f",
    "#7ec850", "#3e63dd", "#ffc53d", "#12a594", "#c2298a", "#a18072", "#5b8def", "#46a758",
    "#e54666", "#6e56cf", "#0090ff", "#ad7f58", "#30a46c", "#f2a33a", "#8da4ef", "#de6c6c",
    "#56b6c2", "#b18cf0", "#c7a03c", "#5fb3a1", "#e0779f", "#7a92b8",
];
export const OTHER_COLOR = "#59616d";

function svg(tag, attrs = {}) {
    const node = document.createElementNS(SVG_NS, tag);
    for (const [name, value] of Object.entries(attrs)) node.setAttribute(name, String(value));
    return node;
}

function arcPath(start, end) {
    // Angles in radians, 0 = 12 o'clock, clockwise.
    const point = (angle, r) => [SIZE / 2 + r * Math.sin(angle), SIZE / 2 - r * Math.cos(angle)];
    const large = end - start > Math.PI ? 1 : 0;
    const [x1, y1] = point(start, RADIUS);
    const [x2, y2] = point(end, RADIUS);
    const [x3, y3] = point(end, HOLE);
    const [x4, y4] = point(start, HOLE);
    return `M${x1} ${y1} A${RADIUS} ${RADIUS} 0 ${large} 1 ${x2} ${y2} L${x3} ${y3} A${HOLE} ${HOLE} 0 ${large} 0 ${x4} ${y4} Z`;
}

/**
 * slices: [{key, label, detail, share (0..100), color, selectable}]
 * options: {centerTitle, centerValue, tooltip: {show(html-free lines, event), hide()}, onSelect(slice)}
 */
export function renderPie(container, slices, options) {
    container.replaceChildren();
    const chart = svg("svg", {viewBox: `0 0 ${SIZE} ${SIZE}`, class: "pie-svg", role: "img"});
    const total = slices.reduce((sum, s) => sum + s.share, 0);
    const legend = document.createElement("ol");
    legend.className = "legend";

    const centerTitle = svg("text", {x: SIZE / 2, y: SIZE / 2 - 6, class: "pie-center-title"});
    const centerValue = svg("text", {x: SIZE / 2, y: SIZE / 2 + 16, class: "pie-center-value"});
    const showCenter = (title, value) => {
        centerTitle.textContent = title;
        centerValue.textContent = value;
    };
    showCenter(options.centerTitle, options.centerValue);

    const items = [];
    let angle = 0;
    for (const slice of slices) {
        if (!(slice.share > 0)) continue;
        const sweep = (slice.share / total) * Math.PI * 2;
        const shape = sweep >= Math.PI * 2 - 1e-6
            ? svg("circle", {cx: SIZE / 2, cy: SIZE / 2, r: (RADIUS + HOLE) / 2, fill: "none",
                stroke: slice.color, "stroke-width": RADIUS - HOLE})
            : svg("path", {d: arcPath(angle, angle + sweep), fill: slice.color});
        shape.classList.add("slice");
        angle += sweep;
        chart.append(shape);

        const entry = document.createElement("li");
        const swatch = document.createElement("span");
        swatch.className = "swatch";
        swatch.style.background = slice.color;
        const label = document.createElement("span");
        label.className = "legend-label";
        label.textContent = slice.label;
        label.title = slice.detail || slice.label;
        const value = document.createElement("span");
        value.className = "legend-value";
        value.textContent = `${slice.share.toFixed(2)}%`;
        entry.append(swatch, label, value);
        if (slice.selectable) entry.classList.add("selectable");
        legend.append(entry);
        items.push({slice, shape, entry});
    }
    chart.append(centerTitle, centerValue);

    const highlight = (active) => {
        for (const item of items) {
            item.shape.classList.toggle("dim", active !== null && item !== active);
            item.entry.classList.toggle("active", item === active);
        }
        if (active) showCenter(active.slice.label, `${active.slice.share.toFixed(2)}%`);
        else showCenter(options.centerTitle, options.centerValue);
    };
    for (const item of items) {
        for (const node of [item.shape, item.entry]) {
            node.addEventListener("mouseenter", (event) => {
                highlight(item);
                options.tooltip?.show(item.slice.tooltip || [item.slice.label], event);
            });
            node.addEventListener("mousemove", (event) => options.tooltip?.move(event));
            node.addEventListener("mouseleave", () => {
                highlight(null);
                options.tooltip?.hide();
            });
            if (item.slice.selectable) node.addEventListener("click", () => options.onSelect?.(item.slice));
        }
    }
    container.append(chart, legend);
}
