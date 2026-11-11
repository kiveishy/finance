// Explorer tree: groups (folders) containing symbols, VS Code style.
import { el, formatNumber, icon } from "./ui.js";

const EXPANDED_KEY = "owl.explorer.collapsed";
const DRAG_TYPE = "application/x-owl-symbol";

export class Explorer {
    /**
     * handlers: onOpen(symbol, groupId), onAddSymbol(groupId), onRemoveSymbol(groupId, symbol),
     *           onRenameGroup(group), onDeleteGroup(group),
     *           onMoveSymbol(fromGroupId, symbol, toGroupId, {copy, before}), onPickMoveTarget(groupId, symbol)
     */
    constructor(root, handlers) {
        this.root = root;
        this.handlers = handlers;
        this.groups = [];
        this.quotes = {};
        this.selected = null; // {groupId, symbol}
        this.dragging = null; // {groupId, symbol} while a row is dragged
        this.renderPending = false;
        this.collapsed = new Set(JSON.parse(localStorage.getItem(EXPANDED_KEY) || "[]"));
    }

    setGroups(groups) {
        this.groups = groups;
        this.render();
    }

    /** quotes: {SYMBOL: {close, change, change_percent, date} | null}, merged into the known ones. */
    setQuotes(quotes) {
        Object.assign(this.quotes, quotes);
        this.render();
    }

    get symbols() {
        return [...new Set(this.groups.flatMap((g) => g.items.map((i) => i.symbol)))];
    }

    select(symbol, groupId = null) {
        this.selected = symbol ? { symbol, groupId } : null;
        this.render();
    }

    toggle(groupId) {
        if (this.collapsed.has(groupId)) this.collapsed.delete(groupId);
        else this.collapsed.add(groupId);
        localStorage.setItem(EXPANDED_KEY, JSON.stringify([...this.collapsed]));
        this.render();
    }

    render() {
        // Replacing the dragged row's element would cancel the drag: re-render when it ends.
        if (this.dragging) {
            this.renderPending = true;
            return;
        }
        const rows = [];
        for (const group of this.groups) {
            const open = !this.collapsed.has(group.id);
            rows.push(this.dropTarget(el("div", {
                class: "tree-row group-row",
                role: "treeitem",
                "aria-expanded": String(open),
                style: "padding-left: 4px",
                onclick: () => this.toggle(group.id),
            },
                el("span", { class: "twistie" }, icon(open ? "chevron-down" : "chevron-right")),
                el("span", { class: "label" }, group.name),
                this.groupChangeCell(group),
                el("span", { class: "badge" }, String(group.items.length)),
                el("span", { class: "row-actions" },
                    this.action("add", "Add symbol to group", () => this.handlers.onAddSymbol(group.id)),
                    this.action("edit", "Rename group", () => this.handlers.onRenameGroup(group)),
                    this.action("trash", "Delete group", () => this.handlers.onDeleteGroup(group)))), group, null));
            if (!open) continue;
            for (const item of group.items) {
                const selected = this.selected && this.selected.symbol === item.symbol &&
                    (this.selected.groupId === null || this.selected.groupId === group.id);
                const row = el("div", {
                    class: "tree-row symbol-row" + (selected ? " selected" : ""),
                    role: "treeitem",
                    style: "padding-left: 22px",
                    title: (item.name ? `${item.symbol} — ${item.name}` : item.symbol) +
                        "\nDrag to another group to move it (Ctrl+drag copies)",
                    draggable: "true",
                    onclick: () => this.handlers.onOpen(item.symbol, group.id),
                    ondragstart: (event) => this.startDrag(event, group.id, item.symbol),
                    ondragend: () => this.endDrag(),
                },
                    icon("symbol-event"),
                    el("span", { class: "label" }, item.symbol),
                    el("span", { class: "description" }, item.name || ""),
                    this.quoteCell(item.symbol),
                    el("span", { class: "row-actions" },
                        this.action("move", "Move to group…", () => this.handlers.onPickMoveTarget(group.id, item.symbol)),
                        this.action("close", "Remove from group", () => this.handlers.onRemoveSymbol(group.id, item.symbol))));
                rows.push(this.dropTarget(row, group, item.symbol));
            }
        }
        if (!this.groups.length) {
            rows.push(el("div", { class: "tree-row", style: "padding-left: 20px; color: var(--fg-muted)" }, "No groups yet"));
        }
        this.root.replaceChildren(...rows);
    }

    /** Equal-weighted average day change of the group's symbols that have a quote. */
    groupChangeCell(group) {
        const changes = group.items
            .map((item) => ({ symbol: item.symbol, pct: this.quotes[item.symbol]?.change_percent }))
            .filter((c) => typeof c.pct === "number");
        if (!changes.length) return null;
        const average = changes.reduce((sum, c) => sum + c.pct, 0) / changes.length;
        const up = changes.filter((c) => c.pct > 0).length;
        const down = changes.filter((c) => c.pct < 0).length;
        const sorted = [...changes].sort((a, b) => b.pct - a.pct);
        const fmt = (pct) => `${pct > 0 ? "+" : ""}${pct.toFixed(2)}%`;
        const direction = average > 0 ? "up" : average < 0 ? "down" : "";
        return el("span", {
            class: "group-change",
            title: `Average day change of ${changes.length} of ${group.items.length} symbols: ${fmt(average)}\n` +
                `${up} up, ${down} down, ${changes.length - up - down} unchanged\n` +
                `Best: ${sorted[0].symbol} ${fmt(sorted[0].pct)} · Worst: ${sorted.at(-1).symbol} ${fmt(sorted.at(-1).pct)}`,
        },
            el("span", { class: "counts" }, el("span", { class: "up" }, `${up}▲`), el("span", { class: "down" }, `${down}▼`)),
            el("span", { class: `change ${direction}` }, fmt(average)));
    }

    quoteCell(symbol) {
        const quote = this.quotes[symbol];
        if (!quote || quote.close == null) return el("span", { class: "quote" }, quote === null ? "—" : "");
        const pct = quote.change_percent;
        const direction = pct > 0 ? "up" : pct < 0 ? "down" : "";
        return el("span", { class: "quote", title: `Last close ${quote.date}` },
            el("span", { class: "price" }, formatNumber(quote.close, quote.close < 1 ? 4 : 2)),
            pct == null ? null : el("span", { class: `change ${direction}` }, `${pct > 0 ? "+" : ""}${pct.toFixed(2)}%`));
    }

    // ---- drag and drop -------------------------------------------------------------------------

    startDrag(event, groupId, symbol) {
        this.dragging = { groupId, symbol };
        event.dataTransfer.effectAllowed = "copyMove";
        event.dataTransfer.setData(DRAG_TYPE, JSON.stringify(this.dragging));
        event.dataTransfer.setData("text/plain", symbol);
        event.currentTarget.classList.add("dragging");
    }

    endDrag() {
        this.dragging = null;
        this.root.querySelectorAll(".drop-into, .drop-before").forEach((n) => n.classList.remove("drop-into", "drop-before"));
        if (this.renderPending) {
            this.renderPending = false;
            this.render();
        } else {
            this.root.querySelectorAll(".dragging").forEach((n) => n.classList.remove("dragging"));
        }
    }

    /** Group rows accept a drop at the end of the group; symbol rows insert before themselves. */
    dropTarget(row, group, beforeSymbol) {
        const accepts = (event) => {
            const drag = this.dragging;
            if (!drag || !event.dataTransfer.types.includes(DRAG_TYPE)) return false;
            if (drag.groupId !== group.id) return true;
            return beforeSymbol !== null && beforeSymbol !== drag.symbol;  // reorder inside the group
        };
        const marker = beforeSymbol === null ? "drop-into" : "drop-before";
        row.addEventListener("dragover", (event) => {
            if (!accepts(event)) return;
            event.preventDefault();
            const copy = (event.ctrlKey || event.altKey) && this.dragging.groupId !== group.id;
            event.dataTransfer.dropEffect = copy ? "copy" : "move";
            row.classList.add(marker);
        });
        row.addEventListener("dragleave", () => row.classList.remove(marker));
        row.addEventListener("drop", (event) => {
            row.classList.remove(marker);
            if (!accepts(event)) return;
            event.preventDefault();
            const drag = this.dragging;
            const copy = (event.ctrlKey || event.altKey) && drag.groupId !== group.id;
            this.handlers.onMoveSymbol(drag.groupId, drag.symbol, group.id, { copy, before: beforeSymbol });
        });
        return row;
    }

    action(name, title, handler) {
        return el("button", {
            class: "icon-button",
            title,
            onclick: (event) => {
                event.stopPropagation();
                handler();
            },
        }, icon(name));
    }
}
