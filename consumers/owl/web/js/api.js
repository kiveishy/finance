// REST client for the owl server. Server URL/API key are configurable (needed by the Android app).
const SETTINGS_KEY = "owl.connection";

export class ApiError extends Error {
    constructor(message, status) {
        super(message);
        this.status = status;
    }
}

export function loadConnection() {
    try {
        return { url: "", apiKey: "", ...JSON.parse(localStorage.getItem(SETTINGS_KEY) || "{}") };
    } catch {
        return { url: "", apiKey: "" };
    }
}

export function saveConnection(connection) {
    localStorage.setItem(SETTINGS_KEY, JSON.stringify(connection));
}

export class ApiClient {
    constructor(connection = loadConnection()) {
        this.configure(connection);
        this.onFailure = null; // (ApiError) => void, used to trigger reconnect checks
    }

    configure({ url = "", apiKey = "" }) {
        this.baseUrl = url.replace(/\/+$/, "");
        this.apiKey = apiKey;
    }

    async request(method, path, body) {
        try {
            return await this.send(method, path, body);
        } catch (err) {
            if (err instanceof ApiError && this.onFailure && !path.startsWith("/api/status")) this.onFailure(err);
            throw err;
        }
    }

    async send(method, path, body) {
        const headers = { Accept: "application/json" };
        if (this.apiKey) headers["X-API-Key"] = this.apiKey;
        if (body !== undefined) headers["Content-Type"] = "application/json";
        let response;
        try {
            response = await fetch(this.baseUrl + path, {
                method,
                headers,
                body: body === undefined ? undefined : JSON.stringify(body),
            });
        } catch (err) {
            throw new ApiError(`Cannot reach owl server${this.baseUrl ? " " + this.baseUrl : ""}: ${err.message}`, 0);
        }
        let payload = null;
        try {
            payload = await response.json();
        } catch {
            /* non-JSON error page */
        }
        if (!response.ok || payload?.status === "error") {
            throw new ApiError(payload?.error || `HTTP ${response.status}`, response.status);
        }
        return payload?.data;
    }

    status(refresh = false) { return this.request("GET", refresh ? "/api/status?refresh=1" : "/api/status"); }
    groups() { return this.request("GET", "/api/groups"); }
    createGroup(name) { return this.request("POST", "/api/groups", { name }); }
    renameGroup(id, name) { return this.request("PATCH", `/api/groups/${id}`, { name }); }
    deleteGroup(id) { return this.request("DELETE", `/api/groups/${id}`); }
    addSymbol(groupId, symbol) { return this.request("POST", `/api/groups/${groupId}/items`, { symbol }); }
    removeSymbol(groupId, symbol) {
        return this.request("DELETE", `/api/groups/${groupId}/items/${encodeURIComponent(symbol)}`);
    }
    ticker(symbol) { return this.request("GET", `/api/ticker/${encodeURIComponent(symbol)}`); }
    search(query, limit = 12) {
        return this.request("GET", `/api/search?${new URLSearchParams({ q: query, limit: String(limit) })}`);
    }
    chart(symbol, { range, interval, indicators }) {
        const query = new URLSearchParams({ range });
        if (interval) query.set("interval", interval);
        if (indicators.length) query.set("indicators", indicators.join(","));
        return this.request("GET", `/api/chart/${encodeURIComponent(symbol)}?${query}`);
    }
    getSetting(key) { return this.request("GET", `/api/settings/${key}`); }
    putSetting(key, value) { return this.request("PUT", `/api/settings/${key}`, value); }
}
