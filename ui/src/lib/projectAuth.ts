/**
 * Per-project owner credentials on the client (server side: ADR 0019).
 *
 * Everyone reads every project; changing, running or deleting a protected one
 * needs its owner credential, sent as `X-Project-Authorization`. The credential
 * a person unlocks with is kept in `sessionStorage`: it survives a reload,
 * disappears with the tab, and is never written to disk-backed `localStorage`
 * on a shared machine.
 *
 * The HTTP client asks for a credential through `requestUnlock` when the server
 * refuses a write. Whoever renders the dialog registers as the handler; with no
 * handler (unit tests, a page without the shell) the request resolves `false`
 * at once, so a refused write surfaces as an ordinary error instead of hanging.
 */
import { useSyncExternalStore } from "react";

export const PROJECT_AUTH_HEADER = "X-Project-Authorization";
const STORAGE_KEY = "pe.projectAuth.v1";

type Tokens = Record<string, string>;
const listeners = new Set<() => void>();
let cache: Tokens | null = null;

function read(): Tokens {
    if (cache) return cache;
    try {
        cache = JSON.parse(sessionStorage.getItem(STORAGE_KEY) ?? "{}") as Tokens;
    } catch {
        cache = {};
    }
    return cache;
}

function write(next: Tokens) {
    cache = next;
    try {
        sessionStorage.setItem(STORAGE_KEY, JSON.stringify(next));
    } catch {
        // Private mode or blocked storage: the credential still works until reload.
    }
    listeners.forEach((l) => l());
}

/** `Basic <base64(owner:password)>`, UTF-8 safe (btoa alone breaks on non-Latin-1). */
export function encodeBasic(owner: string, password: string): string {
    const bytes = new TextEncoder().encode(`${owner}:${password}`);
    let binary = "";
    bytes.forEach((b) => (binary += String.fromCharCode(b)));
    return `Basic ${btoa(binary)}`;
}

export function tokenFor(projectId: string): string | null {
    return read()[projectId] ?? null;
}

export function setToken(projectId: string, owner: string, password: string): void {
    write({ ...read(), [projectId]: encodeBasic(owner, password) });
}

export function clearToken(projectId: string): void {
    const next = { ...read() };
    delete next[projectId];
    write(next);
}

/** Test helper: forget every credential and any registered dialog. */
export function resetProjectAuth(): void {
    write({});
    handler = null;
    pending.clear();
}

function subscribe(listener: () => void): () => void {
    listeners.add(listener);
    return () => listeners.delete(listener);
}

/** True when this tab holds a credential for the project. */
export function useIsUnlocked(projectId: string | undefined): boolean {
    return useSyncExternalStore(subscribe, () => (projectId ? !!tokenFor(projectId) : false));
}

/** The project id of an API path under `/api/projects/{id}`, else null. */
export function projectIdFromPath(path: string): string | null {
    const match = /^\/api\/projects\/([^/?#]+)/.exec(path);
    return match ? decodeURIComponent(match[1]!) : null;
}

/** Which requests carry the credential: every write, plus the access probe. */
export function needsProjectAuth(method: string, path: string): boolean {
    if (!projectIdFromPath(path)) return false;
    return method !== "GET" || /\/access(\?|$)/.test(path);
}

// -- unlock requests ---------------------------------------------------------

export interface UnlockRequest {
    projectId: string;
    owner: string | null;
    /** `invalid`: a stored credential was refused (rotated or mistyped). */
    reason: "locked" | "invalid";
}

type UnlockHandler = (request: UnlockRequest) => Promise<boolean>;
let handler: UnlockHandler | null = null;
const pending = new Map<string, Promise<boolean>>();

/** Registered by the unlock dialog; returns the unregister function. */
export function registerUnlockHandler(next: UnlockHandler): () => void {
    handler = next;
    return () => {
        if (handler === next) handler = null;
    };
}

/**
 * Ask the person for the project's credential. Concurrent refusals for one
 * project (several optimistic writes) share a single dialog.
 */
export function requestUnlock(request: UnlockRequest): Promise<boolean> {
    if (!handler) return Promise.resolve(false);
    const existing = pending.get(request.projectId);
    if (existing) return existing;
    const asked = handler(request).finally(() => pending.delete(request.projectId));
    pending.set(request.projectId, asked);
    return asked;
}
