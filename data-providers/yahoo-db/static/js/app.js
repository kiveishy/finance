// Yahoo DB dashboard: views (dashboard, tickers, schedule, API), status bar and notifications.
import { describeMarket } from "./market.js";

const $ = (id) => document.getElementById(id);
const VIEWS = ["dashboard", "tickers", "schedule", "api"];
const TIMEZONES = [
    ["America/New_York", "New York (US market time)"],
    ["local", "Server local time"],
    ["UTC", "UTC"],
    ["Asia/Jerusalem", "Jerusalem"],
    ["Europe/London", "London"],
];
const API_ENDPOINTS = [
    ["GET", "/api/tickers", "All tickers in the database"],
    ["GET", "/api/ticker/{symbol}", "Ticker information"],
    ["GET", "/api/ticker/{symbol}/history?days=30", "Daily prices (also months=, years=, start_date= & end_date=)"],
    ["GET", "/api/ticker/{symbol}/updates", "Update history of a ticker"],
    ["GET", "/api/ticker/{symbol}/splits", "Splits taken care of, and the ticker's full split check"],
    ["POST", "/api/ticker/{symbol}/check-splits", "Check the whole history for splits now (?force=1 re-downloads)"],
    ["GET", "/api/search?q=apple&source=db|yahoo", "Ticker search by symbol or company name"],
    ["POST", "/api/ticker", "Add a ticker {\"symbol\": \"AAPL\"}"],
    ["GET", "/api/sync/status", "Update status and progress"],
    ["POST", "/api/sync/start", "Start an update"],
    ["POST", "/api/sync/stop", "Stop the running update"],
    ["GET", "/api/schedule", "Daily update schedule, next and last run"],
    ["PUT", "/api/schedule", "Change the schedule {\"enabled\", \"run_time\": \"23:30\", \"timezone\", \"catch_up\"}"],
];

// ---- helpers --------------------------------------------------------------------------------

function el(tag, props = {}, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(props)) {
        if (value === undefined || value === null || value === false) continue;
        if (key === "class") node.className = value;
        else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
        else node.setAttribute(key, value === true ? "" : value);
    }
    for (const child of children.flat()) {
        if (child !== null && child !== undefined && child !== false) node.append(child instanceof Node ? child : String(child));
    }
    return node;
}

const icon = (name, extra = "") => el("i", { class: `codicon codicon-${name} ${extra}`.trim() });

function notify(message, kind = "info", timeout = 6000) {
    const iconName = { error: "error", warning: "warning", success: "pass" }[kind] ?? "info";
    const item = el("div", { class: "notification" },
        icon(iconName),
        el("div", { class: "text" }, message),
        el("button", { class: "icon-button", title: "Close", onclick: () => item.remove() }, icon("close")));
    $("notifications").append(item);
    if (timeout) setTimeout(() => item.remove(), timeout);
}

async function api(path, { method = "GET", body } = {}) {
    const response = await fetch(path, {
        method,
        cache: "no-store",
        headers: body === undefined ? {} : { "Content-Type": "application/json" },
        body: body === undefined ? undefined : JSON.stringify(body),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data.status === "error") throw new Error(data.error || `HTTP ${response.status}`);
    return data;
}

const number = (value, digits = 2) => (value == null ? "—" : Number(value).toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits }));
const integer = (value) => (value == null ? "—" : Number(value).toLocaleString());
const dateTime = (iso) => (iso ? new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "—");
// SQLite CURRENT_TIMESTAMP values are UTC without a zone: "2026-11-11 10:00:00".
const sqliteTime = (value) => dateTime(value && /^\d{4}-\d\d-\d\d \d/.test(value) ? `${value.replace(" ", "T")}Z` : value);

function zoneTime(iso, zone) {
    if (!iso) return "—";
    const options = { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", hourCycle: "h23" };
    if (zone && zone !== "local") options.timeZone = zone;
    return new Date(iso).toLocaleString("en-GB", options);
}

function zoneLabel(zone) {
    return TIMEZONES.find(([value]) => value === zone)?.[1] ?? zone;
}

function relative(iso) {
    const minutes = Math.round((new Date(iso) - Date.now()) / 60000);
    if (Math.abs(minutes) < 1) return "now";
    const abs = Math.abs(minutes);
    const text = abs >= 1440 ? `${Math.floor(abs / 1440)}d ${Math.floor((abs % 1440) / 60)}h`
        : abs >= 60 ? `${Math.floor(abs / 60)}h ${String(abs % 60).padStart(2, "0")}m` : `${abs}m`;
    return minutes > 0 ? `in ${text}` : `${text} ago`;
}

function stateClass(status) {
    if (!status) return "";
    if (status.startsWith("error")) return "state-error";
    return `state-${status.split(":")[0]}`;
}

// ---- type-ahead -----------------------------------------------------------------------------

/** Suggests tickers under `input` (via /api/search). Enter/click picks, Escape closes. */
function attachSuggest(input, { source, onSubmit, submitOnPick }) {
    const list = el("div", { class: "suggest-list", hidden: true });
    input.parentElement.append(list);
    let items = [];
    let active = -1;
    let timer = null;
    let seq = 0;

    const close = () => {
        clearTimeout(timer);
        seq += 1;
        items = [];
        active = -1;
        list.hidden = true;
    };
    const render = (message) => {
        list.replaceChildren(...[
            message ? el("div", { class: "suggest-msg" }, message) : null,
            ...items.map((item, index) => el("div", {
                class: `suggest-item${index === active ? " active" : ""}`,
                onmousedown: (e) => { e.preventDefault(); pick(index); },
            },
                el("span", { class: "suggest-symbol" }, item.symbol),
                el("span", { class: "suggest-name" }, item.long_name || item.short_name || ""),
                el("span", { class: "suggest-meta" }, [item.exchange, item.quote_type, item.in_database ? "in database" : ""].filter(Boolean).join(" · ")))),
        ].filter(Boolean));
        list.hidden = !message && !items.length;
        list.querySelector(".active")?.scrollIntoView({ block: "nearest" });
    };
    const pick = (index) => {
        input.value = items[index].symbol;
        close();
        if (submitOnPick) onSubmit();
    };

    input.addEventListener("input", () => {
        clearTimeout(timer);
        const query = input.value.trim();
        const mySeq = ++seq;
        if (!query) {
            close();
            return;
        }
        timer = setTimeout(async () => {
            try {
                const data = await api(`/api/search?${new URLSearchParams({ q: query, limit: "10", source })}`);
                if (mySeq !== seq) return;
                items = data.data;
                const exact = items.findIndex((i) => i.symbol.toUpperCase() === query.toUpperCase());
                active = items.length ? Math.max(exact, 0) : -1;
                render(items.length ? "" : "No matches");
            } catch (err) {
                if (mySeq === seq) {
                    items = [];
                    render(err.message || "Search unavailable");
                }
            }
        }, 200);
    });
    input.addEventListener("keydown", (e) => {
        const open = !list.hidden && items.length > 0;
        if ((e.key === "ArrowDown" || e.key === "ArrowUp") && open) {
            e.preventDefault();
            active = (active + (e.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
            render("");
        } else if (e.key === "Enter") {
            e.preventDefault();
            if (open && active >= 0) pick(active);
            else {
                close();
                onSubmit();
            }
        } else if (e.key === "Escape") close();
    });
    input.addEventListener("blur", close);
}

// ---- app ------------------------------------------------------------------------------------

class YahooDbApp {
    constructor() {
        this.status = null;
        this.schedule = null;
        this.scheduleDirty = false;
        this.detailSymbol = null;
        this.offline = false;
        this.bind();
        this.renderApiTable();
        this.renderMarket();
        setInterval(() => this.renderMarket(), 15_000);
        this.route();
        this.pollStatus();
        this.loadSchedule();
        setInterval(() => this.renderScheduleTimes(), 30_000);
    }

    bind() {
        window.addEventListener("hashchange", () => this.route());
        document.addEventListener("click", (event) => {
            const view = event.target.closest("[data-view]")?.dataset.view;
            const goto = event.target.closest("[data-goto]")?.dataset.goto;
            if (view || goto) location.hash = view || goto;
        });
        $("btn-start-sync").addEventListener("click", () => this.startSync());
        $("btn-stop-sync").addEventListener("click", () => this.stopSync());
        $("btn-add-ticker").addEventListener("click", () => this.addTicker());
        attachSuggest($("ticker-input"), { source: "yahoo", onSubmit: () => this.addTicker(), submitOnPick: false });
        attachSuggest($("detail-ticker-input"), {
            source: "db",
            onSubmit: () => {
                const symbol = $("detail-ticker-input").value.trim().toUpperCase();
                if (symbol) location.hash = `tickers/${encodeURIComponent(symbol)}`;
            },
            submitOnPick: true,
        });
        for (const tab of document.querySelectorAll(".tab")) tab.addEventListener("click", () => this.showTab(tab.dataset.tab));
        $("history-period").addEventListener("change", () => this.loadHistory());

        const form = $("schedule-form");
        form.addEventListener("submit", (event) => {
            event.preventDefault();
            this.saveSchedule();
        });
        form.addEventListener("input", () => this.markScheduleDirty());
        form.addEventListener("change", () => this.markScheduleDirty());
        $("btn-reset-schedule").addEventListener("click", () => {
            this.scheduleDirty = false;
            this.fillScheduleForm();
        });
        window.addEventListener("beforeunload", (event) => {
            if (this.scheduleDirty) event.preventDefault();
        });
    }

    route() {
        const [view = "dashboard", symbol] = location.hash.replace(/^#/, "").split("/");
        const current = VIEWS.includes(view) ? view : "dashboard";
        for (const name of VIEWS) $(`view-${name}`).hidden = name !== current;
        for (const button of document.querySelectorAll(".activity")) button.classList.toggle("active", button.dataset.view === current);
        document.title = `${current[0].toUpperCase()}${current.slice(1)} — Yahoo DB`;
        if (current === "tickers" && symbol) {
            const decoded = decodeURIComponent(symbol).toUpperCase();
            if (decoded !== this.detailSymbol) this.openTicker(decoded);
        }
        if (current === "schedule") this.loadSchedule();
    }

    renderMarket() {
        const market = describeMarket();
        const node = $("market-status");
        node.className = `market-status ${market.state}`;
        node.textContent = market.text;
        node.title = market.title;
    }

    // ---- status ---------------------------------------------------------------------------

    async pollStatus() {
        clearTimeout(this.statusTimer);
        try {
            this.status = await api("/api/sync/status");
            this.offline = false;
            this.renderStatus();
        } catch {
            this.offline = true;
            $("statusbar").className = "offline";
            $("status-sync").replaceChildren(icon("debug-disconnect"), "Service unreachable - retrying");
        }
        const delay = this.status?.sync_running ? 2000 : 5000;
        this.statusTimer = setTimeout(() => this.pollStatus(), this.offline ? 5000 : delay);
    }

    renderStatus() {
        const s = this.status;
        const running = s.sync_running;
        const label = s.sync_status ? s.sync_status[0].toUpperCase() + s.sync_status.slice(1) : "—";
        $("stat-status").textContent = running ? "Running" : label;
        $("stat-status").className = `stat-value ${running ? "state-running" : stateClass(s.sync_status)}`;
        $("stat-tickers").textContent = integer(s.ticker_count);
        $("stat-last-sync").textContent = dateTime(s.last_sync_time);
        $("btn-start-sync").disabled = running;
        $("btn-stop-sync").disabled = !running;

        const progress = s.progress || {};
        const showProgress = running && progress.total > 0;
        $("progress-card").hidden = !showProgress;
        if (showProgress) {
            const percent = (progress.current / progress.total) * 100;
            $("progress-fill").style.width = `${percent}%`;
            $("progress-text").textContent = `Updating ${progress.current_ticker || "…"} (${integer(progress.current)} of ${integer(progress.total)})`;
            $("progress-percent").textContent = `${percent.toFixed(1)}%`;
        }

        $("statusbar").className = running ? "syncing" : "";
        $("status-sync").replaceChildren(
            icon(running ? "sync" : s.sync_status?.startsWith("error") ? "error" : "check", running ? "codicon-modifier-spin" : ""),
            running ? (showProgress ? `Updating ${integer(progress.current)}/${integer(progress.total)}` : "Updating…") : `Update: ${label}`);
        $("status-tickers").replaceChildren(icon("database"), `${integer(s.ticker_count)} tickers`);
        $("status-version").textContent = s.version ? `Yahoo DB ${s.version}` : "Yahoo DB";
        if (s.schedule) this.renderScheduleSummary(s.schedule);
    }

    renderScheduleSummary(schedule) {
        const text = schedule.enabled && schedule.next_run
            ? `${zoneTime(schedule.next_run, schedule.timezone)} (${relative(schedule.next_run)})`
            : "Disabled";
        $("stat-next-run").textContent = text;
        $("status-schedule").replaceChildren(icon("watch"), schedule.enabled
            ? `Daily update ${schedule.run_time} ${schedule.timezone === "local" ? "server time" : schedule.timezone}`
            : "Daily update off");
    }

    async startSync() {
        try {
            await api("/api/sync/start", { method: "POST" });
            notify("Update started", "success", 3000);
        } catch (err) {
            notify(`Could not start the update: ${err.message}`, "error");
        }
        this.pollStatus();
    }

    async stopSync() {
        try {
            await api("/api/sync/stop", { method: "POST" });
            notify("Stop requested - the update stops after the current ticker", "info", 4000);
        } catch (err) {
            notify(`Could not stop the update: ${err.message}`, "error");
        }
        this.pollStatus();
    }

    // ---- tickers ---------------------------------------------------------------------------

    async addTicker() {
        const input = $("ticker-input");
        const symbol = input.value.trim().toUpperCase();
        if (!symbol) {
            notify("Enter a ticker symbol to add", "warning", 3000);
            return;
        }
        const button = $("btn-add-ticker");
        button.disabled = true;
        try {
            const data = await api("/api/ticker", { method: "POST", body: { symbol } });
            notify(`${symbol} added (${integer(data.records_added)} price records)`, "success");
            input.value = "";
            location.hash = `tickers/${encodeURIComponent(symbol)}`;
            this.pollStatus();
        } catch (err) {
            notify(`Could not add ${symbol}: ${err.message}`, "error");
        } finally {
            button.disabled = false;
        }
    }

    openTicker(symbol) {
        this.detailSymbol = symbol;
        $("detail-ticker-input").value = symbol;
        $("detail").hidden = false;
        $("detail-empty").hidden = true;
        $("detail-title").textContent = symbol;
        $("detail-subtitle").textContent = "Loading…";
        this.showTab("info");
        this.loadInfo();
        this.loadHistory();
        this.loadUpdates();
        this.loadSplits();
    }

    showTab(name) {
        for (const tab of document.querySelectorAll(".tab")) tab.classList.toggle("active", tab.dataset.tab === name);
        for (const panel of document.querySelectorAll(".tab-panel")) panel.hidden = panel.id !== `tab-${name}`;
    }

    async loadInfo() {
        const symbol = this.detailSymbol;
        const panel = $("tab-info");
        try {
            const d = (await api(`/api/ticker/${encodeURIComponent(symbol)}`)).data;
            if (symbol !== this.detailSymbol) return;
            $("detail-title").textContent = `${d.long_name || d.short_name || symbol} (${symbol})`;
            $("detail-subtitle").textContent = [d.exchange, d.quote_type, d.currency].filter(Boolean).join(" · ");
            const website = /^https?:\/\//i.test(d.website || "")
                ? el("a", { href: d.website, target: "_blank", rel: "noopener noreferrer" }, d.website) : d.website;
            const fields = [
                ["Symbol", d.symbol], ["Short name", d.short_name], ["Sector", d.sector], ["Industry", d.industry],
                ["Exchange", d.exchange], ["Market cap", d.market_cap ? `$${integer(d.market_cap)}` : null],
                ["Currency", d.currency], ["Country", d.country], ["Quote type", d.quote_type],
                ["Employees", d.employees ? integer(d.employees) : null], ["Website", website],
                ["Added", sqliteTime(d.created_at)], ["Last updated", sqliteTime(d.updated_at)],
            ];
            panel.replaceChildren(el("div", { class: "info-grid" },
                fields.map(([label, value]) => el("div", { class: "info-item" },
                    el("div", { class: "info-label" }, label), el("div", { class: "info-value" }, value || "—"))),
                d.description ? el("div", { class: "info-item wide" },
                    el("div", { class: "info-label" }, "Description"), el("div", { class: "info-value" }, d.description)) : null));
        } catch (err) {
            if (symbol !== this.detailSymbol) return;
            $("detail-subtitle").textContent = "";
            panel.replaceChildren(this.empty("warning", err.message.includes("not found") ? `${symbol} is not in the database. Add it above.` : err.message));
        }
    }

    async loadHistory() {
        const symbol = this.detailSymbol;
        if (!symbol) return;
        const target = $("history-table");
        try {
            const data = await api(`/api/ticker/${encodeURIComponent(symbol)}/history?days=${$("history-period").value}`);
            if (symbol !== this.detailSymbol) return;
            if (!data.data.length) {
                target.replaceChildren(this.empty("graph-line", "No prices in this period"));
                return;
            }
            const rows = [...data.data].reverse();
            target.replaceChildren(el("table", { class: "table" },
                el("thead", {}, el("tr", {}, ["Date", "Open", "High", "Low", "Close", "Volume"].map((h, i) => el("th", { class: i ? "num" : "" }, h)))),
                el("tbody", {}, rows.map((r) => el("tr", {},
                    el("td", {}, r.date), el("td", { class: "num" }, number(r.open)), el("td", { class: "num" }, number(r.high)),
                    el("td", { class: "num" }, number(r.low)), el("td", { class: "num" }, number(r.close)), el("td", { class: "num" }, integer(r.volume)))))));
        } catch (err) {
            if (symbol === this.detailSymbol) target.replaceChildren(this.empty("warning", err.message));
        }
    }

    async loadUpdates() {
        const symbol = this.detailSymbol;
        const panel = $("tab-updates");
        try {
            const data = await api(`/api/ticker/${encodeURIComponent(symbol)}/updates`);
            if (symbol !== this.detailSymbol) return;
            const s = data.summary || {};
            const cards = el("div", { class: "cards" },
                [["Updates", integer(s.total_updates)], ["Successful", integer(s.successful_updates)], ["Failed", integer(s.failed_updates)],
                    ["Records written", integer(s.total_records_updated || 0)], ["First update", sqliteTime(s.first_update)], ["Last update", sqliteTime(s.last_update)]]
                    .map(([label, value]) => el("div", { class: "card stat" }, el("div", { class: "stat-label" }, label), el("div", { class: "stat-value small" }, value))));
            const table = data.data.length ? el("div", { class: "table-wrap" }, el("table", { class: "table" },
                el("thead", {}, el("tr", {}, ["Date", "Type", "Records", "Status", "Error"].map((h) => el("th", {}, h)))),
                el("tbody", {}, data.data.map((r) => el("tr", {},
                    el("td", {}, sqliteTime(r.updated_at)), el("td", {}, r.update_type), el("td", { class: "num" }, integer(r.records_updated)),
                    el("td", {}, el("span", { class: `badge ${r.status === "success" ? "success" : "error"}` }, r.status)),
                    el("td", { class: "muted" }, r.error_message || "")))))) : this.empty("history", "No updates recorded yet");
            panel.replaceChildren(cards, el("div", { style: "height: 12px" }), table);
        } catch (err) {
            if (symbol === this.detailSymbol) panel.replaceChildren(this.empty("warning", err.message));
        }
    }

    async loadSplits() {
        const symbol = this.detailSymbol;
        const panel = $("tab-splits");
        try {
            const data = await api(`/api/ticker/${encodeURIComponent(symbol)}/splits`);
            if (symbol !== this.detailSymbol) return;
            const check = data.check;
            const summary = el("div", { class: "card" },
                el("div", { class: "progress-head" },
                    el("span", {}, check ? `Full split check: ${check.result}${check.mismatched_bars ? ` (${check.mismatched_bars} bars fixed)` : ""} · ${sqliteTime(check.checked_at)}` : "The whole history has not been checked for splits yet."),
                    el("button", { class: "button secondary", onclick: () => this.checkSplits() }, icon("refresh"), "Check now")));
            const table = data.data.length ? el("div", { class: "table-wrap" }, el("table", { class: "table" },
                el("thead", {}, el("tr", {}, ["Split date", "Ratio", "Action", "Taken care of"].map((h) => el("th", {}, h)))),
                el("tbody", {}, data.data.map((r) => el("tr", {},
                    el("td", {}, r.split_date), el("td", {}, r.ratio >= 1 ? `${number(r.ratio, 0)}:1` : `1:${number(1 / r.ratio, 0)}`),
                    el("td", {}, r.action), el("td", {}, sqliteTime(r.handled_at))))))) : this.empty("split-horizontal", "No splits recorded for this ticker");
            panel.replaceChildren(summary, el("div", { style: "height: 12px" }), table);
        } catch (err) {
            if (symbol === this.detailSymbol) panel.replaceChildren(this.empty("warning", err.message));
        }
    }

    async checkSplits() {
        const symbol = this.detailSymbol;
        notify(`Checking ${symbol} for splits…`, "info", 3000);
        try {
            const result = await api(`/api/ticker/${encodeURIComponent(symbol)}/check-splits`, { method: "POST" });
            notify(`${symbol}: ${result.action}${result.splits?.length ? ` (${result.splits.length} split${result.splits.length === 1 ? "" : "s"})` : ""}`, "success");
        } catch (err) {
            notify(`Split check of ${symbol} failed: ${err.message}`, "error");
        }
        if (symbol === this.detailSymbol) {
            this.loadSplits();
            this.loadHistory();
        }
    }

    empty(iconName, text) {
        return el("div", { class: "empty" }, icon(iconName), el("p", {}, text));
    }

    // ---- schedule --------------------------------------------------------------------------

    async loadSchedule() {
        try {
            this.schedule = (await api("/api/schedule")).data;
        } catch (err) {
            notify(`Could not load the schedule: ${err.message}`, "error");
            return;
        }
        if (!this.scheduleDirty) this.fillScheduleForm();
        this.renderScheduleTimes();
    }

    fillScheduleForm() {
        const s = this.schedule;
        if (!s) return;
        $("schedule-enabled").checked = s.enabled;
        $("schedule-time").value = s.run_time;
        $("schedule-catch-up").checked = s.catch_up;
        const zones = TIMEZONES.some(([value]) => value === s.timezone) ? TIMEZONES : [...TIMEZONES, [s.timezone, s.timezone]];
        $("schedule-timezone").replaceChildren(...zones.map(([value, label]) => el("option", { value }, label)));
        $("schedule-timezone").value = s.timezone;
        this.markScheduleDirty(false);
    }

    formSchedule() {
        return {
            enabled: $("schedule-enabled").checked,
            run_time: $("schedule-time").value,
            timezone: $("schedule-timezone").value,
            catch_up: $("schedule-catch-up").checked,
        };
    }

    markScheduleDirty(dirty = null) {
        const s = this.schedule;
        if (dirty === null) {
            const f = this.formSchedule();
            dirty = Boolean(s) && (f.enabled !== s.enabled || f.run_time !== s.run_time || f.timezone !== s.timezone || f.catch_up !== s.catch_up);
        }
        this.scheduleDirty = dirty;
        $("schedule-dirty").textContent = dirty ? "Unsaved changes" : "";
        $("btn-save-schedule").disabled = !dirty;
        $("btn-reset-schedule").disabled = !dirty;
        const enabled = $("schedule-enabled").checked;
        for (const id of ["schedule-time", "schedule-timezone", "schedule-catch-up"]) $(id).disabled = !enabled;
    }

    async saveSchedule() {
        const value = this.formSchedule();
        if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(value.run_time)) {
            notify("Enter a time as HH:MM", "warning");
            return;
        }
        try {
            this.schedule = (await api("/api/schedule", { method: "PUT", body: value })).data;
            this.scheduleDirty = false;
            this.fillScheduleForm();
            this.renderScheduleTimes();
            notify(value.enabled
                ? `Saved: the update runs every day at ${value.run_time} (${zoneLabel(value.timezone)})`
                : "Saved: the daily update is turned off", "success");
            this.pollStatus();
        } catch (err) {
            notify(`Could not save the schedule: ${err.message}`, "error");
        }
    }

    renderScheduleTimes() {
        const s = this.schedule;
        if (!s) return;
        if (s.enabled && s.next_run) {
            $("schedule-next").textContent = `${zoneTime(s.next_run, s.timezone)} ${s.timezone === "local" ? "server time" : zoneLabel(s.timezone)}`;
            $("schedule-next-local").textContent = `${s.next_run_is_catch_up ? "Missed update, " : ""}${relative(s.next_run)} · ${zoneTime(s.next_run)} your time`;
        } else {
            $("schedule-next").textContent = "Disabled";
            $("schedule-next-local").textContent = "";
        }
        const last = s.last_run || {};
        $("schedule-last").textContent = s.running_since ? `Running since ${dateTime(s.running_since)}` : (last.started_at ? dateTime(last.started_at) : "Not run yet");
        $("schedule-last-result").textContent = last.result && last.started_at
            ? `${last.result}${last.finished_at ? ` · finished ${dateTime(last.finished_at)}` : ""}` : "";
        $("schedule-server-time").textContent = dateTime(s.server_time);
    }

    renderApiTable() {
        $("api-table").querySelector("tbody").replaceChildren(...API_ENDPOINTS.map(([method, path, text]) => el("tr", {},
            el("td", {}, el("span", { class: `badge ${method}` }, method)),
            el("td", {}, method === "GET" && !path.includes("{")
                ? el("a", { href: path.split("|")[0], target: "_blank", rel: "noopener" }, el("code", {}, path)) : el("code", {}, path)),
            el("td", { class: "muted" }, text))));
    }
}

new YahooDbApp();
