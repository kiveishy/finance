// Explorer tree: groups (folders) containing symbols, VS Code style.
import { el, icon } from "./ui.js";

const EXPANDED_KEY = "owl.explorer.collapsed";

export class Explorer {
    /**
     * handlers: onOpen(symbol, groupId), onAddSymbol(groupId), onRemoveSymbol(groupId, symbol),
     *           onRenameGroup(group), onDeleteGroup(group)
     */
    constructor(root, handlers) {
        this.root = root;
        this.handlers = handlers;
        this.groups = [];
        this.selected = null; // {groupId, symbol}
        this.collapsed = new Set(JSON.parse(localStorage.getItem(EXPANDED_KEY) || "[]"));
    }

    setGroups(groups) {
        this.groups = groups;
        this.render();
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
        const rows = [];
        for (const group of this.groups) {
            const open = !this.collapsed.has(group.id);
            rows.push(el("div", {
                class: "tree-row group-row",
                role: "treeitem",
                "aria-expanded": String(open),
                style: "padding-left: 4px",
                onclick: () => this.toggle(group.id),
            },
                el("span", { class: "twistie" }, icon(open ? "chevron-down" : "chevron-right")),
                el("span", { class: "label" }, group.name),
                el("span", { class: "badge" }, String(group.items.length)),
                el("span", { class: "row-actions" },
                    this.action("add", "Add symbol to group", () => this.handlers.onAddSymbol(group.id)),
                    this.action("edit", "Rename group", () => this.handlers.onRenameGroup(group)),
                    this.action("trash", "Delete group", () => this.handlers.onDeleteGroup(group)))));
            if (!open) continue;
            for (const item of group.items) {
                const selected = this.selected && this.selected.symbol === item.symbol &&
                    (this.selected.groupId === null || this.selected.groupId === group.id);
                rows.push(el("div", {
                    class: "tree-row symbol-row" + (selected ? " selected" : ""),
                    role: "treeitem",
                    style: "padding-left: 22px",
                    title: item.name ? `${item.symbol} — ${item.name}` : item.symbol,
                    onclick: () => this.handlers.onOpen(item.symbol, group.id),
                },
                    icon("symbol-event"),
                    el("span", { class: "label" }, item.symbol),
                    el("span", { class: "description" }, item.name || ""),
                    el("span", { class: "row-actions" },
                        this.action("close", "Remove from group", () => this.handlers.onRemoveSymbol(group.id, item.symbol)))));
            }
        }
        if (!this.groups.length) {
            rows.push(el("div", { class: "tree-row", style: "padding-left: 20px; color: var(--fg-muted)" }, "No groups yet"));
        }
        this.root.replaceChildren(...rows);
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
