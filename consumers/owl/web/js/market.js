// US stock market (NYSE / Nasdaq) session for a moment in time, computed locally in New York time.
// Holidays and early closes follow the NYSE rules, so no server or network is needed.

const ZONE = "America/New_York";
const PRE_OPEN = 4 * 60;
const OPEN = 9 * 60 + 30;
const CLOSE = 16 * 60;
const EARLY_CLOSE = 13 * 60;
const POST_CLOSE = 20 * 60;
const WEEKDAYS = { Sun: 0, Mon: 1, Tue: 2, Wed: 3, Thu: 4, Fri: 5, Sat: 6 };

const partsFormat = new Intl.DateTimeFormat("en-US", {
    timeZone: ZONE, hourCycle: "h23", year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit", weekday: "short",
});

/** New York wall-clock parts of `date`. */
function nyParts(date) {
    const p = Object.fromEntries(partsFormat.formatToParts(date).map(({ type, value }) => [type, value]));
    return {
        year: Number(p.year), month: Number(p.month), day: Number(p.day),
        weekday: WEEKDAYS[p.weekday], minutes: Number(p.hour) * 60 + Number(p.minute), seconds: Number(p.second),
    };
}

const key = (y, m, d) => `${y}-${String(m).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
const weekdayOf = (y, m, d) => new Date(Date.UTC(y, m - 1, d)).getUTCDay();
const addDays = (y, m, d, n) => {
    const date = new Date(Date.UTC(y, m - 1, d + n));
    return [date.getUTCFullYear(), date.getUTCMonth() + 1, date.getUTCDate()];
};

function nthWeekday(y, m, weekday, n) {
    const first = weekdayOf(y, m, 1);
    return 1 + ((weekday - first + 7) % 7) + (n - 1) * 7;
}

function lastWeekday(y, m, weekday) {
    const lastDay = new Date(Date.UTC(y, m, 0)).getUTCDate();
    return lastDay - ((weekdayOf(y, m, lastDay) - weekday + 7) % 7);
}

function easter(y) { // anonymous Gregorian algorithm
    const a = y % 19, b = Math.floor(y / 100), c = y % 100, d = Math.floor(b / 4), e = b % 4;
    const f = Math.floor((b + 8) / 25), g = Math.floor((b - f + 1) / 3), h = (19 * a + b - d - g + 15) % 30;
    const i = Math.floor(c / 4), k = c % 4, l = (32 + 2 * e + 2 * i - h - k) % 7, m = Math.floor((a + 11 * h + 22 * l) / 451);
    const month = Math.floor((h + l - 7 * m + 114) / 31);
    return [month, ((h + l - 7 * m + 114) % 31) + 1];
}

const holidayCache = new Map();

/** NYSE full-day holidays of year `y`: {"YYYY-MM-DD": name} (weekend dates moved to Friday/Monday). */
function holidays(y) {
    if (holidayCache.has(y)) return holidayCache.get(y);
    const result = {};
    const fixed = (m, d, name, { saturdayToFriday = true } = {}) => {
        const wd = weekdayOf(y, m, d);
        if (wd === 6) {
            if (saturdayToFriday) result[key(...addDays(y, m, d, -1))] = name;
        } else if (wd === 0) result[key(...addDays(y, m, d, 1))] = name;
        else result[key(y, m, d)] = name;
    };
    fixed(1, 1, "New Year's Day", { saturdayToFriday: false }); // NYSE doesn't close on Dec 31 for it
    result[key(y, 1, nthWeekday(y, 1, 1, 3))] = "Martin Luther King Jr. Day";
    result[key(y, 2, nthWeekday(y, 2, 1, 3))] = "Washington's Birthday";
    const [em, ed] = easter(y);
    result[key(...addDays(y, em, ed, -2))] = "Good Friday";
    result[key(y, 5, lastWeekday(y, 5, 1))] = "Memorial Day";
    if (y >= 2022) fixed(6, 19, "Juneteenth");
    fixed(7, 4, "Independence Day");
    result[key(y, 9, nthWeekday(y, 9, 1, 1))] = "Labor Day";
    result[key(y, 11, nthWeekday(y, 11, 4, 4))] = "Thanksgiving Day";
    fixed(12, 25, "Christmas Day");
    holidayCache.set(y, result);
    return result;
}

/** Closing time in minutes of a trading day (13:00 on NYSE early-close days). */
function closeMinutes(y, m, d) {
    const isTradingDay = (dd) => weekdayOf(y, m, dd) % 6 !== 0 && !holidays(y)[key(y, m, dd)];
    if (m === 7 && d === 3 && isTradingDay(3)) return EARLY_CLOSE;
    if (m === 11 && d === nthWeekday(y, 11, 4, 4) + 1) return EARLY_CLOSE;
    if (m === 12 && d === 24 && isTradingDay(24)) return EARLY_CLOSE;
    return CLOSE;
}

function tradingDayInfo(y, m, d) {
    const wd = weekdayOf(y, m, d);
    if (wd === 0 || wd === 6) return { trading: false, reason: "weekend" };
    const holiday = holidays(y)[key(y, m, d)];
    if (holiday) return { trading: false, reason: holiday };
    return { trading: true, close: closeMinutes(y, m, d) };
}

/** UTC instant of New York wall time y-m-d + minutes (handles daylight saving time). */
function nyInstant(y, m, d, minutes) {
    const wall = Date.UTC(y, m - 1, d, 0, minutes);
    let guess = wall;
    for (let i = 0; i < 2; i += 1) {
        const p = nyParts(new Date(guess));
        const shownAsUtc = Date.UTC(p.year, p.month - 1, p.day, 0, p.minutes, p.seconds);
        guess += wall - shownAsUtc;
    }
    return new Date(guess);
}

/**
 * {state: "open" | "pre" | "post" | "closed", reason, earlyClose, closesAt, next: {event: "open" | "close", at: Date}}
 */
export function marketSession(now = new Date()) {
    const p = nyParts(now);
    const today = tradingDayInfo(p.year, p.month, p.day);
    const t = p.minutes;
    if (today.trading && t < today.close && t >= PRE_OPEN) {
        const earlyClose = today.close !== CLOSE;
        if (t >= OPEN) {
            return { state: "open", earlyClose, next: { event: "close", at: nyInstant(p.year, p.month, p.day, today.close) } };
        }
        return { state: "pre", earlyClose, next: { event: "open", at: nyInstant(p.year, p.month, p.day, OPEN) } };
    }
    // Find the next regular open (skipping weekends and holidays).
    let date = [p.year, p.month, p.day];
    if (!today.trading || t >= OPEN) date = addDays(...date, 1);
    for (let i = 0; i < 14 && !tradingDayInfo(...date).trading; i += 1) date = addDays(...date, 1);
    const next = { event: "open", at: nyInstant(...date, OPEN) };
    if (today.trading && t >= today.close && t < POST_CLOSE) return { state: "post", next };
    return { state: "closed", reason: today.trading ? null : today.reason, next };
}

/** "2h 05m", "35m", "3d 4h" */
export function formatDuration(ms) {
    const minutes = Math.max(0, Math.ceil(ms / 60_000));
    const days = Math.floor(minutes / 1440);
    const hours = Math.floor((minutes % 1440) / 60);
    const mins = minutes % 60;
    if (days) return `${days}d ${hours}h`;
    if (hours) return `${hours}h ${String(mins).padStart(2, "0")}m`;
    return `${mins}m`;
}

const etFormat = new Intl.DateTimeFormat("en-US", { timeZone: ZONE, weekday: "short", hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
const localFormat = new Intl.DateTimeFormat(undefined, { weekday: "short", hour: "2-digit", minute: "2-digit" });

/** Text, tone and tooltip for the title bar. */
export function describeMarket(now = new Date()) {
    const s = marketSession(now);
    const until = formatDuration(s.next.at - now);
    const at = `${etFormat.format(s.next.at)} ET`;
    const texts = {
        open: `Market open · closes in ${until}${s.earlyClose ? " (early close)" : ""}`,
        pre: `Pre-market · opens in ${until}`,
        post: `After hours · opens ${at}`,
        closed: `Market closed${s.reason && s.reason !== "weekend" ? ` (${s.reason})` : ""} · opens ${at}`,
    };
    const nextLabel = s.next.event === "close" ? "Closes" : "Opens";
    return {
        state: s.state,
        text: texts[s.state],
        title: [
            "US stock market (NYSE / Nasdaq), regular session 09:30-16:00 ET, Mon-Fri except holidays.",
            `Now ${etFormat.format(now)} ET.`,
            `${nextLabel} ${at} (${localFormat.format(s.next.at)} your time), in ${until}.`,
        ].join("\n"),
    };
}
