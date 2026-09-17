/**
 * Completion announcements outside the page: browser Notification when the tab
 * is hidden, and a flashing tab title until the analyst comes back. The in-page
 * toast is raised by the caller through AntD's `notification`.
 */
const baseTitle = typeof document !== "undefined" ? document.title : "Prompt Engine";
let flashTimer: ReturnType<typeof setInterval> | null = null;

export function askNotifyPermission(): void {
    if (typeof Notification === "undefined") return;
    if (Notification.permission === "default") {
        Notification.requestPermission().catch(() => undefined);
    }
}

export function browserNotify(title: string, body: string): void {
    if (typeof Notification === "undefined") return;
    if (Notification.permission === "granted" && document.hidden) {
        try {
            new Notification(title, { body });
        } catch {
            /* not permitted in this context */
        }
    }
}

export function flashTitle(text: string): void {
    stopFlash();
    let on = false;
    flashTimer = setInterval(() => {
        on = !on;
        document.title = on ? `✓ ${text}` : baseTitle;
    }, 900);
    const stop = () => stopFlash();
    window.addEventListener("focus", stop, { once: true });
    document.addEventListener("click", stop, { once: true });
    if (!document.hidden) setTimeout(stop, 6000);
}

export function stopFlash(): void {
    if (flashTimer) clearInterval(flashTimer);
    flashTimer = null;
    document.title = baseTitle;
}

/** Everything at once: used when a job leaves the active state. */
export function announceJobDone(title: string, body: string): void {
    browserNotify(title, body);
    flashTitle(title);
}
