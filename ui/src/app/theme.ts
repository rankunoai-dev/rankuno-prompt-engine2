/** Ant Design theme tokens for both modes; brand colours from the build brief. */
import { theme as antTheme, type ThemeConfig } from "antd";

export const BRAND = {
    accent: "#1f5eff",
    success: "#1a8f4d",
    warning: "#b7791f",
    danger: "#c0392b",
    star: "#e0a400",
} as const;

/** Fixed colour per platform so a series never changes hue when filtered. */
export const ENGINE_COLOR: Record<string, string> = {
    GOOGLE_AI_OVERVIEW: "#2a78d6",
    CHATGPT_SEARCH: "#eb6834",
    PERPLEXITY: "#1baf7a",
    GEMINI: "#7c6ce0",
};

export const ENGINE_LABEL: Record<string, string> = {
    GOOGLE_AI_OVERVIEW: "Google AI Overview",
    CHATGPT_SEARCH: "ChatGPT Search",
    PERPLEXITY: "Perplexity",
    GEMINI: "Gemini",
};

export const ENGINE_SHORT: Record<string, string> = {
    GOOGLE_AI_OVERVIEW: "Google AIO",
    CHATGPT_SEARCH: "ChatGPT",
    PERPLEXITY: "Perplexity",
    GEMINI: "Gemini",
};

export function buildTheme(dark: boolean, reducedMotion = false): ThemeConfig {
    return {
        cssVar: true,
        hashed: false,
        algorithm: dark ? antTheme.darkAlgorithm : antTheme.defaultAlgorithm,
        token: {
            colorPrimary: BRAND.accent,
            colorSuccess: BRAND.success,
            colorWarning: BRAND.warning,
            colorError: BRAND.danger,
            colorInfo: BRAND.accent,
            motion: !reducedMotion,
            borderRadius: 8,
            fontFamily:
                'system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif',
            fontSize: 14,
            colorBgLayout: dark ? "#0f1218" : "#f6f7f9",
            colorBgContainer: dark ? "#171b23" : "#ffffff",
            colorBorderSecondary: dark ? "#2a3140" : "#e3e6eb",
        },
        components: {
            Layout: {
                siderBg: dark ? "#12151c" : "#ffffff",
                headerBg: dark ? "#171b23" : "#ffffff",
                headerHeight: 52,
                headerPadding: "0 16px",
            },
            Menu: { itemBorderRadius: 8 },
            Card: { paddingLG: 16 },
            Table: { cellPaddingBlock: 10 },
        },
    };
}
