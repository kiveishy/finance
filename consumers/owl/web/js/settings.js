// Settings: a schema-driven store (cached locally, shared via the owl server) and a VS Code-like settings page.
// To add a setting for a new feature, add one entry to the schema passed to SettingsStore (see app.js).
import { el, icon } from "./ui.js";

const CACHE_KEY = "owl.settings";
const SERVER_KEY = "ui";

export class InvalidSettingError extends Error {}

/**
 * Schema entry:
 *   {key, section, title, description, type: "enum" | "boolean" | "number" | "action", default,
 *    options: [[value, label]] (enum), min, max, unit (number), button, run(), detail() (action)}
 */
export class SettingsStore extends EventTarget {
    constructor(api, schema) {
        super();
        this.api = api;
        this.schema = schema;
        this.byKey = new Map(schema.filter((s) => s.type !== "action").map((s) => [s.key, s]));
        this.values = this.sanitize(readCache());
    }

    get(key) {
        const definition = this.byKey.get(key);
        if (!definition) throw new Error(`Unknown setting ${key}`);
        return key in this.values ? this.values[key] : definition.default;
    }

    isModified(key) {
        return key in this.values;
    }

    /** Returns an error message, or null when `value` is valid for `key`. */
    validate(key, value) {
        const s = this.byKey.get(key);
        if (!s) return "Unknown setting";
        switch (s.type) {
            case "boolean":
                return typeof value === "boolean" ? null : "Must be true or false";
            case "enum":
                return s.options.some(([option]) => option === value) ? null : "Not one of the allowed values";
            case "number":
                if (typeof value !== "number" || !Number.isFinite(value)) return "Must be a number";
                if ((s.min !== undefined && value < s.min) || (s.max !== undefined && value > s.max)) {
                    return `Must be between ${s.min} and ${s.max}`;
                }
                return null;
            default:
                return "Unsupported setting type";
        }
    }

    sanitize(raw) {
        const values = {};
        if (raw && typeof raw === "object" && !Array.isArray(raw)) {
            for (const [key, value] of Object.entries(raw)) {
                if (this.byKey.has(key) && this.validate(key, value) === null) values[key] = value;
            }
        }
        return values;
    }

    /** Server values win over the local cache; keeps the cache when the server can't be reached. */
    async load() {
        let remote;
        try {
            remote = await this.api.getSetting(SERVER_KEY);
        } catch {
            return;
        }
        const before = { ...this.values };
        this.values = this.sanitize(remote);
        writeCache(this.values);
        const changed = [...this.byKey.keys()].filter((k) => before[k] !== this.values[k]);
        if (changed.length) this.dispatchEvent(new CustomEvent("change", { detail: { keys: changed } }));
    }

    /** Saves `value` (or removes the override when it equals the default). Throws on invalid values. */
    async set(key, value) {
        const error = this.validate(key, value);
        if (error) throw new InvalidSettingError(error);
        if (value === this.byKey.get(key).default) {
            if (!this.isModified(key)) return;
            delete this.values[key];
        } else {
            if (this.values[key] === value) return;
            this.values[key] = value;
        }
        await this.commit([key]);
    }

    async reset(key) {
        if (!this.isModified(key)) return;
        delete this.values[key];
        await this.commit([key]);
    }

    async commit(keys) {
        writeCache(this.values);
        this.dispatchEvent(new CustomEvent("change", { detail: { keys } }));
        await this.api.putSetting(SERVER_KEY, this.values);
    }
}

function readCache() {
    try {
        return JSON.parse(localStorage.getItem(CACHE_KEY) || "{}");
    } catch {
        return {};
    }
}

function writeCache(values) {
    localStorage.setItem(CACHE_KEY, JSON.stringify(values));
}

/** The Settings editor tab: search box, section list and one row per setting. */
export class SettingsPage {
    constructor(root, store, { onError }) {
        this.store = store;
        this.onError = onError;
        this.errors = new Map(); // key -> message of the last rejected input
        this.search = el("input", {
            type: "search",
            class: "settings-search",
            placeholder: "Search settings",
            spellcheck: "false",
            "aria-label": "Search settings",
            oninput: () => this.render(),
        });
        this.toc = el("nav", { class: "settings-toc" });
        this.list = el("div", { class: "settings-list" });
        root.replaceChildren(
            el("div", { class: "settings-header" }, this.search),
            el("div", { class: "settings-body" }, this.toc, this.list));
        store.addEventListener("change", () => this.render());
    }

    focusSearch() {
        this.search.focus();
    }

    matches(setting, words) {
        const text = `${setting.section} ${setting.title} ${setting.description} ${setting.key}`.toLowerCase();
        return words.every((word) => text.includes(word));
    }

    render() {
        const words = this.search.value.toLowerCase().split(/\s+/).filter(Boolean);
        const visible = this.store.schema.filter((s) => this.matches(s, words));
        const sections = [...new Set(visible.map((s) => s.section))];
        this.toc.replaceChildren(...sections.map((section) => el("button", {
            class: "settings-toc-item",
            onclick: () => this.list.querySelector(`[data-section="${CSS.escape(section)}"]`)?.scrollIntoView({ block: "start" }),
        }, section)));
        if (!visible.length) {
            this.list.replaceChildren(el("div", { class: "settings-empty" }, "No settings found"));
            return;
        }
        this.list.replaceChildren(...sections.flatMap((section) => [
            el("h2", { class: "settings-section", dataset: { section } }, section),
            ...visible.filter((s) => s.section === section).map((s) => this.item(s)),
        ]));
    }

    item(setting) {
        const modified = setting.type !== "action" && this.store.isModified(setting.key);
        const error = this.errors.get(setting.key);
        return el("div", { class: `setting-item${modified ? " modified" : ""}`, dataset: { key: setting.key } },
            el("div", { class: "setting-title" },
                el("span", { class: "setting-category" }, `${setting.section}: `),
                el("span", {}, setting.title),
                modified ? el("button", {
                    class: "icon-button setting-reset",
                    title: `Reset to default (${this.formatValue(setting, setting.default)})`,
                    onclick: () => this.save(() => this.store.reset(setting.key), setting.key),
                }, icon("discard")) : null),
            el("div", { class: "setting-description" }, setting.description),
            this.control(setting),
            error ? el("div", { class: "setting-error" }, error) : null);
    }

    formatValue(setting, value) {
        if (setting.type === "enum") return setting.options.find(([v]) => v === value)?.[1] ?? String(value);
        if (setting.type === "boolean") return value ? "on" : "off";
        return `${value}${setting.unit ? ` ${setting.unit}` : ""}`;
    }

    control(setting) {
        const key = setting.key;
        switch (setting.type) {
            case "enum": {
                const select = el("select", {
                    class: "setting-input",
                    "aria-label": setting.title,
                    onchange: (e) => this.save(() => this.store.set(key, e.target.value), key),
                }, setting.options.map(([value, label]) => el("option", { value }, label)));
                select.value = this.store.get(key);
                return select;
            }
            case "boolean":
                return el("label", { class: "setting-checkbox" },
                    el("input", {
                        type: "checkbox",
                        checked: this.store.get(key),
                        onchange: (e) => this.save(() => this.store.set(key, e.target.checked), key),
                    }),
                    setting.checkboxLabel ?? setting.title);
            case "number":
                return el("div", { class: "setting-number" },
                    el("input", {
                        type: "number",
                        class: "setting-input",
                        "aria-label": setting.title,
                        value: String(this.store.get(key)),
                        min: setting.min,
                        max: setting.max,
                        step: setting.step ?? 1,
                        onchange: (e) => this.save(() => this.store.set(key, e.target.valueAsNumber), key),
                    }),
                    setting.unit ? el("span", { class: "setting-unit" }, setting.unit) : null);
            case "action":
                return el("div", { class: "setting-action" },
                    setting.detail ? el("span", { class: "setting-detail" }, setting.detail()) : null,
                    el("button", { class: "banner-button", onclick: () => setting.run() }, setting.button));
            default:
                return null;
        }
    }

    async save(change, key) {
        try {
            this.errors.delete(key);
            await change();
        } catch (err) {
            if (err instanceof InvalidSettingError) this.errors.set(key, err.message);
            else this.onError(err); // applied locally, but the server didn't store it
        }
        this.render();
    }
}
