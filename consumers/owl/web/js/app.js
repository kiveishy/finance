// Owl web app: wires the API client, explorer, editor tabs, chart view and status bar.
import { ApiClient, ApiError, loadConnection, saveConnection } from "./api.js";
import { ChartView, DEFAULT_INDICATORS, INDICATOR_COLORS, indicatorKey, indicatorLabel } from "./chart.js";
import { ConnectionMonitor, State } from "./connection.js";
import { LineDrawings, MAX_LINES_PER_SYMBOL } from "./drawings.js";
import { Explorer } from "./explorer.js";
import { describeMarket } from "./market.js";
import { SettingsPage, SettingsStore } from "./settings.js";
import { confirm, el, formatNumber, icon, notify, prompt } from "./ui.js";

const RANGES = ["1D", "1W", "1M", "3M", "6M", "1Y", "2Y", "5Y", "ALL"];
const INTERVALS = [["", "Auto", "Interval chosen by range"], ["D", "D", "Daily candles"], ["W", "W", "Weekly candles"], ["M", "M", "Monthly candles"]];
const UI_KEY = "owl.ui";
const SETTINGS_TAB = "@settings"; // tab id that can never be a ticker symbol
const QUOTES_PER_REQUEST = 200;
const SYMBOL_RE = /^[A-Za-z0-9^][A-Za-z0-9.\-^=]{0,19}$/;
const INDICATOR_RE = /^(sma|ema|ma)\s*[: ]?\s*(\d{1,4})$/i;
const TYPE_LABELS = { EQUITY: "Stock", ETF: "ETF", MUTUALFUND: "Fund", INDEX: "Index", FUTURE: "Future", CRYPTOCURRENCY: "Crypto", CURRENCY: "FX" };
const TYPE_ICONS = { EQUITY: "symbol-event", ETF: "package", MUTUALFUND: "package", INDEX: "graph", FUTURE: "clock", CRYPTOCURRENCY: "circle-large-outline", CURRENCY: "arrow-swap" };

const $ = (id) => document.getElementById(id);

class OwlApp {
    constructor() {
        this.api = new ApiClient();
        this.ui = { range: "6M", interval: "", tabs: [], active: null };
        try {
            Object.assign(this.ui, JSON.parse(localStorage.getItem(UI_KEY) || "{}"));
        } catch { /* ignore corrupt UI state */ }
        this.settings = new SettingsStore(this.api, this.settingsSchema());
        this.settingsPage = new SettingsPage($("settings-panel"), this.settings, {
            onError: (err) => this.reportError("Saving settings failed", err),
        });
        this.settings.addEventListener("change", (e) => this.applySettings(e.detail.keys));
        this.indicators = DEFAULT_INDICATORS.map((i) => ({ ...i }));
        this.drawings = {}; // symbol -> trend lines
        this.lineTool = null;
        this.lineSymbol = null; // symbol whose lines are on the chart
        this.groups = [];
        this.chart = null;
        this.chartRequest = 0;
        this.connection = new ConnectionMonitor(this.api);
        this.api.onFailure = (err) => this.connection.reportFailure(err);
        this.connection.addEventListener("change", (e) => this.onConnectionChange(e.detail));
        setInterval(() => this.renderConnection(), 1000); // retry countdown
        this.explorer = new Explorer($("explorer"), {
            onOpen: (symbol) => this.open(symbol),
            onAddSymbol: (groupId) => this.addSymbol(groupId),
            onRemoveSymbol: (groupId, symbol) => this.removeSymbol(groupId, symbol),
            onRenameGroup: (group) => this.renameGroup(group),
            onDeleteGroup: (group) => this.deleteGroup(group),
            onMoveSymbol: (from, symbol, to, options) => this.moveSymbol(from, symbol, to, options),
            onPickMoveTarget: (from, symbol) => this.pickMoveTarget(from, symbol),
        });
        this.applySettings();
        this.renderMarketStatus();
        setInterval(() => this.renderMarketStatus(), 15_000);
        this.bindEvents();
        this.renderToolbar();
    }

    /** Every user-configurable feature is listed here; the Settings tab is generated from it. */
    settingsSchema() {
        return [
            {
                key: "workbench.explorerPosition", section: "Workbench", title: "Explorer Position", type: "enum",
                description: "Side of the window where the Explorer (watchlist) and the activity bar are shown.",
                options: [["left", "Left"], ["right", "Right"]], default: "right",
            },
            {
                key: "workbench.explorerWidth", section: "Workbench", title: "Explorer Width", type: "number",
                description: "Width of the Explorer. You can also drag the Explorer's edge.",
                min: 170, max: 600, unit: "px", default: 260,
            },
            {
                key: "workbench.showMarketStatus", section: "Workbench", title: "Show Market Status", type: "boolean",
                description: "Show in the title bar whether the US stock market (NYSE / Nasdaq) is open, in pre-market, after hours or closed, and when it opens or closes. Computed locally in New York time, including NYSE holidays and early closes.",
                default: true,
            },
            {
                key: "explorer.showPrices", section: "Explorer", title: "Show Prices", type: "boolean",
                description: "Show the last price of every symbol in the Explorer.", default: true,
            },
            {
                key: "explorer.showChange", section: "Explorer", title: "Show Day Change", type: "boolean",
                description: "Show the day change in % next to the price (green up, red down).", default: true,
            },
            {
                key: "explorer.showGroupChange", section: "Explorer", title: "Show Group Summary", type: "boolean",
                description: "Show on each group row how many of its symbols are up (▲) and down (▼) today and their average day change (equal-weighted). Hover it for the best and worst symbol.",
                default: true,
            },
            {
                key: "explorer.priceRefreshSeconds", section: "Explorer", title: "Price Refresh Interval", type: "number",
                description: "How often the Explorer prices are refreshed.", min: 15, max: 3600, unit: "seconds", default: 60,
            },
            {
                key: "chart.resetIndicators", section: "Chart", title: "Indicators", type: "action",
                description: "Moving averages drawn on every chart. Add or toggle them with the Indicators menu of a chart.",
                detail: () => this.indicators.map(indicatorLabel).join(", ") || "none",
                button: "Reset to MA 20 / 50 / 200",
                run: () => {
                    this.indicators = DEFAULT_INDICATORS.map((i) => ({ ...i }));
                    this.saveIndicators();
                    this.settingsPage.render();
                },
            },
            {
                key: "chart.drawings", section: "Chart", title: "Trend Lines", type: "action",
                description: "Lines drawn with the Line tool of the chart toolbar. Click a line to select it, drag it to move it, drag an end to stretch it, press Delete to remove it. They are saved per symbol on the server.",
                detail: () => {
                    const counts = Object.values(this.drawings).map((lines) => lines.length);
                    const total = counts.reduce((sum, n) => sum + n, 0);
                    return `${total} line${total === 1 ? "" : "s"} on ${counts.length} symbol${counts.length === 1 ? "" : "s"}`;
                },
                button: "Delete all lines…",
                run: () => this.clearAllDrawings(),
            },
            {
                key: "connection.server", section: "Connection", title: "Owl Server", type: "action",
                description: "Address and API key of the owl server. Only needed when the UI isn't served by owl itself (e.g. the Android app). Stored on this device only.",
                detail: () => loadConnection().url || "This owl server",
                button: "Edit…",
                run: () => this.editConnection(),
            },
            {
                key: "connection.providers", section: "Connection", title: "Data Providers", type: "action",
                description: "Status of the market data providers, in fallback order.",
                detail: () => this.connection.status?.aggregator?.detail ?? this.connection.state,
                button: "Show status",
                run: () => this.showStatusDetails(),
            },
        ];
    }

    applySettings(keys = null) {
        const changed = (key) => keys === null || keys.includes(key);
        const s = this.settings;
        document.body.classList.toggle("explorer-right", s.get("workbench.explorerPosition") === "right");
        document.body.classList.toggle("hide-market-status", !s.get("workbench.showMarketStatus"));
        document.documentElement.style.setProperty("--sidebar-width", `${s.get("workbench.explorerWidth")}px`);
        document.body.classList.toggle("hide-prices", !s.get("explorer.showPrices"));
        document.body.classList.toggle("hide-change", !s.get("explorer.showChange"));
        document.body.classList.toggle("hide-group-change", !s.get("explorer.showGroupChange"));
        if (changed("explorer.priceRefreshSeconds")) {
            clearInterval(this.quoteTimer);
            this.quoteTimer = setInterval(() => this.refreshQuotes(), s.get("explorer.priceRefreshSeconds") * 1000);
        }
        if (keys !== null && changed("explorer.showPrices") && s.get("explorer.showPrices")) this.refreshQuotes();
    }

    renderMarketStatus() {
        const market = describeMarket();
        const node = $("market-status");
        node.className = `market-status ${market.state}`;
        node.textContent = market.text;
        node.title = market.title;
    }

    openSettings() {
        this.open(SETTINGS_TAB);
        this.settingsPage.focusSearch();
    }

    /** The symbol of the active chart tab (null when no tab or the Settings tab is active). */
    get activeSymbol() {
        return this.ui.active === SETTINGS_TAB ? null : this.ui.active;
    }

    async start() {
        // Inside the Android app the UI is bundled, so the owl server location must be configured first.
        if (window.Capacitor?.isNativePlatform?.() && !loadConnection().url) {
            notify("Enter the address of your owl server (e.g. http://192.168.1.20:8710)", "info", 10000);
            await this.editConnection();
        }
        await this.connection.check();
        await Promise.all([this.loadSettings(), this.settings.load(), this.refreshGroups()]);
        this.ui.tabs = this.ui.tabs.filter((s) => s === SETTINGS_TAB || SYMBOL_RE.test(s));
        if (this.ui.active && this.ui.tabs.includes(this.ui.active)) this.activate(this.ui.active);
        else this.renderTabs();
    }

    saveUi() {
        localStorage.setItem(UI_KEY, JSON.stringify(this.ui));
    }

    // ---- events ------------------------------------------------------------------------------

    bindEvents() {
        const actions = {
            "add-symbol": () => this.addSymbol(),
            "go-to-symbol": () => this.goToSymbol(),
            "new-group": () => this.newGroup(),
            refresh: () => { this.connection.check(true); this.refreshGroups(); this.loadChart(); },
            "retry-connection": () => this.connection.check(true),
            "connection-details": () => this.showStatusDetails(),
            settings: () => this.openSettings(),
            "edit-connection": () => this.editConnection(),
            "zoom-in": () => this.chart?.zoom(0.7),
            "zoom-out": () => this.chart?.zoom(1.4),
            "zoom-reset": () => this.chart?.resetZoom(),
            "reload-chart": () => this.loadChart(),
            "draw-line": () => this.toggleLineDrawing(),
            "delete-line": () => this.lineTool?.deleteSelected(),
            "clear-lines": () => this.clearLines(),
        };
        document.addEventListener("click", (event) => {
            const target = event.target.closest("[data-action]");
            if (target && actions[target.dataset.action]) actions[target.dataset.action]();
            if (!event.target.closest("#indicators-dropdown")) $("indicators-menu").hidden = true;
        });
        document.addEventListener("keydown", (event) => {
            if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "p") {
                event.preventDefault();
                this.goToSymbol();
            } else if ((event.ctrlKey || event.metaKey) && event.key === ",") {
                event.preventDefault();
                this.openSettings();
            } else if (this.lineTool && this.activeSymbol && !event.target.closest("input, textarea, select") && $("quickinput").hidden) {
                if (event.key === "Escape") this.lineTool.cancelDrawing();
                else if ((event.key === "Delete" || event.key === "Backspace") && this.lineTool.selected) {
                    event.preventDefault();
                    this.lineTool.deleteSelected();
                }
            }
        });
        $("toggle-sidebar").addEventListener("click", () => document.body.classList.toggle("sidebar-open"));
        $("status-connection").addEventListener("click", () => this.showStatusDetails());
        $("indicators-button").addEventListener("click", () => this.toggleIndicatorMenu());
        this.bindSash();
    }

    bindSash() {
        const sash = $("sash");
        sash.addEventListener("pointerdown", (event) => {
            sash.setPointerCapture(event.pointerId);
            sash.classList.add("dragging");
            const startX = event.clientX;
            const startWidth = $("sidebar").getBoundingClientRect().width;
            // The Explorer grows towards the editor: leftwards when it is on the right.
            const direction = document.body.classList.contains("explorer-right") ? -1 : 1;
            let width = startWidth;
            const move = (e) => {
                width = Math.round(Math.min(600, Math.max(170, startWidth + direction * (e.clientX - startX))));
                document.documentElement.style.setProperty("--sidebar-width", `${width}px`);
            };
            const up = () => {
                sash.classList.remove("dragging");
                sash.removeEventListener("pointermove", move);
                this.settings.set("workbench.explorerWidth", width)
                    .catch((err) => this.reportError("Saving settings failed", err));
            };
            sash.addEventListener("pointermove", move);
            sash.addEventListener("pointerup", up, { once: true });
        });
    }

    // ---- watchlist ---------------------------------------------------------------------------

    async refreshGroups() {
        if (!this.connection.serverReachable && this.connection.state !== State.CHECKING) return;
        try {
            this.groups = await this.api.groups();
            this.explorer.setGroups(this.groups);
            this.explorer.select(this.activeSymbol);
        } catch (err) {
            this.reportError("Loading watchlist failed", err);
            return;
        }
        this.refreshQuotes();
    }

    /** Current price of every watchlist symbol (the server caches them for a minute). */
    async refreshQuotes() {
        if (!this.connection.healthy || this.quotesLoading || !this.settings.get("explorer.showPrices")) return;
        const symbols = this.explorer.symbols;
        if (!symbols.length) return;
        this.quotesLoading = true;
        try {
            for (let i = 0; i < symbols.length; i += QUOTES_PER_REQUEST) {
                this.explorer.setQuotes(await this.api.quotes(symbols.slice(i, i + QUOTES_PER_REQUEST)));
            }
        } catch {
            /* prices are optional; the status bar already reports provider problems */
        } finally {
            this.quotesLoading = false;
        }
    }

    async moveSymbol(fromGroupId, symbol, toGroupId, { copy = false, before = null } = {}) {
        try {
            this.groups = await this.api.moveSymbol(fromGroupId, symbol, toGroupId, { copy, before });
            this.explorer.setGroups(this.groups);
            this.explorer.select(this.activeSymbol);
        } catch (err) {
            this.reportError(copy ? "Copying symbol failed" : "Moving symbol failed", err);
        }
    }

    async pickMoveTarget(fromGroupId, symbol) {
        const others = this.groups.filter((g) => g.id !== fromGroupId);
        if (!others.length) {
            notify("Create another group first, then move symbols into it.", "info", 5000);
            return;
        }
        const target = await prompt({
            title: `Move ${symbol} to group`,
            placeholder: "Select a group",
            hint: "Tip: on a desktop you can also drag the symbol onto a group (Ctrl+drag copies)",
            items: others.map((g) => {
                const has = g.items.some((i) => i.symbol === symbol);
                return { label: g.name, detail: has ? `already contains ${symbol}` : `${g.items.length} symbols`, value: g.id, icon: "folder" };
            }),
            allowCustom: false,
        });
        if (target !== null && target !== undefined) await this.moveSymbol(fromGroupId, symbol, target);
    }

    async pickGroup(title, symbol = null) {
        if (!this.groups.length) {
            const created = await this.api.createGroup("Watchlist");
            await this.refreshGroups();
            return created.id;
        }
        if (this.groups.length === 1) return this.groups[0].id;
        return prompt({
            title,
            placeholder: "Select a group",
            items: this.groups.map((g) => {
                const has = symbol && g.items.some((i) => i.symbol === symbol);
                return { label: g.name, detail: has ? `already contains ${symbol}` : `${g.items.length} symbols`, value: g.id, icon: "folder" };
            }),
            allowCustom: false,
        });
    }

    /** Search-first: pick the stock (ticker or company name), then the group unless one is given or only one exists. */
    async addSymbol(groupId = null) {
        try {
            const target = groupId === null ? (this.groups.length === 1 ? this.groups[0] : null)
                : this.groups.find((g) => g.id === groupId);
            const picked = await prompt({
                title: target ? `Add symbol to "${target.name}"` : "Add symbol",
                placeholder: "Type a ticker or company name, e.g. AAPL, apple, berkshire",
                hint: "Matches appear as you type. Enter adds the highlighted stock, Escape cancels",
                search: (text) => this.searchItems(text, target?.name),
                validate: (v) => (SYMBOL_RE.test(v.trim()) ? null : "Not a valid ticker symbol"),
            });
            if (!picked) return;
            const symbol = picked.trim().toUpperCase();
            groupId ??= await this.pickGroup(`Add ${symbol} to group`, symbol);
            if (groupId === null) return;
            await this.api.addSymbol(groupId, symbol);
            await this.refreshGroups();
            this.open(symbol);
        } catch (err) {
            this.reportError("Adding symbol failed", err);
        }
    }

    /** Type-ahead results from the data provider, as quick-input items. */
    async searchItems(text, targetGroup = null) {
        if (!this.connection.healthy && this.connection.state !== State.CHECKING) {
            throw new Error(this.outageText() || "data provider unavailable");
        }
        const matches = await this.api.search(text, 12);
        return matches.map((m) => {
            const already = targetGroup && m.groups.includes(targetGroup);
            const where = m.groups.length ? `in ${m.groups.join(", ")}` : "";
            return {
                label: m.symbol,
                description: m.name || "",
                detail: [m.exchange, TYPE_LABELS[m.quote_type] ?? m.quote_type, already ? "already in this group" : where]
                    .filter(Boolean).join(" · "),
                value: m.symbol,
                icon: TYPE_ICONS[m.quote_type] ?? "symbol-event",
            };
        });
    }

    async removeSymbol(groupId, symbol) {
        try {
            await this.api.removeSymbol(groupId, symbol);
            await this.refreshGroups();
        } catch (err) {
            this.reportError("Removing symbol failed", err);
        }
    }

    async newGroup() {
        const name = await prompt({
            title: "New group",
            placeholder: "Group name",
            validate: (v) => (v.trim().length > 64 ? "At most 64 characters" : null),
        });
        if (!name) return;
        try {
            await this.api.createGroup(name.trim());
            await this.refreshGroups();
        } catch (err) {
            this.reportError("Creating group failed", err);
        }
    }

    async renameGroup(group) {
        const name = await prompt({ title: `Rename group "${group.name}"`, value: group.name });
        if (!name || name.trim() === group.name) return;
        try {
            await this.api.renameGroup(group.id, name.trim());
            await this.refreshGroups();
        } catch (err) {
            this.reportError("Renaming group failed", err);
        }
    }

    async deleteGroup(group) {
        if (!(await confirm(`Delete group "${group.name}" and its ${group.items.length} symbols?`, "Delete"))) return;
        try {
            await this.api.deleteGroup(group.id);
            await this.refreshGroups();
        } catch (err) {
            this.reportError("Deleting group failed", err);
        }
    }

    async goToSymbol() {
        const seen = new Map();
        for (const group of this.groups) {
            for (const item of group.items) {
                if (!seen.has(item.symbol)) {
                    seen.set(item.symbol, { label: item.symbol, description: item.name || "", detail: group.name, value: item.symbol, icon: "star-full" });
                }
            }
        }
        const symbol = await prompt({
            title: "Go to symbol",
            placeholder: "Type a ticker or company name - watchlist first, then all markets",
            items: [...seen.values()],
            search: (text) => this.searchItems(text),
            validate: (v) => (SYMBOL_RE.test(v.trim()) ? null : "Not a valid ticker symbol"),
        });
        if (symbol) this.open(symbol.trim().toUpperCase());
    }

    // ---- tabs --------------------------------------------------------------------------------

    open(symbol) {
        if (!this.ui.tabs.includes(symbol)) this.ui.tabs.push(symbol);
        document.body.classList.remove("sidebar-open");
        this.activate(symbol);
    }

    activate(symbol) {
        this.ui.active = symbol;
        this.saveUi();
        this.renderTabs();
        this.explorer.select(this.activeSymbol);
        if (symbol === SETTINGS_TAB) this.settingsPage.render();
        else this.loadChart();
    }

    close(symbol) {
        const index = this.ui.tabs.indexOf(symbol);
        if (index < 0) return;
        this.ui.tabs.splice(index, 1);
        if (this.ui.active === symbol) {
            const next = this.ui.tabs[Math.min(index, this.ui.tabs.length - 1)] ?? null;
            if (next) {
                this.activate(next);
                return;
            }
            this.ui.active = null;
            this.explorer.select(null);
        }
        this.saveUi();
        this.renderTabs();
    }

    renderTabs() {
        const tabs = this.ui.tabs.map((id) => el("div", {
            class: "tab" + (id === this.ui.active ? " active" : ""),
            role: "tab",
            title: id === SETTINGS_TAB ? "Settings" : id,
            onclick: () => this.activate(id),
            onauxclick: (e) => { if (e.button === 1) this.close(id); },
        },
            id === SETTINGS_TAB ? icon("settings-gear") : icon("graph-line"),
            el("span", {}, id === SETTINGS_TAB ? "Settings" : id),
            el("button", {
                class: "icon-button tab-close",
                title: "Close",
                onclick: (e) => { e.stopPropagation(); this.close(id); },
            }, icon("close"))));
        $("tabs").replaceChildren(...tabs);
        const symbol = this.activeSymbol;
        const settingsOpen = this.ui.active === SETTINGS_TAB;
        $("welcome").hidden = Boolean(this.ui.active);
        $("chart-panel").hidden = !symbol;
        $("settings-panel").hidden = !settingsOpen;
        document.querySelector('.activity[data-action="settings"]').classList.toggle("active", settingsOpen);
        document.title = symbol ? `${symbol} — Owl` : settingsOpen ? "Settings — Owl" : "Owl";
        $("window-title").textContent = document.title;
        if (!symbol) {
            $("status-sources").replaceChildren();
            $("status-quote").replaceChildren();
        }
    }

    // ---- chart -------------------------------------------------------------------------------

    renderToolbar() {
        $("range-buttons").replaceChildren(...RANGES.map((range) => el("button", {
            class: range === this.ui.range ? "active" : "",
            onclick: () => { this.ui.range = range; this.saveUi(); this.renderToolbar(); this.loadChart(); },
        }, range)));
        $("interval-buttons").replaceChildren(...INTERVALS.map(([key, label, title]) => el("button", {
            class: key === this.ui.interval ? "active" : "",
            title,
            onclick: () => { this.ui.interval = key; this.saveUi(); this.renderToolbar(); this.loadChart(); },
        }, label)));
    }

    async loadChart() {
        const symbol = this.activeSymbol;
        if (!symbol) return;
        const requestId = ++this.chartRequest;
        this.chart ??= new ChartView($("chart-container"), $("legend"));
        this.lineTool ??= new LineDrawings(this.chart, {
            onChange: (lines) => this.saveDrawings(this.lineSymbol, lines),
            onState: () => this.renderLineTools(),
        });
        if (this.lineSymbol !== symbol) {
            this.lineSymbol = symbol;
            this.lineTool.setBars([]);
            this.lineTool.setLines(this.drawings[symbol] ?? []);
        }
        this.renderBreadcrumbs(symbol, null);
        if (!this.connection.healthy && this.connection.state !== State.CHECKING) {
            this.chartPending = true;
            this.showChartMessage(`${this.outageText()} The chart will load automatically when the connection is back.`, "debug-disconnect");
            return;
        }
        this.chartPending = false;
        this.showChartMessage("Loading…", "sync codicon-modifier-spin");
        try {
            const payload = await this.api.chart(symbol, {
                range: this.ui.range,
                interval: this.ui.interval,
                indicators: this.indicators.filter((i) => i.enabled).map(indicatorKey),
            });
            if (requestId !== this.chartRequest) return;
            this.chart.setData(payload, this.indicators);
            this.lineTool.setBars(payload.bars);
            this.showChartMessage(null);
            this.renderBreadcrumbs(symbol, payload);
            this.renderSources(payload);
        } catch (err) {
            if (requestId !== this.chartRequest) return;
            if ([0, 502, 503, 504].includes(err.status)) {
                // Outage: the connection monitor re-checks now and reloads the chart on recovery.
                this.chartPending = true;
                this.showChartMessage(`${err.message}. The chart will load automatically when the data provider is back.`, "debug-disconnect");
                return;
            }
            const message = err.status === 404 ? `No price data found for ${symbol}` : err.message;
            this.showChartMessage(message, "warning");
        }
    }

    showChartMessage(text, iconName = "info") {
        const box = $("chart-message");
        box.hidden = !text;
        if (text) box.replaceChildren(icon(iconName), el("span", {}, text));
    }

    renderBreadcrumbs(symbol, payload) {
        const item = this.groups.flatMap((g) => g.items.map((i) => ({ ...i, group: g.name }))).find((i) => i.symbol === symbol);
        const parts = [
            el("span", {}, item?.group ?? "Chart"),
            icon("chevron-right"),
            el("span", { class: "crumb-symbol" }, symbol),
            item?.name ? el("span", {}, item.name) : null,
        ];
        const quote = payload?.quote;
        if (quote) {
            const up = (quote.change ?? 0) >= 0;
            parts.push(
                el("span", { class: "price" }, formatNumber(quote.close)),
                quote.change === null ? null : el("span", { class: up ? "up" : "down" },
                    `${up ? "+" : ""}${formatNumber(quote.change)} (${up ? "+" : ""}${formatNumber(quote.change_percent)}%)`),
                el("span", {}, `as of ${quote.date}`));
            $("status-quote").replaceChildren(icon("pulse"), `${symbol} ${formatNumber(quote.close)}`);
        }
        $("breadcrumbs").replaceChildren(...parts.filter(Boolean));
    }

    renderSources(payload) {
        const sources = Object.entries(payload.sources || {}).map(([name, count]) => `${name} ${count}`).join(" · ");
        const node = $("status-sources");
        node.title = "Price bars per data provider";
        node.replaceChildren(icon("database"), sources || "—");
    }

    // ---- indicators --------------------------------------------------------------------------

    async loadSettings() {
        let saved = null;
        try {
            saved = await this.api.getSetting("chart");
        } catch {
            saved = JSON.parse(localStorage.getItem("owl.chart") || "null");
        }
        if (saved?.indicators?.length) this.indicators = saved.indicators;
        await this.loadDrawings();
    }

    // ---- trend lines -------------------------------------------------------------------------

    async loadDrawings() {
        let saved;
        try {
            saved = await this.api.getSetting("drawings");
        } catch {
            try {
                saved = JSON.parse(localStorage.getItem("owl.drawings") || "null");
            } catch {
                saved = null;
            }
        }
        this.drawings = saved && typeof saved === "object" && !Array.isArray(saved) ? saved : {};
        if (this.lineTool && this.lineSymbol && !this.lineTool.drag) this.lineTool.setLines(this.drawings[this.lineSymbol] ?? []);
    }

    async saveDrawings(symbol, lines) {
        if (!symbol) return;
        if (lines.length) this.drawings[symbol] = lines;
        else delete this.drawings[symbol];
        localStorage.setItem("owl.drawings", JSON.stringify(this.drawings));
        try {
            await this.api.putSetting("drawings", this.drawings);
        } catch (err) {
            this.reportError("Saving lines failed", err);
        }
    }

    toggleLineDrawing() {
        if (!this.lineTool) return;
        if (this.lineTool.drawing) this.lineTool.cancelDrawing();
        else if (!this.lineTool.startDrawing()) notify(`At most ${MAX_LINES_PER_SYMBOL} lines per symbol`, "warning");
    }

    async clearLines() {
        const count = this.lineTool?.lines.length ?? 0;
        if (!count || !(await confirm(`Delete all ${count} line${count === 1 ? "" : "s"} of ${this.lineSymbol}?`, "Delete"))) return;
        this.lineTool.deleteAll();
    }

    async clearAllDrawings() {
        if (!Object.keys(this.drawings).length || !(await confirm("Delete the lines of all symbols?", "Delete"))) return;
        this.drawings = {};
        this.lineTool?.setLines([]);
        localStorage.setItem("owl.drawings", "{}");
        try {
            await this.api.putSetting("drawings", {});
        } catch (err) {
            this.reportError("Saving lines failed", err);
        }
        this.settingsPage.render();
    }

    renderLineTools() {
        const tool = this.lineTool;
        $("draw-line-button").classList.toggle("active", Boolean(tool?.drawing));
        $("delete-line-button").disabled = !tool?.selected;
        $("clear-lines-button").disabled = !tool?.lines.length;
    }

    async saveIndicators() {
        const value = { indicators: this.indicators };
        localStorage.setItem("owl.chart", JSON.stringify(value));
        try {
            await this.api.putSetting("chart", value);
        } catch (err) {
            this.reportError("Saving indicator settings failed", err);
        }
        this.loadChart();
    }

    toggleIndicatorMenu() {
        const menu = $("indicators-menu");
        if (!menu.hidden) {
            menu.hidden = true;
            return;
        }
        this.renderIndicatorMenu();
        const rect = $("indicators-button").getBoundingClientRect();
        menu.style.left = `${Math.min(rect.left, window.innerWidth - 240)}px`;
        menu.style.top = `${rect.bottom + 4}px`;
        menu.hidden = false;
    }

    renderIndicatorMenu() {
        const rows = this.indicators.map((indicator, index) => el("div", {
            class: "menu-item",
            onclick: (e) => {
                e.stopPropagation();
                indicator.enabled = !indicator.enabled;
                this.renderIndicatorMenu();
                this.saveIndicators();
            },
        },
            el("span", { class: "check" }, indicator.enabled ? icon("check") : null),
            el("span", { class: "swatch", style: `background:${indicator.color}` }),
            el("span", { class: "menu-label" }, indicatorLabel(indicator)),
            el("button", {
                class: "icon-button",
                title: "Remove",
                onclick: (e) => {
                    e.stopPropagation();
                    this.indicators.splice(index, 1);
                    this.renderIndicatorMenu();
                    this.saveIndicators();
                },
            }, icon("trash"))));
        rows.push(
            el("div", { class: "menu-separator" }),
            el("div", { class: "menu-item", onclick: () => this.addIndicator() }, el("span", { class: "check" }, icon("add")), el("span", { class: "menu-label" }, "Add custom indicator…")),
            el("div", {
                class: "menu-item",
                onclick: (e) => {
                    e.stopPropagation();
                    this.indicators = DEFAULT_INDICATORS.map((i) => ({ ...i }));
                    this.renderIndicatorMenu();
                    this.saveIndicators();
                },
            }, el("span", { class: "check" }, icon("discard")), el("span", { class: "menu-label" }, "Reset to MA 20 / 50 / 200")));
        $("indicators-menu").replaceChildren(...rows);
    }

    async addIndicator() {
        $("indicators-menu").hidden = true;
        const value = await prompt({
            title: "Add indicator",
            placeholder: "Type and period, e.g. \"sma 100\" or \"ema 21\"",
            hint: "sma = simple moving average, ema = exponential moving average (period 1-1000)",
            validate: (v) => {
                const match = INDICATOR_RE.exec(v.trim());
                if (!match) return "Use: sma <period> or ema <period>";
                const period = Number(match[2]);
                if (period < 1 || period > 1000) return "Period must be between 1 and 1000";
                return null;
            },
        });
        if (!value) return;
        const match = INDICATOR_RE.exec(value.trim());
        const type = match[1].toLowerCase() === "ema" ? "ema" : "sma";
        const indicator = { type, period: Number(match[2]), enabled: true };
        if (this.indicators.some((i) => indicatorKey(i) === indicatorKey(indicator))) {
            notify(`${indicatorLabel(indicator)} is already on the chart`, "info");
            return;
        }
        if (this.indicators.length >= 10) {
            notify("At most 10 indicators", "warning");
            return;
        }
        indicator.color = INDICATOR_COLORS[this.indicators.length % INDICATOR_COLORS.length];
        this.indicators.push(indicator);
        this.saveIndicators();
    }

    // ---- status / connection -----------------------------------------------------------------

    onConnectionChange({ previous, state }) {
        this.renderConnection();
        if (this.connection.status) $("status-version").textContent = `Owl ${this.connection.status.version}`;
        this.checkWebBuild(this.connection.status?.web_build);
        if (previous === state || previous === State.CHECKING) return;

        const wasHealthy = previous === State.ONLINE || previous === State.DEGRADED;
        const wasReachable = wasHealthy || previous === State.PROVIDER_DOWN;
        if (this.connection.healthy && !wasHealthy) {
            notify(previous === State.OFFLINE ? "Reconnected to the owl server" : "Data provider is available again - reconnected", "info", 5000);
        } else if (state === State.DEGRADED && previous === State.ONLINE) {
            const names = this.connection.unavailableProviders.map((p) => p.name).join(", ");
            notify(`Some data providers are unavailable (${names}); prices come from the remaining ones.`, "warning", 8000);
        } else if (state === State.ONLINE && previous === State.DEGRADED) {
            notify("All data providers are available again", "info", 4000);
        }
        if (this.connection.serverReachable && !wasReachable) {
            this.loadSettings();
            this.settings.load();
            this.refreshGroups();
        } else if (this.connection.healthy && !wasHealthy) {
            this.refreshQuotes();
        }
        if (this.connection.healthy && (!wasHealthy || this.chartPending)) this.loadChart();
        else if (!this.connection.healthy && wasHealthy && this.activeSymbol) this.loadChart();
    }

    /** A tab left open across an Owl update keeps running the old UI code until reloaded. */
    checkWebBuild(build) {
        if (!build) return;
        this.webBuild ??= build;
        if (build === this.webBuild || this.reloadOffered) return;
        this.reloadOffered = true;
        notify("Owl was updated. Reload to use the new version.", "info", 0,
            [{ label: "Reload", run: () => location.reload() }]);
    }

    outageText() {
        const c = this.connection;
        switch (c.state) {
            case State.OFFLINE:
                return `Cannot reach the owl server${this.api.baseUrl ? ` (${this.api.baseUrl})` : ""}.`;
            case State.UNAUTHORIZED:
                return "The owl server rejected the API key.";
            case State.PROVIDER_DOWN: {
                const detail = c.status?.aggregator?.detail || "no provider answers";
                return `The data provider is unavailable: ${detail.length > 140 ? `${detail.slice(0, 140)}…` : detail}.`;
            }
            default:
                return "";
        }
    }

    renderConnection() {
        const c = this.connection;
        const retry = c.retryInSeconds;
        const retryText = retry === null ? "checking now" : `retrying in ${retry}s`;
        const aggregator = c.status?.aggregator;
        const item = $("status-connection");
        const views = {
            [State.CHECKING]: ["sync codicon-modifier-spin", "Connecting…", ""],
            [State.ONLINE]: ["plug", "Data: online", ""],
            [State.DEGRADED]: ["warning", `Data: degraded (${aggregator?.detail ?? ""})`, "warning"],
            [State.PROVIDER_DOWN]: ["debug-disconnect", `Data provider unavailable - ${retryText}`, "error"],
            [State.OFFLINE]: ["debug-disconnect", `Owl server offline - ${retryText}`, "error"],
            [State.UNAUTHORIZED]: ["lock", "Unauthorized - check the API key", "error"],
        };
        const [iconName, text, tone] = views[c.state];
        item.replaceChildren(icon(iconName), text);
        item.title = "Data provider status (click for details)";
        item.className = `status-item ${tone ? `status-${tone}` : ""}`.trim();
        $("statusbar").classList.toggle("offline", tone === "error");
        this.renderBanner(retryText);
    }

    renderBanner(retryText) {
        const banner = $("banner");
        const c = this.connection;
        if (c.healthy || c.state === State.CHECKING) {
            banner.hidden = true;
            return;
        }
        const actions = [
            c.state !== State.UNAUTHORIZED
                ? el("button", { class: "banner-button", "data-action": "retry-connection" }, "Retry now")
                : null,
            c.state === State.PROVIDER_DOWN
                ? el("button", { class: "banner-button secondary", "data-action": "connection-details" }, "Details")
                : el("button", { class: "banner-button secondary", "data-action": "edit-connection" }, "Settings"),
        ];
        const consequence = c.state === State.PROVIDER_DOWN
            ? "Prices cannot be loaded; the watchlist still works."
            : "Watchlist and prices cannot be loaded.";
        banner.replaceChildren(
            icon(c.state === State.UNAUTHORIZED ? "lock" : "debug-disconnect"),
            el("span", { class: "banner-text" }, `${this.outageText()} ${consequence} `,
                c.state === State.UNAUTHORIZED ? null : el("span", { class: "banner-retry" }, `Reconnecting automatically, ${retryText}.`)),
            ...actions.filter(Boolean));
        banner.hidden = false;
    }

    async showStatusDetails() {
        await this.connection.check(true);
        const c = this.connection;
        const providers = c.status?.aggregator?.providers ?? [];
        const items = [{ label: "Retry now", detail: "Check the connection again", value: "__retry", icon: "refresh" }];
        if (!c.serverReachable) {
            items.push({ label: "Owl server", detail: c.error?.message ?? c.state, value: "__server", icon: "error" });
        }
        providers.forEach((p, index) => items.push({
            label: `${index + 1}. ${p.name} - ${p.state}`,
            detail: p.detail,
            value: p.name,
            icon: p.state === "unavailable" ? "error" : "pass",
        }));
        const choice = await prompt({
            title: `Data providers (fallback order) - ${c.status?.aggregator?.state ?? c.state}`,
            placeholder: "Provider status",
            items,
            allowCustom: false,
        });
        if (choice === "__retry") this.connection.check(true);
    }

    async editConnection() {
        const current = loadConnection();
        const url = await prompt({
            title: "Owl server URL (leave empty when the UI is served by owl itself)",
            placeholder: "e.g. http://192.168.1.20:8710",
            value: current.url,
            validate: (v) => (!v.trim() || /^https?:\/\/[^\s/]+/i.test(v.trim()) ? null : "Must start with http:// or https://"),
        });
        if (url === null) return;
        const apiKey = await prompt({
            title: "API key (leave empty if the server has none)",
            placeholder: "X-API-Key",
            value: current.apiKey,
        });
        if (apiKey === null) return;
        const connection = { url: url.trim(), apiKey: apiKey.trim() };
        saveConnection(connection);
        this.api.configure(connection);
        notify("Connection settings saved", "info", 3000);
        await this.connection.check(true);
        await Promise.all([this.loadSettings(), this.settings.load(), this.refreshGroups()]);
        if (this.ui.active === SETTINGS_TAB) this.settingsPage.render();
        this.loadChart();
    }

    reportError(prefix, err) {
        // Outages are shown by the connection banner; avoid a second, noisier message.
        if (err instanceof ApiError && [0, 401, 503].includes(err.status) && !this.connection.healthy) return;
        const message = err instanceof ApiError ? err.message : String(err);
        notify(`${prefix}: ${message}`, "error");
    }
}

const app = new OwlApp();
app.start();
