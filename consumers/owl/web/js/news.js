// News: headlines from the feeds the owl server aggregates (see owl/news), shown in a scrolling side panel.
//   NewsSources        the server's feed catalog + the user's on/off choices (setting "news.feeds")
//   NewsPanel          the side panel view (list, header, status line)
//   FeedSettingsView   the feed table on the Settings page: where each headline comes from and its health
//   NewsCenter         controller: periodic refresh, wiring sources, panel and settings together
import { el, icon } from "./ui.js";

export const FEEDS_SETTING = "news.feeds";
const MAX_ITEMS = 150;

/** "now", "5m", "3h", "2d" or a date. */
export function relativeTime(iso, now = Date.now()) {
    if (!iso) return "";
    const ms = now - Date.parse(iso);
    if (Number.isNaN(ms)) return "";
    const minutes = Math.floor(ms / 60_000);
    if (minutes < 1) return "now";
    if (minutes < 60) return `${minutes}m`;
    if (minutes < 24 * 60) return `${Math.floor(minutes / 60)}h`;
    if (minutes < 7 * 24 * 60) return `${Math.floor(minutes / 1440)}d`;
    return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

const absoluteTime = (iso) => (iso ? new Date(iso).toLocaleString() : "unknown time");

/** Validates the "news.feeds" setting value: {feedId: true | false} overrides of each feed's default. */
export function validateFeedChoices(value) {
    if (!value || typeof value !== "object" || Array.isArray(value)) return "Must be an object";
    const entries = Object.entries(value);
    if (entries.length > 100) return "Too many feeds";
    return entries.every(([k, v]) => k.length <= 64 && typeof v === "boolean") ? null : "Feed choices must be true or false";
}

export class NewsSources {
    constructor(api) {
        this.api = api;
        this.feeds = []; // [{id, name, url, homepage, language, region, description, enabled_by_default, status}]
        this.loaded = false;
    }

    async load() {
        this.feeds = await this.api.newsFeeds();
        this.loaded = true;
        return this.feeds;
    }

    byId(id) {
        return this.feeds.find((f) => f.id === id);
    }

    isEnabled(feed, choices) {
        return feed.id in choices ? choices[feed.id] : feed.enabled_by_default;
    }

    enabledIds(choices) {
        return this.feeds.filter((f) => this.isEnabled(f, choices)).map((f) => f.id);
    }

    /** Latest statuses from a /api/news response. */
    updateStatuses(statuses) {
        for (const status of statuses) {
            const feed = this.byId(status.feed);
            if (feed) feed.status = status;
        }
    }
}

export class NewsPanel {
    /** callbacks: {onRefresh(), onSources(), onClose()} */
    constructor(root, callbacks) {
        this.root = root;
        this.items = [];
        this.seen = new Set(); // links already shown, to highlight new headlines
        this.count = el("span", { class: "news-count" });
        this.list = el("div", { class: "news-list", role: "feed", "aria-label": "Financial news" });
        this.status = el("div", { class: "news-status" });
        this.refreshButton = el("button", { class: "icon-button", title: "Refresh news", onclick: callbacks.onRefresh }, icon("refresh"));
        root.replaceChildren(
            el("div", { class: "sidebar-header news-header" },
                el("span", {}, "NEWS"), this.count,
                el("div", { class: "sidebar-actions" },
                    this.refreshButton,
                    el("button", { class: "icon-button", title: "News sources (Settings)", onclick: callbacks.onSources }, icon("rss")),
                    el("button", { class: "icon-button", title: "Hide the news panel (View menu shows it again)", onclick: callbacks.onClose }, icon("close")))),
            this.list,
            this.status);
        this.showMessage("Loading news…");
    }

    setLoading(loading) {
        this.refreshButton.replaceChildren(icon(loading ? "sync codicon-modifier-spin" : "refresh"));
    }

    showMessage(text) {
        this.list.replaceChildren(el("div", { class: "news-empty" }, text));
    }

    setItems(items, sources) {
        const first = this.seen.size === 0;
        this.items = items;
        this.count.textContent = items.length ? String(items.length) : "";
        if (!items.length) {
            this.showMessage("No headlines. Choose news sources in Settings → News.");
            return;
        }
        const scroll = this.list.scrollTop;
        this.list.replaceChildren(...items.map((item) => this.renderItem(item, sources, !first && !this.seen.has(item.link))));
        this.list.scrollTop = scroll;
        items.forEach((item) => this.seen.add(item.link));
    }

    renderItem(item, sources, fresh) {
        const feed = sources.byId(item.feed);
        return el("a", {
            class: `news-item${fresh ? " fresh" : ""}`,
            href: item.link,
            target: "_blank",
            rel: "noopener noreferrer",
            title: [item.title, item.summary, `${item.source} · ${absoluteTime(item.published)}`].filter(Boolean).join("\n\n"),
        },
        el("div", { class: "news-title", dir: "auto" }, item.title),
        el("div", { class: "news-meta" },
            el("span", { class: "news-source", "data-feed": item.feed }, item.source),
            feed?.language && feed.language !== "en" ? el("span", { class: "news-lang" }, feed.language.toUpperCase()) : null,
            el("span", { class: "news-time", "data-published": item.published ?? "" }, relativeTime(item.published))));
    }

    /** Re-renders the "5m ago" labels. */
    tick() {
        for (const node of this.list.querySelectorAll(".news-time")) node.textContent = relativeTime(node.dataset.published);
    }

    setStatus(statuses, sources, updatedAt) {
        const failed = statuses.filter((s) => !s.ok && s.error);
        const time = updatedAt.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
        this.status.className = `news-status${failed.length ? " warning" : ""}`;
        this.status.title = statuses.map((s) => `${sources.byId(s.feed)?.name ?? s.feed}: ${s.ok ? `${s.items} headlines` : s.error ?? "not loaded yet"}`).join("\n");
        this.status.replaceChildren(
            icon(failed.length ? "warning" : "rss"),
            ` ${statuses.length} source${statuses.length === 1 ? "" : "s"} · updated ${time}`,
            failed.length ? ` · ${failed.length} failing` : "");
    }

    setError(message) {
        this.status.className = "news-status warning";
        this.status.title = message;
        this.status.replaceChildren(icon("warning"), ` ${message}`);
        if (!this.items.length) this.showMessage("News is unavailable right now.");
    }
}

/** The feed table shown on the Settings page ("custom" setting control). */
export class FeedSettingsView {
    constructor(sources, { onToggle, onReload }) {
        this.sources = sources;
        this.onToggle = onToggle;
        this.onReload = onReload;
    }

    render(choices) {
        if (!this.sources.loaded) {
            return el("div", { class: "feed-settings" }, el("div", { class: "setting-detail" }, "Loading the list of feeds…"),
                el("button", { class: "banner-button secondary", onclick: () => this.onReload() }, "Retry"));
        }
        const rows = this.sources.feeds.map((feed) => {
            const enabled = this.sources.isEnabled(feed, choices);
            return el("tr", { class: enabled ? "" : "disabled" },
                el("td", {}, el("input", {
                    type: "checkbox", checked: enabled, "aria-label": `Use ${feed.name}`,
                    onchange: (e) => this.onToggle(feed, e.target.checked),
                })),
                el("td", {},
                    el("div", { class: "feed-name" }, feed.name,
                        el("span", { class: "feed-tags" }, `${feed.region} · ${feed.language.toUpperCase()}`)),
                    el("div", { class: "feed-description" }, feed.description),
                    el("div", { class: "feed-links" },
                        el("a", { href: feed.homepage, target: "_blank", rel: "noopener noreferrer", title: "Publisher website" }, icon("globe"), ` ${new URL(feed.homepage).host}`),
                        el("a", { href: feed.url, target: "_blank", rel: "noopener noreferrer", class: "feed-url", title: `Feed address the owl server reads:\n${feed.url}` }, icon("rss"), ` ${feed.url}`))),
                el("td", { class: "feed-health" }, this.health(feed.status)));
        });
        return el("div", { class: "feed-settings" },
            el("table", { class: "feed-table" },
                el("thead", {}, el("tr", {}, el("th", {}, "On"), el("th", {}, "Source (where the headlines come from)"), el("th", {}, "Last fetch"))),
                el("tbody", {}, rows)),
            el("div", { class: "setting-detail" },
                "The owl server downloads these public RSS feeds (no account needed) every few minutes and merges them, newest first; the same story from several sources is shown once. Feeds are unofficial and may change or stop without notice."));
    }

    health(status) {
        if (!status?.last_attempt) return el("span", { class: "feed-state idle" }, "not fetched yet");
        if (status.ok) {
            return el("span", { class: "feed-state ok", title: `Last success ${absoluteTime(status.last_success)}` },
                icon("pass"), ` ${status.items} · ${relativeTime(status.last_success)}`);
        }
        return el("span", { class: "feed-state error", title: `${status.error}\nLast attempt ${absoluteTime(status.last_attempt)}` },
            icon("error"), ` ${status.error}`);
    }
}

export class NewsCenter {
    /** options: {api, settings (SettingsStore), canLoad() -> bool, onSourcesChanged(), openSettings()} */
    constructor(root, options) {
        this.options = options;
        this.settings = options.settings;
        this.sources = new NewsSources(options.api);
        this.panel = new NewsPanel(root, {
            onRefresh: () => this.refresh(),
            onSources: () => options.openSettings(),
            onClose: () => options.onClose(),
        });
        this.feedSettings = new FeedSettingsView(this.sources, {
            onToggle: (feed, enabled) => this.setFeedEnabled(feed, enabled),
            onReload: () => this.loadSources().then(() => this.options.onSourcesChanged()),
        });
        this.loading = false;
        this.timer = null;
        setInterval(() => this.panel.tick(), 60_000);
    }

    get choices() {
        return this.settings.get(FEEDS_SETTING);
    }

    get visible() {
        return !this.panel.root.hidden && this.panel.root.offsetParent !== null;
    }

    async loadSources() {
        try {
            await this.sources.load();
        } catch (err) {
            this.panel.setError(`Loading news sources failed: ${err.message}`);
        }
    }

    /** Restarts the refresh timer (minutes from the "news.refreshMinutes" setting). */
    schedule(minutes) {
        clearInterval(this.timer);
        this.timer = setInterval(() => this.refresh(), minutes * 60_000);
    }

    async refresh() {
        if (this.loading || !this.options.canLoad()) return;
        this.loading = true;
        this.panel.setLoading(true);
        try {
            if (!this.sources.loaded) await this.sources.load();
            const ids = this.sources.enabledIds(this.choices);
            if (!ids.length) {
                this.panel.setItems([], this.sources);
                this.panel.setStatus([], this.sources, new Date());
                return;
            }
            const result = await this.options.api.news(ids, MAX_ITEMS);
            this.sources.updateStatuses(result.feeds);
            this.panel.setItems(result.items, this.sources);
            this.panel.setStatus(result.feeds, this.sources, new Date());
            this.options.onSourcesChanged();
        } catch (err) {
            this.panel.setError(`News unavailable: ${err.message}`);
        } finally {
            this.loading = false;
            this.panel.setLoading(false);
        }
    }

    setFeedEnabled(feed, enabled) {
        const choices = { ...this.choices };
        if (enabled === feed.enabled_by_default) delete choices[feed.id];
        else choices[feed.id] = enabled;
        return this.settings.set(FEEDS_SETTING, choices);
    }

    /** The Settings page control for "news.feeds". */
    renderFeedSettings() {
        return this.feedSettings.render(this.choices);
    }
}
