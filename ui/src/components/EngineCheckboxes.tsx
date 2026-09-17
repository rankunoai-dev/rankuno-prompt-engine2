/** Platform checkboxes; with `allowInherit`, null means the project's own set. */
import { Checkbox, Space } from "antd";
import { useOptions } from "@/api/queries";
import type { Engine } from "@/api/endpoints";
import { EngineDot } from "./EngineTag";

interface Props {
    value?: Engine[] | null;
    onChange?: (value: Engine[] | null) => void;
    allowInherit?: boolean;
    inherited?: Engine[];
}

export function EngineCheckboxes({ value, onChange, allowInherit = false, inherited }: Props) {
    const { data: options } = useOptions();
    const engines = options?.engines ?? [];
    const inherit = allowInherit && (value === null || value === undefined);
    const shown = inherit ? (inherited ?? []) : (value ?? []);
    return (
        <Space wrap size={[12, 4]}>
            {allowInherit && (
                <Checkbox
                    checked={inherit}
                    onChange={(e) => onChange?.(e.target.checked ? null : [...shown])}
                >
                    Inherit
                </Checkbox>
            )}
            {engines.map((e) => (
                <Checkbox
                    key={e.value}
                    disabled={inherit}
                    checked={shown.includes(e.value)}
                    onChange={(ev) => {
                        const next = ev.target.checked
                            ? [...shown, e.value]
                            : shown.filter((x) => x !== e.value);
                        onChange?.(next);
                    }}
                >
                    <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                        <EngineDot engine={e.value} />
                        {e.label}
                    </span>
                </Checkbox>
            ))}
        </Space>
    );
}
