import { Tag, Tooltip } from "antd";
import { ENGINE_COLOR, ENGINE_LABEL, ENGINE_SHORT } from "@/app/theme";

/** A platform chip with its fixed colour dot; `short` for dense rows. */
export function EngineTag({ engine, short = false }: { engine: string; short?: boolean }) {
    const label = (short ? ENGINE_SHORT[engine] : ENGINE_LABEL[engine]) ?? engine;
    return (
        <Tooltip title={ENGINE_LABEL[engine] ?? engine}>
            <Tag
                style={{
                    display: "inline-flex",
                    alignItems: "center",
                    gap: 6,
                    marginInlineEnd: 4,
                    borderColor: "transparent",
                    background: "var(--ant-color-fill-tertiary)",
                }}
            >
                <EngineDot engine={engine} />
                {label}
            </Tag>
        </Tooltip>
    );
}

export function EngineDot({ engine, size = 8 }: { engine: string; size?: number }) {
    return (
        <span
            aria-hidden
            style={{
                width: size,
                height: size,
                borderRadius: 2,
                background: ENGINE_COLOR[engine] ?? "var(--ant-color-text-tertiary)",
                display: "inline-block",
                flex: "none",
            }}
        />
    );
}
