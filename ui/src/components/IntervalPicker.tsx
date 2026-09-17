/**
 * Presets from `/api/options` plus a custom text (`4d`, `10h`, `6w`). With
 * `allowInherit`, an empty value means "inherit from the project".
 */
import { useEffect, useMemo, useState } from "react";
import { Input, Select, Space } from "antd";
import { useOptions } from "@/api/queries";
import { isPresetInterval } from "@/app/format";

const CUSTOM = "__custom";

export const INTERVAL_PATTERN = /^(daily|weekly|monthly|\d+[hdw])$/i;

interface Props {
    value?: string | null;
    onChange?: (value: string | null) => void;
    allowInherit?: boolean;
    inheritLabel?: string;
    id?: string;
    size?: "small" | "middle";
}

export function IntervalPicker({
    value,
    onChange,
    allowInherit = false,
    inheritLabel = "Inherit from project",
    id,
    size = "middle",
}: Props) {
    const { data: options } = useOptions();
    const presets = useMemo(() => options?.intervals ?? [], [options]);
    const isCustom = !!value && !isPresetInterval(value, presets);
    const [mode, setMode] = useState<string>(value ? (isCustom ? CUSTOM : value) : "");
    const [custom, setCustom] = useState(isCustom ? value! : "");

    useEffect(() => {
        const nowCustom = !!value && !isPresetInterval(value, presets);
        setMode(value ? (nowCustom ? CUSTOM : value) : "");
        if (nowCustom) setCustom(value!);
    }, [value, presets]);

    return (
        <Space.Compact style={{ width: "100%" }}>
            <Select
                id={id}
                size={size}
                aria-label="Interval"
                value={mode}
                style={{ minWidth: 180, flex: 1 }}
                onChange={(v) => {
                    setMode(v);
                    if (v === CUSTOM) return;
                    onChange?.(v || null);
                }}
                options={[
                    ...(allowInherit ? [{ value: "", label: inheritLabel }] : []),
                    ...presets.map((p) => ({ value: p.value, label: p.label })),
                    { value: CUSTOM, label: "Custom…" },
                ]}
            />
            {mode === CUSTOM && (
                <Input
                    size={size}
                    aria-label="Custom interval"
                    placeholder="e.g. 4d, 10h, 6w"
                    value={custom}
                    style={{ width: 140 }}
                    onChange={(e) => setCustom(e.target.value)}
                    onBlur={() => onChange?.(custom.trim().toLowerCase() || null)}
                    onPressEnter={() => onChange?.(custom.trim().toLowerCase() || null)}
                />
            )}
        </Space.Compact>
    );
}
