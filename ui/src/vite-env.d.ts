/// <reference types="vite/client" />

interface ImportMetaEnv {
    readonly VITE_MOCK?: string;
    readonly VITE_CONTROL_PLANE?: string;
}

interface ImportMeta {
    readonly env: ImportMetaEnv;
}
