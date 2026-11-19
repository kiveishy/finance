// Import of a broker's transaction history (CSV) as BUY / SELL / DIVIDEND labels, with a preview dialog.
// Works with exports that have Date, Action, Symbol, Quantity, Price and Amount columns (e.g. Charles Schwab:
// "Buy", "Sell", "Reinvest Shares", "Cash Dividend", "Qualified Dividend", "Reinvest Dividend", "NRA Tax Adj"),
// and recognises the wording of other brokers ("YOU BOUGHT", "DIVIDEND RECEIVED", ...).
import { el, icon } from "./ui.js";

export const MAX_IMPORT_BYTES = 5 * 1024 * 1024;
const SYMBOL_RE = /^[A-Z0-9^][A-Z0-9.\-^=]{0,19}$/;
const CUSIP_RE = /^(?=(?:.*\d){3})[0-9A-Z]{9}$/; // positions without a ticker (delisted, reorganised) show a CUSIP
const COLUMNS = {
    date: /^(run |trade |transaction |settlement )?date$/i,
    action: /^(action|transaction|transaction type|type|activity)$/i,
    symbol: /^(symbol|ticker)$/i,
    description: /^(description|security description|name)$/i,
    quantity: /^(quantity|qty|shares)$/i,
    price: /^price/i,
    amount: /^(amount|net amount)/i,
};
const SIDE_LABELS = { buy: "BUY", sell: "SELL", dividend: "DIVIDEND" };

/** Rows of a CSV text (RFC 4180: quoted fields, doubled quotes, CRLF / LF). */
export function parseCsv(text) {
    const rows = [];
    let row = [];
    let field = "";
    let quoted = false;
    for (let i = 0; i < text.length; i++) {
        const c = text[i];
        if (quoted) {
            if (c === '"' && text[i + 1] === '"') {
                field += '"';
                i++;
            } else if (c === '"') {
                quoted = false;
            } else {
                field += c;
            }
        } else if (c === '"') {
            quoted = true;
        } else if (c === ",") {
            row.push(field);
            field = "";
        } else if (c === "\n" || c === "\r") {
            if (c === "\r" && text[i + 1] === "\n") i++;
            row.push(field);
            rows.push(row);
            row = [];
            field = "";
        } else {
            field += c;
        }
    }
    if (field || row.length) {
        row.push(field);
        rows.push(row);
    }
    return rows.filter((r) => r.some((cell) => cell.trim()));
}

/** "-$1,234.56", "(12.50)", "1 234" -> number; empty -> null. */
export function parseNumber(text) {
    let value = String(text ?? "").trim();
    if (!value) return null;
    const negative = /^\(.*\)$/.test(value) || value.includes("-");
    value = value.replace(/[()$€£\s,+-]/g, "");
    if (!/^\d*\.?\d+$/.test(value)) return null;
    return (negative ? -1 : 1) * Number(value);
}

/** "11/18/2026", "11/18/2026 as of 11/15/2026" or "2026-11-18" -> "2026-11-18"; null when unreadable. */
export function parseDate(text) {
    const value = String(text ?? "").trim();
    let match = /^(\d{1,2})\/(\d{1,2})\/(\d{4})/.exec(value);
    let iso = null;
    if (match) iso = `${match[3]}-${match[1].padStart(2, "0")}-${match[2].padStart(2, "0")}`;
    match = /^(\d{4})-(\d{2})-(\d{2})/.exec(value);
    if (match) iso = match[0];
    if (!iso) return null;
    const date = new Date(`${iso}T00:00:00Z`);
    return Number.isNaN(date.getTime()) || date.toISOString().slice(0, 10) !== iso ? null : iso;
}

/** buy | sell | dividend | tax for a broker's action text, or null for actions that are not imported. */
export function classifyAction(action) {
    const a = action.toLowerCase();
    if (/\btax\b|withholding/.test(a)) return "tax";
    if (/reinvest(ed)? shares|^reinvestment\b/.test(a)) return "buy"; // shares bought with a dividend
    if (/div(idend)?\b|dividend/.test(a)) return "dividend";
    if (/\bbuy\b|\bbought\b/.test(a)) return "buy";
    if (/\bsell\b|\bsold\b/.test(a)) return "sell";
    return null;
}

/**
 * Parses a broker CSV into actions.
 * Returns {actions: [{id, symbol, description, side, date, amount, price, tax, action}],
 *          skipped: [{label, count}]} (rows that are not imported, by reason).
 * Throws an Error when the file has no Date / Action / Symbol header.
 */
export function parseBrokerHistory(text) {
    const rows = parseCsv(text.replace(/^﻿/, ""));
    const headerIndex = rows.findIndex((row) => ["date", "action", "symbol"]
        .every((key) => row.some((cell) => COLUMNS[key].test(cell.trim()))));
    if (headerIndex < 0) throw new Error("No Date, Action and Symbol columns found - is this a broker transaction history CSV?");
    const header = rows[headerIndex].map((cell) => cell.trim());
    const column = Object.fromEntries(Object.entries(COLUMNS).map(([key, re]) => [key, header.findIndex((h) => re.test(h))]));
    const cell = (row, key) => (column[key] >= 0 ? (row[column[key]] ?? "").trim() : "");

    const actions = [];
    const taxes = [];
    const skipped = new Map();
    const skip = (label) => skipped.set(label, (skipped.get(label) ?? 0) + 1);
    rows.slice(headerIndex + 1).forEach((row, index) => {
        const date = parseDate(cell(row, "date"));
        const action = cell(row, "action");
        if (!date) {
            if (action || cell(row, "symbol")) skip("Rows without a date (totals)");
            return;
        }
        const kind = classifyAction(action);
        if (!kind) {
            skip(action || "Rows without an action");
            return;
        }
        const symbol = cell(row, "symbol").toUpperCase();
        if (!SYMBOL_RE.test(symbol) || CUSIP_RE.test(symbol)) {
            skip(`${action} without a ticker symbol${symbol ? ` (${symbol})` : ""}`);
            return;
        }
        const quantity = Math.abs(parseNumber(cell(row, "quantity")) ?? 0);
        const amount = Math.abs(parseNumber(cell(row, "amount")) ?? 0);
        let price = Math.abs(parseNumber(cell(row, "price")) ?? 0);
        const base = { id: `r${headerIndex + index + 1}`, symbol, description: cell(row, "description"), date, action };
        if (kind === "tax") {
            if (amount > 0) taxes.push({ ...base, amount });
            return;
        }
        if (kind === "dividend") {
            if (amount > 0) actions.push({ ...base, side: "dividend", amount, price: null, tax: 0 });
            else skip(`${action} without an amount`);
            return;
        }
        if (!price && quantity && amount) price = amount / quantity;
        if (quantity > 0 && price > 0) actions.push({ ...base, side: kind, amount: quantity, price, tax: 0 });
        else skip(`${action} without a quantity or price`);
    });
    // Withheld tax (e.g. "NRA Tax Adj") belongs to the dividend of the same symbol and day.
    for (const tax of taxes) {
        const dividend = actions.find((a) => a.side === "dividend" && a.symbol === tax.symbol && a.date === tax.date);
        if (dividend) dividend.tax = Math.round((dividend.tax + tax.amount) * 100) / 100;
        else skip(`${tax.action} without a dividend`);
    }
    actions.sort((x, y) => x.symbol.localeCompare(y.symbol) || x.date.localeCompare(y.date));
    return { actions, skipped: [...skipped].map(([label, count]) => ({ label, count })) };
}

const money = (value) => value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const units = (value) => value.toLocaleString("en-US", { maximumFractionDigits: 8 });
const price = (value) => value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: value < 1 ? 8 : 4 });

/**
 * Shows what an import will add, grouped by symbol, with a checkbox per action and per symbol.
 * options: fileName, actions (with `status`: null | "duplicate" | "limit"), skipped, watchlist (Set of symbols),
 *          groups [{id, name}].
 * Resolves with {actions: selected actions, groupId: id | "__new" | null} or null when cancelled.
 */
export function showImportPreview({ fileName, actions, skipped, watchlist, groups }) {
    return new Promise((resolve) => {
        const selected = new Set(actions.filter((a) => !a.status).map((a) => a.id));
        const symbols = [...new Set(actions.map((a) => a.symbol))];
        const missing = symbols.filter((s) => !watchlist.has(s));
        const rowBoxes = new Map(); // action id -> checkbox
        const symbolBoxes = new Map(); // symbol -> checkbox

        const sections = symbols.map((symbol) => {
            const items = actions.filter((a) => a.symbol === symbol);
            const counts = ["buy", "sell", "dividend"].map((side) => [side, items.filter((a) => a.side === side).length])
                .filter(([, n]) => n).map(([side, n]) => `${n} ${side}${n === 1 ? "" : "s"}`);
            const symbolBox = el("input", {
                type: "checkbox",
                title: `Select all actions of ${symbol}`,
                onchange: (e) => {
                    for (const a of items) {
                        if (e.target.checked) selected.add(a.id);
                        else selected.delete(a.id);
                    }
                    update();
                },
            });
            symbolBoxes.set(symbol, symbolBox);
            const rows = items.map((a) => {
                const box = el("input", {
                    type: "checkbox",
                    onchange: (e) => {
                        if (e.target.checked) selected.add(a.id);
                        else selected.delete(a.id);
                        update();
                    },
                });
                rowBoxes.set(a.id, box);
                const dividend = a.side === "dividend";
                const note = a.status === "duplicate" ? "already on the chart"
                    : a.status === "limit" ? "over the 100 drawings limit"
                        : dividend && a.tax ? `tax ${money(a.tax)}, net ${money(a.amount - a.tax)}`
                            : a.action !== { buy: "Buy", sell: "Sell" }[a.side] ? a.action : "";
                return el("tr", { class: a.status ? "muted" : "" },
                    el("td", {}, box),
                    el("td", {}, a.date),
                    el("td", {}, el("span", { class: `trade-badge ${a.side}` }, SIDE_LABELS[a.side])),
                    el("td", { class: "num" }, dividend ? "" : units(a.amount)),
                    el("td", { class: "num" }, dividend ? "" : price(a.price)),
                    el("td", { class: "num" }, money(dividend ? a.amount : a.amount * a.price)),
                    el("td", { class: "note" }, note));
            });
            return el("tbody", {},
                el("tr", { class: "import-symbol" },
                    el("td", {}, symbolBox),
                    el("td", { colspan: "6" },
                        el("b", {}, symbol),
                        el("span", { class: "import-name" }, items[0].description),
                        el("span", { class: "import-counts" }, counts.join(" · ")),
                        watchlist.has(symbol) ? null : el("span", { class: "import-tag" }, "not in watchlist"))),
                rows);
        });

        const groupSelect = el("select", {},
            groups.map((g) => el("option", { value: String(g.id) }, g.name)),
            el("option", { value: "__new" }, "New group \"Portfolio\""));
        const addMissing = el("input", { type: "checkbox", checked: missing.length > 0 });
        const footerOption = missing.length
            ? el("label", { class: "import-option" }, addMissing,
                `Add ${missing.length} symbol${missing.length === 1 ? "" : "s"} not in the watchlist (${missing.join(", ")}) to`, groupSelect)
            : null;
        const summary = el("div", { class: "import-summary" });
        const submit = el("button", { type: "submit", class: "banner-button" }, "Import");
        const skippedTotal = skipped.reduce((sum, s) => sum + s.count, 0);
        const skippedInfo = skippedTotal
            ? el("details", { class: "import-skipped" },
                el("summary", {}, `${skippedTotal} row${skippedTotal === 1 ? "" : "s"} not imported (interest, splits, tax without dividend, …)`),
                el("ul", {}, skipped.map((s) => el("li", {}, `${s.label} × ${s.count}`))))
            : null;

        function update() {
            for (const [id, box] of rowBoxes) box.checked = selected.has(id);
            for (const [symbol, box] of symbolBoxes) {
                const ids = actions.filter((a) => a.symbol === symbol).map((a) => a.id);
                const n = ids.filter((id) => selected.has(id)).length;
                box.checked = n === ids.length;
                box.indeterminate = n > 0 && n < ids.length;
            }
            const chosen = actions.filter((a) => selected.has(a.id));
            const bySide = (side) => chosen.filter((a) => a.side === side).length;
            const onSymbols = new Set(chosen.map((a) => a.symbol)).size;
            summary.textContent = `${chosen.length} of ${actions.length} actions selected on ${onSymbols} symbol${onSymbols === 1 ? "" : "s"}: `
                + `${bySide("buy")} buy, ${bySide("sell")} sell, ${bySide("dividend")} dividend`;
            submit.textContent = `Add ${chosen.length} to the charts`;
            submit.disabled = chosen.length === 0;
            groupSelect.disabled = !addMissing.checked;
        }

        const close = (result) => {
            overlay.remove();
            document.removeEventListener("keydown", onKey, true);
            resolve(result);
        };
        const onKey = (event) => {
            if (event.key !== "Escape") return;
            event.preventDefault();
            event.stopPropagation();
            close(null);
        };
        const form = el("form", {
            class: "form-dialog import-dialog",
            onsubmit: (event) => {
                event.preventDefault();
                const chosen = actions.filter((a) => selected.has(a.id));
                if (!chosen.length) return;
                close({ actions: chosen, groupId: missing.length && addMissing.checked ? groupSelect.value : null });
            },
            onchange: (e) => { if (e.target === addMissing) update(); },
        },
            el("div", { class: "form-title" }, icon("cloud-download"), ` Import broker history - ${fileName}`),
            el("div", { class: "form-preview" },
                "These actions will be added to the charts as BUY / SELL / DIVIDEND labels. "
                + "Untick the ones you don't want. Actions already on a chart are not selected."),
            el("div", { class: "import-table-wrap" },
                el("table", { class: "import-table" },
                    el("thead", {}, el("tr", {},
                        el("th", {}), el("th", {}, "Date"), el("th", {}, "Action"), el("th", { class: "num" }, "Quantity"),
                        el("th", { class: "num" }, "Price"), el("th", { class: "num" }, "Total"), el("th", {}, "Note"))),
                    sections)),
            skippedInfo,
            footerOption,
            summary,
            el("div", { class: "form-buttons" },
                el("button", { type: "button", class: "banner-button secondary", onclick: () => close(null) }, "Cancel"),
                submit));
        const overlay = el("div", { class: "form-overlay", onmousedown: (e) => { if (e.target === overlay) close(null); } }, form);
        document.addEventListener("keydown", onKey, true);
        document.body.append(overlay);
        update();
        submit.focus();
    });
}

/** Lets the user pick a file; resolves with the File or null when cancelled. */
export function pickFile(accept) {
    return new Promise((resolve) => {
        const input = el("input", { type: "file", accept, style: "display:none" });
        input.addEventListener("change", () => {
            resolve(input.files?.[0] ?? null);
            input.remove();
        });
        input.addEventListener("cancel", () => {
            resolve(null);
            input.remove();
        });
        document.body.append(input);
        input.click();
    });
}
