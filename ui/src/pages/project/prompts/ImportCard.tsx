/** Import prompts from a file or pasted text, or add one prompt. Files are read in the browser. */
import { useRef, useState } from "react";
import { App, Button, Card, Col, Input, Row, Space, Typography, Upload } from "antd";
import { UploadOutlined } from "@ant-design/icons";
import { useAddPrompt, useImportPrompts } from "@/api/mutations";
import { parseImportText } from "@/lib/promptView";

export function ImportCard({ projectId }: { projectId: string }) {
    const { message } = App.useApp();
    const importPrompts = useImportPrompts(projectId);
    const addPrompt = useAddPrompt(projectId);
    const [text, setText] = useState("");
    const [single, setSingle] = useState("");
    const [keyword, setKeyword] = useState("");
    const [subtopic, setSubtopic] = useState("");
    const fileRef = useRef<HTMLInputElement>(null);
    const parsed = parseImportText(text);

    const readFile = (file: File) => {
        const reader = new FileReader();
        reader.onload = () => setText(String(reader.result ?? ""));
        reader.onerror = () => message.error("Could not read that file.");
        reader.readAsText(file);
    };

    return (
        <Card title="Add prompts">
            <Row gutter={[24, 16]}>
                <Col xs={24} lg={14}>
                    <Space direction="vertical" style={{ width: "100%" }} size={8}>
                        <Space wrap>
                            <Upload
                                accept=".txt,.csv,text/plain"
                                showUploadList={false}
                                beforeUpload={(file) => {
                                    readFile(file);
                                    return false;
                                }}
                            >
                                <Button icon={<UploadOutlined />}>Choose a prompts file</Button>
                            </Upload>
                            <Typography.Text type="secondary">
                                One prompt per line. Optional: <code>| keyword | subtopic</code>
                            </Typography.Text>
                        </Space>
                        <Input.TextArea
                            aria-label="Prompts to import"
                            rows={5}
                            value={text}
                            onChange={(e) => setText(e.target.value)}
                            placeholder={
                                "How do I implement GEP procurement software? | gep implementation | Implementation"
                            }
                        />
                        <Space>
                            <Button
                                type="primary"
                                disabled={!parsed.length}
                                loading={importPrompts.isPending}
                                onClick={async () => {
                                    try {
                                        const added = await importPrompts.mutateAsync(text);
                                        message.success(`Imported ${added.length} prompt(s).`);
                                        setText("");
                                    } catch (err) {
                                        message.error(
                                            err instanceof Error ? err.message : "Import failed.",
                                        );
                                    }
                                }}
                            >
                                {parsed.length ? `Import ${parsed.length} prompt(s)` : "Import"}
                            </Button>
                            <input ref={fileRef} type="file" hidden />
                        </Space>
                    </Space>
                </Col>
                <Col xs={24} lg={10}>
                    <Space direction="vertical" style={{ width: "100%" }} size={8}>
                        <Input
                            aria-label="New prompt text"
                            placeholder="Type one prompt exactly as a user would ask it"
                            value={single}
                            onChange={(e) => setSingle(e.target.value)}
                        />
                        <Space.Compact block>
                            <Input
                                aria-label="Keyword"
                                placeholder="keyword (optional)"
                                value={keyword}
                                onChange={(e) => setKeyword(e.target.value)}
                            />
                            <Input
                                aria-label="Subtopic"
                                placeholder="subtopic (optional)"
                                value={subtopic}
                                onChange={(e) => setSubtopic(e.target.value)}
                            />
                        </Space.Compact>
                        <Button
                            disabled={single.trim().length < 3}
                            loading={addPrompt.isPending}
                            onClick={async () => {
                                try {
                                    await addPrompt.mutateAsync({
                                        prompt_text: single.trim(),
                                        keyword: keyword.trim() || null,
                                        subtopic: subtopic.trim() || null,
                                        important: false,
                                        enabled: true,
                                    });
                                    message.success("Prompt added.");
                                    setSingle("");
                                    setKeyword("");
                                    setSubtopic("");
                                } catch (err) {
                                    message.error(
                                        err instanceof Error
                                            ? err.message
                                            : "Could not add the prompt.",
                                    );
                                }
                            }}
                        >
                            Add prompt
                        </Button>
                    </Space>
                </Col>
            </Row>
        </Card>
    );
}
