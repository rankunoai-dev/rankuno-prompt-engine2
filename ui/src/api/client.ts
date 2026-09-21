/**
 * Thin typed fetch client for the control plane.
 *
 * Every response shape comes from `schema.d.ts`, generated from the server's
 * OpenAPI document (`npm run gen:types`). Nothing here re-declares an API
 * shape; the two routes the server declares as free-form objects
 * (`/api/health`, `/api/options`) are narrowed in `endpoints.ts` with a comment
 * pointing at the server code they mirror.
 */
import type { components } from "./schema";
import {
    PROJECT_AUTH_HEADER,
    clearToken,
    needsProjectAuth,
    projectIdFromPath,
    requestUnlock,
    tokenFor,
} from "@/lib/projectAuth";

export type Schemas = components["schemas"];

/** Error raised for any non-2xx response, carrying the server's `detail` verbatim. */
export class ApiError extends Error {
    readonly status: number;
    readonly detail: unknown;
    /** 422 validation errors keyed by the body field path (e.g. `client.brand_name`). */
    readonly fieldErrors: Record<string, string>;
    /** Machine-readable reason beside `detail`, e.g. `project_locked` (ADR 0019). */
    readonly code: string | null;

    constructor(status: number, detail: unknown, statusText: string, code: string | null = null) {
        super(ApiError.describe(status, detail, statusText));
        this.name = "ApiError";
        this.status = status;
        this.detail = detail;
        this.code = code;
        this.fieldErrors = ApiError.extractFields(detail);
    }

    private static describe(status: number, detail: unknown, statusText: string): string {
        if (typeof detail === "string") return detail;
        if (Array.isArray(detail)) {
            const items = detail as Schemas["ValidationError"][];
            return items
                .map((e) => `${e.loc.filter((l) => l !== "body").join(".")}: ${e.msg}`)
                .join("; ");
        }
        return statusText || `HTTP ${status}`;
    }

    private static extractFields(detail: unknown): Record<string, string> {
        if (!Array.isArray(detail)) return {};
        const out: Record<string, string> = {};
        for (const e of detail as Schemas["ValidationError"][]) {
            const path = e.loc.filter((l) => l !== "body").join(".");
            if (path) out[path] = e.msg;
        }
        return out;
    }
}

export interface RequestOptions {
    signal?: AbortSignal;
    /** Extra headers; a project credential given here is used instead of the stored one. */
    headers?: Record<string, string>;
    /** Never open the unlock dialog for this call (the dialog's own probe). */
    noUnlockPrompt?: boolean;
    query?: Record<string, string | number | boolean | null | undefined>;
}

function withQuery(path: string, query?: RequestOptions["query"]): string {
    if (!query) return path;
    const qs = new URLSearchParams();
    for (const [k, v] of Object.entries(query)) {
        if (v !== undefined && v !== null && v !== "") qs.set(k, String(v));
    }
    const s = qs.toString();
    return s ? `${path}?${s}` : path;
}

async function request<T>(
    method: "GET" | "POST" | "PUT" | "DELETE",
    path: string,
    body?: unknown,
    opts: RequestOptions = {},
    retried = false,
): Promise<T> {
    const headers: Record<string, string> = {};
    if (body !== undefined) headers["Content-Type"] = "application/json";
    const projectId = projectIdFromPath(path);
    const stored = projectId && needsProjectAuth(method, path) ? tokenFor(projectId) : null;
    if (stored) headers[PROJECT_AUTH_HEADER] = stored;
    Object.assign(headers, opts.headers);
    const res = await fetch(withQuery(path, opts.query), {
        method,
        headers: Object.keys(headers).length ? headers : undefined,
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: opts.signal,
    });
    if (res.status === 204) return undefined as T;
    const text = await res.text();
    let data: unknown = null;
    if (text) {
        try {
            data = JSON.parse(text);
        } catch {
            data = text;
        }
    }
    if (!res.ok) {
        const envelope = (data && typeof data === "object" ? data : {}) as {
            detail?: unknown;
            code?: unknown;
            owner?: unknown;
        };
        const detail = "detail" in envelope ? envelope.detail : data;
        const code = typeof envelope.code === "string" ? envelope.code : null;
        const refused = code === "project_locked" || code === "project_credentials_invalid";
        if (res.status === 403 && refused && projectId && !retried && !opts.noUnlockPrompt) {
            // A stored credential that was refused is stale (rotated elsewhere): drop it.
            if (code === "project_credentials_invalid") clearToken(projectId);
            const unlocked = await requestUnlock({
                projectId,
                owner: typeof envelope.owner === "string" ? envelope.owner : null,
                reason: code === "project_locked" ? "locked" : "invalid",
            });
            if (unlocked) return request<T>(method, path, body, opts, true);
        }
        throw new ApiError(res.status, detail, res.statusText, code);
    }
    return data as T;
}

export const http = {
    get: <T>(path: string, opts?: RequestOptions) => request<T>("GET", path, undefined, opts),
    post: <T>(path: string, body?: unknown, opts?: RequestOptions) =>
        request<T>("POST", path, body, opts),
    put: <T>(path: string, body?: unknown, opts?: RequestOptions) =>
        request<T>("PUT", path, body, opts),
    del: <T = void>(path: string, opts?: RequestOptions) =>
        request<T>("DELETE", path, undefined, opts),
};
