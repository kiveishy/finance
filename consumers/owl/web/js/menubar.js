// VS Code-style menu bar (File / Edit / Actions ...) in the title bar.
// menus: [{label, items: () => [{label, keybinding?, icon?, run, enabled?} | "-"]}]; items are built on open, so
// their enabled state always reflects the current workbench state. Alt+<first letter> opens a menu.
import { el, icon } from "./ui.js";

export class MenuBar {
    constructor(root, menus) {
        this.root = root;
        this.menus = menus;
        this.openIndex = -1;
        this.popup = el("div", { class: "dropdown-menu menubar-menu", role: "menu", hidden: true });
        document.body.append(this.popup);
        this.buttons = menus.map((menu, index) => el("button", {
            class: "menubar-item",
            "aria-haspopup": "true",
            onmousedown: (e) => {
                e.preventDefault();
                e.stopPropagation();
                if (this.openIndex === index) this.close();
                else this.open(index);
            },
            onmouseenter: () => { if (this.openIndex >= 0 && this.openIndex !== index) this.open(index); },
        }, menu.label));
        root.replaceChildren(...this.buttons);
        document.addEventListener("mousedown", (e) => {
            if (this.openIndex >= 0 && !this.popup.contains(e.target)) this.close();
        });
        document.addEventListener("keydown", (e) => this.onKey(e), true);
        window.addEventListener("resize", () => this.close());
        window.addEventListener("blur", () => this.close());
    }

    open(index) {
        this.openIndex = index;
        this.buttons.forEach((b, i) => b.classList.toggle("open", i === index));
        const rows = this.menus[index].items().map((item) => {
            if (item === "-") return el("div", { class: "menu-separator" });
            const enabled = item.enabled === undefined || item.enabled;
            return el("div", {
                class: `menu-item${enabled ? "" : " disabled"}`,
                role: "menuitem",
                "aria-disabled": enabled ? null : "true",
                onmousedown: (e) => e.stopPropagation(),
                onclick: (e) => {
                    e.stopPropagation();
                    if (!enabled) return;
                    this.close();
                    item.run();
                },
            },
                el("span", { class: "check" }, item.icon ? icon(item.icon) : null),
                el("span", { class: "menu-label" }, item.label),
                item.keybinding ? el("span", { class: "menu-keybinding" }, item.keybinding) : null);
        });
        this.popup.replaceChildren(...rows);
        const rect = this.buttons[index].getBoundingClientRect();
        this.popup.hidden = false;
        const width = this.popup.offsetWidth;
        this.popup.style.left = `${Math.max(4, Math.min(rect.left, window.innerWidth - width - 4))}px`;
        this.popup.style.top = `${rect.bottom}px`;
    }

    close() {
        if (this.openIndex < 0) return;
        this.openIndex = -1;
        this.popup.hidden = true;
        this.buttons.forEach((b) => b.classList.remove("open"));
    }

    onKey(event) {
        if (this.openIndex >= 0 && event.key === "Escape") {
            event.preventDefault();
            event.stopPropagation();
            this.close();
            return;
        }
        if (this.openIndex >= 0 && (event.key === "ArrowLeft" || event.key === "ArrowRight")) {
            event.preventDefault();
            const step = event.key === "ArrowRight" ? 1 : -1;
            this.open((this.openIndex + step + this.menus.length) % this.menus.length);
            return;
        }
        if (event.altKey && !event.ctrlKey && !event.metaKey && /^[a-z]$/i.test(event.key)) {
            const index = this.menus.findIndex((m) => m.label[0].toLowerCase() === event.key.toLowerCase());
            if (index >= 0) {
                event.preventDefault();
                this.open(index);
            }
        }
    }
}
