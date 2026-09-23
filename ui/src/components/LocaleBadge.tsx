/**
 * The market a project's numbers were captured from. Every verdict is
 * locale-specific, so the badge sits beside the numbers rather than hiding in
 * settings, and it says plainly that Gemini ignores the setting.
 */
import { Tag, Tooltip } from "antd";
import { EnvironmentOutlined } from "@ant-design/icons";
import type { Locale } from "@/api/endpoints";
import { ENGINE_LABEL } from "@/app/theme";

/** Engines whose vendor API accepts a location; mirrors `Engine.honours_locale`. */
export const LOCALE_AWARE_ENGINES = ["GOOGLE_AI_OVERVIEW", "CHATGPT_SEARCH", "PERPLEXITY"];
export const LOCALE_BLIND_ENGINES = ["GEMINI"];

export function localeLabel(locale: Locale | null | undefined): string {
    if (!locale) return "Server default";
    const place = locale.city ? `${locale.city}, ${locale.country}` : locale.country;
    return `${place} · ${locale.language}`;
}

const blindNote = `${LOCALE_BLIND_ENGINES.map((e) => ENGINE_LABEL[e] ?? e).join(", ")} has no location field in its API, so its samples follow the billing account's country whatever this says.`;

export function LocaleBadge({
    locale,
    size = "default",
}: {
    locale: Locale | null | undefined;
    size?: "default" | "small";
}) {
    const detail = locale
        ? [
              `Country ${locale.country}`,
              `Language ${locale.language}`,
              locale.region ? `Region ${locale.region}` : null,
              locale.serp_location ? `Google location "${locale.serp_location}"` : null,
              locale.timezone ? `Timezone ${locale.timezone}` : null,
          ]
              .filter(Boolean)
              .join(" · ")
        : "No market set: crawls use the server default (SERP_GL / SERP_HL / SERP_LOCATION).";
    return (
        <Tooltip
            title={
                <span>
                    {detail}
                    <br />
                    {blindNote}
                </span>
            }
        >
            <Tag
                icon={<EnvironmentOutlined />}
                bordered={false}
                style={{ marginInlineEnd: 0, fontSize: size === "small" ? 11 : undefined }}
            >
                {localeLabel(locale)}
            </Tag>
        </Tooltip>
    );
}
