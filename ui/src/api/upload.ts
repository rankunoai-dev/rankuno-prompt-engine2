/**
 * Raw file upload with progress.
 *
 * `fetch` reports download progress and not upload progress, and an access log
 * can be tens of megabytes, so the one place this app uploads a file uses
 * `XMLHttpRequest` instead. Everything else about the call matches
 * `api/client.ts`: the project credential travels in the same header, and a
 * failure raises the same `ApiError` so callers handle 400/409/413/403 alike.
 *
 * A 403 is deliberately *not* retried here. The unlock dialog belongs to the
 * caller, which can re-run the upload once the credential is stored; retrying a
 * multi-megabyte body inside the transport would upload it twice.
 */
import { ApiError } from "./client";
import { PROJECT_AUTH_HEADER, tokenFor } from "@/lib/projectAuth";

export interface UploadOptions {
    contentType: string;
    /** 0 to 1, called as the body goes out. */
    onProgress?: (fraction: number) => void;
    signal?: AbortSignal;
}

/** POST `body` to `path` and parse the JSON reply. */
export function uploadFile<T>(
    projectId: string,
    path: string,
    body: Blob,
    { contentType, onProgress, signal }: UploadOptions,
): Promise<T> {
    return new Promise<T>((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        xhr.open("POST", path);
        xhr.setRequestHeader("Content-Type", contentType);
        const token = tokenFor(projectId);
        if (token) xhr.setRequestHeader(PROJECT_AUTH_HEADER, token);

        xhr.upload.onprogress = (event) => {
            if (onProgress && event.lengthComputable) onProgress(event.loaded / event.total);
        };
        xhr.onerror = () => reject(new ApiError(0, "The upload failed.", "Network error"));
        xhr.ontimeout = () => reject(new ApiError(0, "The upload timed out.", "Timeout"));
        xhr.onload = () => {
            let parsed: unknown = null;
            try {
                parsed = xhr.responseText ? JSON.parse(xhr.responseText) : null;
            } catch {
                parsed = xhr.responseText;
            }
            if (xhr.status >= 200 && xhr.status < 300) {
                resolve(parsed as T);
                return;
            }
            const envelope = (parsed && typeof parsed === "object" ? parsed : {}) as {
                detail?: unknown;
                code?: unknown;
            };
            const detail = "detail" in envelope ? envelope.detail : parsed;
            const code = typeof envelope.code === "string" ? envelope.code : null;
            reject(new ApiError(xhr.status, detail, xhr.statusText, code));
        };
        signal?.addEventListener("abort", () => xhr.abort(), { once: true });
        xhr.send(body);
    });
}
