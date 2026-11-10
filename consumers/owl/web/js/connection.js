// Watches the owl server and its data provider (the aggregator) and reconnects automatically.
//
// States: checking -> online | degraded | provider-down | offline | unauthorized.
// While healthy the status is polled every 30 s; otherwise retries back off 3 s, 5 s, 10 s, 15 s.
// Failing API calls and the browser regaining network/focus trigger an immediate check.

export const State = Object.freeze({
    CHECKING: "checking",
    ONLINE: "online",
    DEGRADED: "degraded",
    PROVIDER_DOWN: "provider-down",
    OFFLINE: "offline",
    UNAUTHORIZED: "unauthorized",
});

const HEALTHY_INTERVAL_MS = 30_000;
const RETRY_DELAYS_MS = [3_000, 5_000, 10_000, 15_000];
const UNAUTHORIZED_INTERVAL_MS = 60_000;

export class ConnectionMonitor extends EventTarget {
    constructor(api) {
        super();
        this.api = api;
        this.state = State.CHECKING;
        this.status = null; // last /api/status payload
        this.error = null; // last error when the server itself was not usable
        this.failures = 0;
        this.nextCheckAt = null;
        this.timer = null;
        this.inFlight = null;
        window.addEventListener("online", () => this.check());
        document.addEventListener("visibilitychange", () => {
            if (!document.hidden && !this.healthy) this.check();
        });
    }

    /** Prices can be loaded (all or some providers answer). */
    get healthy() {
        return this.state === State.ONLINE || this.state === State.DEGRADED;
    }

    /** The owl server answers (watchlist works even when the data provider is down). */
    get serverReachable() {
        return this.healthy || this.state === State.PROVIDER_DOWN;
    }

    get retryInSeconds() {
        return this.nextCheckAt ? Math.max(0, Math.ceil((this.nextCheckAt - Date.now()) / 1000)) : null;
    }

    get unavailableProviders() {
        return (this.status?.aggregator?.providers ?? []).filter((p) => p.state === "unavailable");
    }

    /** Run a check now (coalesces with one already running). `force` bypasses the server cache. */
    check(force = false) {
        if (this.inFlight) return this.inFlight;
        clearTimeout(this.timer);
        this.nextCheckAt = null;
        this.inFlight = this.runCheck(force).finally(() => {
            this.inFlight = null;
            this.schedule();
        });
        return this.inFlight;
    }

    /** Called by the API client when a request failed in a way that suggests an outage. */
    reportFailure(error) {
        if ([0, 502, 503, 504].includes(error.status)) this.check(true);
    }

    async runCheck(force) {
        let state;
        let status = null;
        let error = null;
        try {
            status = await this.api.status(force);
            const aggregatorState = status.aggregator?.state;
            state = aggregatorState === "available" ? State.ONLINE
                : aggregatorState === "degraded" ? State.DEGRADED
                    : State.PROVIDER_DOWN;
        } catch (err) {
            error = err;
            state = err.status === 401 ? State.UNAUTHORIZED : State.OFFLINE;
        }
        const previous = this.state;
        Object.assign(this, { state, status, error });
        this.failures = this.healthy ? 0 : this.failures + 1;
        this.dispatchEvent(new CustomEvent("change", { detail: { previous, state } }));
    }

    schedule() {
        let delay = HEALTHY_INTERVAL_MS;
        if (this.state === State.UNAUTHORIZED) delay = UNAUTHORIZED_INTERVAL_MS;
        else if (!this.healthy) delay = RETRY_DELAYS_MS[Math.min(this.failures, RETRY_DELAYS_MS.length) - 1] ?? RETRY_DELAYS_MS[0];
        this.nextCheckAt = Date.now() + delay;
        this.timer = setTimeout(() => this.check(), delay);
    }
}
