/**
 * Per-prompt overrides (interval, platforms, samples) shown as text and edited
 * in a Popover, so a long table does not mount three controls per row.
 */
import { useState } from "react";
import { Button, InputNumber, Popover, Space, Typography } from "antd";
import { EditOutlined } from "@ant-design/icons";
import type { Engine, Project, TrackedPrompt, TrackedPromptUpdate } from "@/api/endpoints";
import { EngineCheckboxes } from "@/components/EngineCheckboxes";
import { EngineDot } from "@/components/EngineTag";
import { IntervalPicker } from "@/components/IntervalPicker";

interface Props {
    project: Project;
    prompt: TrackedPrompt;
    onSave: (body: TrackedPromptUpdate) => Promise<void>;
}

export function OverridesEditor({ project, prompt, onSave }: Props) {
    const [open, setOpen] = useState(false);
    const [interval, setInterval] = useState<string | null>(prompt.interval);
    const [engines, setEngines] = useState<Engine[] | null>(prompt.engines);
    const [samples, setSamples] = useState<number | null>(prompt.samples_per_engine);
    const hasOverride = !!prompt.interval || !!prompt.engines || prompt.samples_per_engine !== null;
    const platforms = prompt.engines ?? project.engines;

    const reset = () => {
        setInterval(prompt.interval);
        setEngines(prompt.engines);
        setSamples(prompt.samples_per_engine);
    };

    return (
        <Space size={6} wrap>
            <span style={{ fontSize: 12 }}>
                <Typography.Text type={prompt.interval ? undefined : "secondary"}>
                    {prompt.interval ? `every ${prompt.interval}` : `inherits ${project.interval}`}
                </Typography.Text>
            </span>
            <span style={{ display: "inline-flex", gap: 3 }} title={platforms.join(", ")}>
                {platforms.map((e) => (
                    <EngineDot key={e} engine={e} size={7} />
                ))}
            </span>
            {prompt.samples_per_engine !== null && (
                <Typography.Text style={{ fontSize: 12 }}>
                    × {prompt.samples_per_engine}
                </Typography.Text>
            )}
            <Popover
                trigger="click"
                open={open}
                onOpenChange={(v) => {
                    if (v) reset();
                    setOpen(v);
                }}
                title="Overrides for this prompt"
                content={
                    <Space direction="vertical" size={10} style={{ width: 360 }}>
                        <div>
                            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                                Interval
                            </Typography.Text>
                            <IntervalPicker
                                allowInherit
                                inheritLabel={`Inherit (${project.interval})`}
                                value={interval}
                                onChange={setInterval}
                            />
                        </div>
                        <div>
                            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                                Platforms
                            </Typography.Text>
                            <EngineCheckboxes
                                allowInherit
                                inherited={project.engines}
                                value={engines}
                                onChange={setEngines}
                            />
                        </div>
                        <div>
                            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                                Samples per platform{" "}
                            </Typography.Text>
                            <InputNumber
                                aria-label="Samples override"
                                size="small"
                                min={1}
                                max={10}
                                placeholder="inherit"
                                value={samples}
                                onChange={(v) => setSamples(v ?? null)}
                                style={{ width: 90 }}
                            />
                        </div>
                        <Space>
                            <Button
                                size="small"
                                type="primary"
                                onClick={async () => {
                                    await onSave({
                                        interval,
                                        engines,
                                        samples_per_engine: samples,
                                        clear_overrides:
                                            interval === null &&
                                            engines === null &&
                                            samples === null,
                                    });
                                    setOpen(false);
                                }}
                            >
                                Save
                            </Button>
                            <Button
                                size="small"
                                disabled={!hasOverride}
                                onClick={async () => {
                                    await onSave({ clear_overrides: true });
                                    setOpen(false);
                                }}
                            >
                                Reset to project
                            </Button>
                            <Button size="small" type="text" onClick={() => setOpen(false)}>
                                Cancel
                            </Button>
                        </Space>
                    </Space>
                }
            >
                <Button
                    size="small"
                    type={hasOverride ? "default" : "text"}
                    icon={<EditOutlined />}
                    aria-label={`Edit overrides for ${prompt.prompt_text}`}
                >
                    {hasOverride ? "override" : ""}
                </Button>
            </Popover>
        </Space>
    );
}
