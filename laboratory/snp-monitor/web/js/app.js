import {OTHER_COLOR, PALETTE, renderPie} from "./pie.js";

const $ = (id) => document.getElementById(id);
const MARKET_STATES = {
    REGULAR: "Market open", PRE: "Pre-market", PREPRE: "Market closed", POST: "After hours",
    POSTPOST: "Market closed", CLOSED: "Market closed",
};
const DESCENDING_FIRST = new Set(["price", "change", "change_percent", "market_cap", "price_share", "company_share"]);

const state = {
    snapshot: null,
    sortKey: "rank",
    sortDir: 1,
    filter: "",
    sector: "",
    selected: null,
    webBuild: null,
    pollTimer: null,
};

// ---- formatting ---------------------------------------------------------------------------

const priceFormat = new Intl.NumberFormat("en-US", {minimumFractionDigits: 2, maximumFractionDigits: 2});

function money(value) {
    return value == null ? "–" : priceFormat.format(value);
}

function signed(value, digits = 2, suffix = "") {
    if (value == null) return "–";
    return `${value > 0 ? "+" : ""}${value.toFixed(digits)}${suffix}`;
}

function bigMoney(value) {
    if (value == null) return "–";
    const units = [[1e12, "T"], [1e9, "B"], [1e6, "M"]];
    for (const [size, unit] of units) if (Math.abs(value) >= size) return `$${(value / size).toFixed(2)}${unit}`;
    return `$${priceFormat.format(value)}`;
}

function percent(value, digits = 3) {
    return value == null ? "–" : `${value.toFixed(digits)}%`;
}

function time(iso) {
    return iso ? new Date(iso).toLocaleTimeString() : "–";
}

// ---- tooltip ------------------------------------------------------------------------------

const tooltip = {
    show(lines, event) {
        const node = $("tooltip");
        node.replaceChildren(...lines.map((line, index) => {
            const div = document.createElement("div");
            if (index === 0) div.className = "tooltip-title";
            div.textContent = line;
            return div;
        }));
        node.hidden = false;
        this.move(event);
    },
    move(event) {
        const node = $("tooltip");
        const x = Math.min(event.clientX + 14, window.innerWidth - node.offsetWidth - 8);
        const y = Math.min(event.clientY + 14, window.innerHeight - node.offsetHeight - 8);
        node.style.left = `${x}px`;
        node.style.top = `${y}px`;
    },
    hide() {
        $("tooltip").hidden = true;
    },
};

// ---- data ---------------------------------------------------------------------------------

async function api(path, init) {
    const response = await fetch(path, {cache: "no-store", ...init});
    const body = await response.json().catch(() => null);
    if (!response.ok || body?.status !== "success") {
        throw new Error(body?.error || `HTTP ${response.status}`);
    }
    return body.data;
}

function prepare(snapshot) {
    const byCap = [...snapshot.stocks].sort((a, b) => (b.market_cap ?? -1) - (a.market_cap ?? -1));
    byCap.forEach((stock, index) => { stock.rank = index + 1; });
    return snapshot;
}

// ---- rendering ----------------------------------------------------------------------------

function renderStatus() {
    const snap = state.snapshot;
    const status = $("status");
    status.classList.toggle("warn", Boolean(snap.error));
    const marketState = MARKET_STATES[snap.market_state] || snap.market_state || "";
    const parts = [
        marketState,
        `Updated ${time(snap.updated_at)}`,
        `List: ${snap.constituents.source} (${snap.constituents.count})`,
    ].filter(Boolean);
    if (snap.error) parts.push(`Last refresh failed: ${snap.error}`);
    status.textContent = parts.join(" · ");
    status.title = snap.last_trade_at ? `Last trade ${new Date(snap.last_trade_at).toLocaleString()}` : "";
    $("status").dataset.state = snap.market_state || "";
}

function card(label, value, extraClass = "") {
    const node = document.createElement("div");
    node.className = `stat ${extraClass}`;
    const title = document.createElement("div");
    title.className = "stat-label";
    title.textContent = label;
    const text = document.createElement("div");
    text.className = "stat-value";
    text.textContent = value;
    node.append(title, text);
    return node;
}

function renderSummary() {
    const summary = state.snapshot.summary;
    $("summary").replaceChildren(
        card("Stocks priced", `${summary.priced} / ${state.snapshot.constituents.count}`),
        card("Companies", String(summary.companies)),
        card("Advancers", String(summary.advancers), "up"),
        card("Decliners", String(summary.decliners), "down"),
        card("Total market cap", bigMoney(summary.total_market_cap)),
        card("Sum of all prices", `$${priceFormat.format(summary.total_price)}`),
    );
}

function topSlices(entries, count, otherLabel) {
    const top = entries.slice(0, count);
    const rest = entries.slice(count);
    const slices = top.map((entry, index) => ({...entry, color: PALETTE[index % PALETTE.length], selectable: true}));
    const restShare = rest.reduce((sum, entry) => sum + entry.share, 0);
    if (restShare > 0) {
        slices.push({
            key: "__other__", label: otherLabel(rest.length), share: restShare, color: OTHER_COLOR, selectable: false,
            tooltip: [otherLabel(rest.length), `Share ${restShare.toFixed(2)}%`],
        });
    }
    return slices;
}

function renderCharts() {
    const snap = state.snapshot;
    const count = Number($("slices").value);

    const byPrice = snap.stocks
        .filter((s) => s.price_share != null)
        .sort((a, b) => b.price_share - a.price_share)
        .map((s) => ({
            key: s.symbol, label: s.symbol, detail: s.name, share: s.price_share, symbol: s.symbol,
            tooltip: [`${s.symbol} – ${s.name}`, `Price $${money(s.price)}`, `Share of all prices ${percent(s.price_share)}`],
        }));
    renderPie($("price-pie"), topSlices(byPrice, count, (n) => `Other ${n} stocks`), {
        centerTitle: "Sum of prices",
        centerValue: `$${Math.round(snap.summary.total_price).toLocaleString("en-US")}`,
        tooltip,
        onSelect: (slice) => select(slice.symbol),
    });

    const byCap = snap.companies
        .filter((c) => c.share != null)
        .map((c) => ({
            key: c.symbols.join(","), label: `${c.name} (${c.symbols.join(", ")})`, detail: c.name, share: c.share,
            symbol: c.symbols[0],
            tooltip: [c.name, `Symbols ${c.symbols.join(", ")}`, `Market cap ${bigMoney(c.market_cap)}`,
                `Share of S&P 500 value ${percent(c.share)}`],
        }));
    renderPie($("cap-pie"), topSlices(byCap, count, (n) => `Other ${n} companies`), {
        centerTitle: "Total value",
        centerValue: bigMoney(snap.summary.total_market_cap),
        tooltip,
        onSelect: (slice) => select(slice.symbol),
    });
}

function renderSectors() {
    const select = $("sector");
    const sectors = [...new Set(state.snapshot.stocks.map((s) => s.sector).filter(Boolean))].sort();
    const current = select.value;
    select.replaceChildren(new Option("All sectors", ""), ...sectors.map((s) => new Option(s, s)));
    select.value = sectors.includes(current) ? current : "";
}

function compare(a, b) {
    const key = state.sortKey;
    const x = a[key];
    const y = b[key];
    if (x == null && y == null) return 0;
    if (x == null) return 1; // missing values always last
    if (y == null) return -1;
    const order = typeof x === "string" ? x.localeCompare(y) : x - y;
    return order * state.sortDir;
}

function cell(text, className = "") {
    const td = document.createElement("td");
    td.textContent = text;
    if (className) td.className = className;
    return td;
}

function direction(value) {
    return value > 0 ? "up" : value < 0 ? "down" : "";
}

function renderTable() {
    const needle = state.filter.trim().toLowerCase();
    const rows = state.snapshot.stocks
        .filter((s) => !state.sector || s.sector === state.sector)
        .filter((s) => !needle || s.symbol.toLowerCase().includes(needle) || s.name.toLowerCase().includes(needle)
            || s.sector.toLowerCase().includes(needle))
        .sort(compare);

    const body = document.querySelector("#stocks tbody");
    body.replaceChildren(...rows.map((s) => {
        const tr = document.createElement("tr");
        tr.dataset.symbol = s.symbol;
        if (s.symbol === state.selected) tr.classList.add("selected");
        const move = direction(s.change);
        tr.append(
            cell(String(s.rank), "num muted"),
            cell(s.symbol, "symbol"),
            cell(s.name),
            cell(s.sector, "muted"),
            cell(money(s.price), "num"),
            cell(signed(s.change), `num ${move}`),
            cell(signed(s.change_percent, 2, "%"), `num ${move}`),
            cell(bigMoney(s.market_cap), "num"),
            cell(percent(s.price_share), "num"),
            cell(percent(s.company_share), "num"),
        );
        return tr;
    }));
    $("count").textContent = rows.length === state.snapshot.stocks.length
        ? `(${rows.length})` : `(${rows.length} of ${state.snapshot.stocks.length})`;

    for (const th of document.querySelectorAll("#stocks th")) {
        th.classList.toggle("sorted", th.dataset.key === state.sortKey);
        th.dataset.dir = th.dataset.key === state.sortKey ? (state.sortDir > 0 ? "asc" : "desc") : "";
    }
}

function render() {
    renderStatus();
    renderSummary();
    renderCharts();
    renderSectors();
    renderTable();
}

function select(symbol) {
    state.selected = symbol;
    state.filter = "";
    state.sector = "";
    $("filter").value = "";
    $("sector").value = "";
    renderTable();
    const row = document.querySelector(`#stocks tbody tr[data-symbol="${CSS.escape(symbol)}"]`);
    row?.scrollIntoView({behavior: "smooth", block: "center"});
}

// ---- polling ------------------------------------------------------------------------------

async function checkWebBuild() {
    try {
        const health = await api("/api/health");
        if (state.webBuild && health.web_build && health.web_build !== state.webBuild) location.reload();
        state.webBuild = health.web_build;
    } catch {
        // server restarting - the snapshot poll reports problems
    }
}

async function load() {
    clearTimeout(state.pollTimer);
    let delay = 3000;
    try {
        const snapshot = prepare(await api("/api/snapshot"));
        state.snapshot = snapshot;
        render();
        delay = Math.max(10, snapshot.refresh_seconds || 60) * 1000;
    } catch (error) {
        const status = $("status");
        status.classList.add("warn");
        status.textContent = state.snapshot
            ? `Connection problem: ${error.message} (showing data from ${time(state.snapshot.updated_at)})`
            : `Loading S&P 500 data… ${error.message}`;
    }
    void checkWebBuild();
    state.pollTimer = setTimeout(load, delay);
}

async function refreshNow() {
    const button = $("refresh");
    button.disabled = true;
    const before = state.snapshot?.updated_at;
    try {
        await api("/api/refresh", {method: "POST", headers: {"Content-Type": "application/json"}, body: "{}"});
        for (let attempt = 0; attempt < 15; attempt += 1) {
            await new Promise((resolve) => setTimeout(resolve, 2000));
            const snapshot = await api("/api/snapshot");
            if (snapshot.updated_at !== before) break;
        }
    } catch (error) {
        $("status").textContent = error.message;
    } finally {
        button.disabled = false;
        await load();
    }
}

// ---- events -------------------------------------------------------------------------------

$("refresh").addEventListener("click", refreshNow);
$("slices").addEventListener("change", () => state.snapshot && renderCharts());
$("filter").addEventListener("input", (event) => {
    state.filter = event.target.value;
    state.selected = null;
    if (state.snapshot) renderTable();
});
$("sector").addEventListener("change", (event) => {
    state.sector = event.target.value;
    if (state.snapshot) renderTable();
});
document.querySelector("#stocks thead").addEventListener("click", (event) => {
    const key = event.target.closest("th")?.dataset.key;
    if (!key || !state.snapshot) return;
    if (state.sortKey === key) state.sortDir = -state.sortDir;
    else {
        state.sortKey = key;
        state.sortDir = DESCENDING_FIRST.has(key) ? -1 : 1;
    }
    renderTable();
});
document.querySelector("#stocks tbody").addEventListener("click", (event) => {
    const symbol = event.target.closest("tr")?.dataset.symbol;
    if (!symbol) return;
    state.selected = state.selected === symbol ? null : symbol;
    for (const tr of document.querySelectorAll("#stocks tbody tr")) {
        tr.classList.toggle("selected", tr.dataset.symbol === state.selected);
    }
});

load();
