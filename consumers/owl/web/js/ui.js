// Small DOM helpers and VS Code-like widgets (quick input, notifications). Never uses innerHTML for data.

export function el(tag, props = {}, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(props)) {
        if (value === undefined || value === null || value === false) continue;
        if (key === "class") node.className = value;
        else if (key === "dataset") Object.assign(node.dataset, value);
        else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
        else node.setAttribute(key, value === true ? "" : value);
    }
    for (const child of children.flat()) {
        if (child === null || child === undefined || child === false) continue;
        node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return node;
}

export const icon = (name, extra = "") => el("i", { class: `codicon codicon-${name} ${extra}`.trim() });

export function notify(message, kind = "info", timeout = 6000, actions = []) {
    const box = document.getElementById("notifications");
    const item = el("div", { class: "notification" },
        icon(kind === "error" ? "error" : kind === "warning" ? "warning" : "info"),
        el("div", { class: "text" }, message),
        actions.map(({ label, run }) => el("button", { class: "banner-button", onclick: () => { item.remove(); run(); } }, label)),
        el("button", { class: "icon-button", title: "Close", onclick: () => item.remove() }, icon("close")));
    box.append(item);
    if (timeout) setTimeout(() => item.remove(), timeout);
}

/** Command-palette style input. Resolves with the value, or null when cancelled (Escape/click outside). */
class QuickInput {
    constructor() {
        this.root = document.getElementById("quickinput");
        this.title = document.getElementById("quickinput-title");
        this.field = document.getElementById("quickinput-field");
        this.message = document.getElementById("quickinput-message");
        this.list = document.getElementById("quickinput-list");
        this.pending = null;
        this.searchSeq = 0;
        this.field.addEventListener("keydown", (e) => this.onKey(e));
        this.field.addEventListener("input", () => {
            this.active = 0;
            this.render();
            this.scheduleSearch();
        });
        document.addEventListener("mousedown", (e) => {
            if (this.pending && !this.root.contains(e.target)) this.finish(null);
        });
    }

    /**
     * options: title, placeholder, value, hint, validate(value) -> error string | null,
     *          items: [{label, description, detail, value, icon}] shown and filtered locally,
     *          search: async (text) => items  for type-ahead results from the server,
     *          allowCustom: accept typed text when nothing is selected.
     */
    show(options) {
        if (this.pending) this.finish(null);
        this.options = { items: null, allowCustom: true, searchDelay: 200, ...options };
        this.title.textContent = options.title || "";
        this.field.value = options.value || "";
        this.field.placeholder = options.placeholder || "";
        this.active = 0;
        this.resetSearch();
        this.root.hidden = false;
        this.render();
        this.field.focus();
        this.field.select();
        if (this.field.value) this.scheduleSearch();
        return new Promise((resolve) => (this.pending = resolve));
    }

    get isList() {
        return Boolean(this.options.items || this.options.search);
    }

    resetSearch() {
        clearTimeout(this.searchTimer);
        this.searchSeq++;
        this.remote = [];
        this.searching = false;
        this.searchError = null;
        this.searchedFor = "";
        this.enterPending = false;
    }

    scheduleSearch() {
        if (!this.options.search) return;
        clearTimeout(this.searchTimer);
        const text = this.field.value.trim();
        const seq = ++this.searchSeq;
        if (!text) {
            this.remote = [];
            this.searching = false;
            this.searchedFor = "";
            this.render();
            return;
        }
        this.searching = true;
        this.render();
        this.searchTimer = setTimeout(async () => {
            let items = [];
            let error = null;
            try {
                items = await this.options.search(text);
            } catch (err) {
                error = err.message || String(err);
            }
            if (seq !== this.searchSeq || !this.pending) return; // a newer keystroke superseded this search
            Object.assign(this, { remote: items, searchError: error, searching: false, searchedFor: text });
            this.render();
            if (this.enterPending) {
                this.enterPending = false;
                this.accept();
            }
        }, this.options.searchDelay);
    }

    filtered() {
        const items = this.options.items || [];
        const query = this.field.value.trim().toLowerCase();
        const local = query
            ? items.filter((i) => `${i.label} ${i.description || ""} ${i.detail || ""}`.toLowerCase().includes(query))
            : items;
        const seen = new Set(local.map((i) => i.value));
        return [...local, ...this.remote.filter((i) => !seen.has(i.value))];
    }

    render() {
        const value = this.field.value.trim();
        const error = this.options.validate && value ? this.options.validate(value) : null;
        const items = this.isList ? this.filtered() : [];
        let message = this.options.hint || "";
        let tone = "";
        if (this.searching) {
            message = "Searching…";
        } else if (this.searchError) {
            message = `Search unavailable: ${this.searchError}`;
            tone = "error";
        } else if (this.options.search && value && this.searchedFor === value && !items.length) {
            message = this.options.allowCustom && !error ? `No matches - press Enter to use "${value.toUpperCase()}"` : "No matches";
        } else if (error && !items.length) {
            message = error;
            tone = "error";
        }
        this.message.textContent = message;
        this.message.className = "quickinput-message" + (tone ? ` ${tone}` : "");
        this.list.replaceChildren();
        this.active = Math.min(this.active, Math.max(items.length - 1, 0));
        items.forEach((item, index) => {
            this.list.append(el("div", {
                class: "quickinput-item" + (index === this.active ? " active" : ""),
                onmousedown: (e) => { e.preventDefault(); e.stopPropagation(); this.finish(item.value); },
            },
                item.icon ? icon(item.icon) : null,
                el("span", { class: "label" }, highlight(item.label, value)),
                item.description ? el("span", { class: "description" }, highlight(item.description, value)) : null,
                el("span", { class: "detail" }, item.detail || "")));
        });
        this.list.children[this.active]?.scrollIntoView({ block: "nearest" });
    }

    onKey(event) {
        const items = this.isList ? this.filtered() : [];
        if (event.key === "Escape") {
            this.finish(null);
        } else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
            event.preventDefault();
            if (items.length) this.active = (this.active + (event.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
            this.render();
        } else if (event.key === "Enter") {
            event.preventDefault();
            // Typed fast and hit Enter: wait for the pending search instead of guessing.
            if (this.searching && !items.length) {
                this.enterPending = true;
                return;
            }
            this.accept();
        }
    }

    accept() {
        const items = this.isList ? this.filtered() : [];
        if (items.length) {
            this.finish(items[this.active].value);
            return;
        }
        const value = this.field.value.trim();
        if (!value || !this.options.allowCustom) return;
        if (this.options.validate && this.options.validate(value)) {
            this.render();
            return;
        }
        this.finish(value);
    }

    finish(value) {
        this.root.hidden = true;
        this.resetSearch();
        const resolve = this.pending;
        this.pending = null;
        // Resolve after the current event so a follow-up prompt isn't affected by it (e.g. closed as an outside click).
        if (resolve) setTimeout(() => resolve(value));
    }
}

/** Text nodes with the (case-insensitive) occurrences of `query` wrapped for highlighting. */
function highlight(text, query) {
    const source = String(text ?? "");
    if (!query) return [source];
    const nodes = [];
    const lower = source.toLowerCase();
    const needle = query.toLowerCase();
    let index = 0;
    for (let hit = lower.indexOf(needle); hit !== -1 && needle; hit = lower.indexOf(needle, index)) {
        if (hit > index) nodes.push(source.slice(index, hit));
        nodes.push(el("span", { class: "highlight" }, source.slice(hit, hit + needle.length)));
        index = hit + needle.length;
    }
    if (index < source.length) nodes.push(source.slice(index));
    return nodes;
}

let quickInput = null;
export function prompt(options) {
    quickInput ??= new QuickInput();
    return quickInput.show(options);
}

export async function confirm(title, confirmLabel = "Yes") {
    const answer = await prompt({
        title,
        placeholder: "Select an option",
        items: [{ label: confirmLabel, value: "yes", icon: "check" }, { label: "Cancel", value: "no", icon: "close" }],
        allowCustom: false,
    });
    return answer === "yes";
}

/**
 * Modal form dialog. fields: [{name, label, type, value, placeholder, step, min, max}];
 * validate(values) -> error text | null; preview(values) -> text shown above the buttons.
 * Resolves with {name: string value} on submit, or null when cancelled (Esc, Cancel, click outside).
 */
export function showForm({ title, fields, submitLabel = "OK", submitClass = "", validate = () => null, preview = null }) {
    return new Promise((resolve) => {
        const inputs = Object.fromEntries(fields.map((f) => [f.name, el("input", {
            type: f.type || "text", name: f.name, value: f.value ?? "", placeholder: f.placeholder,
            step: f.step, min: f.min, max: f.max, autocomplete: "off", spellcheck: "false",
        })]));
        const values = () => Object.fromEntries(Object.entries(inputs).map(([name, input]) => [name, input.value.trim()]));
        const info = el("div", { class: "form-preview" });
        const message = el("div", { class: "form-message" });
        const update = () => {
            if (preview) info.textContent = preview(values()) ?? "";
            message.textContent = "";
        };
        const close = (result) => {
            overlay.remove();
            document.removeEventListener("keydown", onKey, true);
            resolve(result);
        };
        const submit = (event) => {
            event?.preventDefault();
            const error = validate(values());
            if (error) message.textContent = error;
            else close(values());
        };
        const onKey = (event) => {
            if (event.key !== "Escape") return;
            event.preventDefault();
            event.stopPropagation();
            close(null);
        };
        const form = el("form", { class: "form-dialog", novalidate: true, onsubmit: submit, oninput: update },
            el("div", { class: "form-title" }, title),
            fields.map((f) => el("label", { class: "form-row" }, el("span", {}, f.label), inputs[f.name])),
            info,
            message,
            el("div", { class: "form-buttons" },
                el("button", { type: "button", class: "banner-button secondary", onclick: () => close(null) }, "Cancel"),
                el("button", { type: "submit", class: `banner-button ${submitClass}`.trim() }, submitLabel)));
        const overlay = el("div", { class: "form-overlay", onmousedown: (e) => { if (e.target === overlay) close(null); } }, form);
        document.addEventListener("keydown", onKey, true);
        document.body.append(overlay);
        update();
        inputs[fields[0].name].focus();
    });
}

export function formatNumber(value, digits = 2) {
    if (value === null || value === undefined || Number.isNaN(value)) return "—";
    return Number(value).toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export function formatVolume(value) {
    if (value === null || value === undefined) return "—";
    for (const [threshold, suffix] of [[1e9, "B"], [1e6, "M"], [1e3, "K"]]) {
        if (Math.abs(value) >= threshold) return `${(value / threshold).toFixed(2)}${suffix}`;
    }
    return String(value);
}
