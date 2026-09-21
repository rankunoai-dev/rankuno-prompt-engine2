/**
 * Owner-credential UI (ADR 0019): the unlock dialog the HTTP client opens when
 * a write is refused, the lock badge in a project's header, and the dialog that
 * sets or rotates a credential.
 *
 * The unlock dialog probes `GET /access` with the typed credential before it is
 * stored, so a typo is reported in the dialog rather than by the retried write.
 */
import { useEffect, useRef, useState } from "react";
import { Alert, App, Button, Form, Input, Modal, Space, Tag, Tooltip, Typography } from "antd";
import { EditOutlined, LockOutlined, UnlockOutlined } from "@ant-design/icons";
import { ApiError } from "@/api/client";
import { endpoints, type Project } from "@/api/endpoints";
import { useSetProjectCredentials } from "@/api/mutations";
import {
    PROJECT_AUTH_HEADER,
    clearToken,
    encodeBasic,
    registerUnlockHandler,
    requestUnlock,
    setToken,
    useIsUnlocked,
    type UnlockRequest,
} from "@/lib/projectAuth";

interface Asked extends UnlockRequest {
    resolve: (unlocked: boolean) => void;
}

/** Mounted once in the shell. Answers every `requestUnlock` from the HTTP client. */
export function ProjectUnlockDialog() {
    const [asked, setAsked] = useState<Asked | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [busy, setBusy] = useState(false);
    const [form] = Form.useForm<{ owner: string; password: string }>();
    const askedRef = useRef<Asked | null>(null);
    askedRef.current = asked;

    useEffect(
        () =>
            registerUnlockHandler(
                (request) =>
                    new Promise<boolean>((resolve) => {
                        setError(
                            request.reason === "invalid"
                                ? "The saved credential no longer works. Enter the current one."
                                : null,
                        );
                        setAsked({ ...request, resolve });
                    }),
            ),
        [],
    );
    // A dialog left open when the shell unmounts must not leave a write hanging.
    useEffect(() => () => askedRef.current?.resolve(false), []);

    const close = (unlocked: boolean) => {
        asked?.resolve(unlocked);
        setAsked(null);
        setError(null);
    };

    const submit = async ({ owner, password }: { owner: string; password: string }) => {
        if (!asked) return;
        setBusy(true);
        setError(null);
        try {
            const access = await endpoints.projectAccess(asked.projectId, {
                headers: { [PROJECT_AUTH_HEADER]: encodeBasic(owner.trim(), password) },
                noUnlockPrompt: true,
            });
            if (access.can_write) {
                setToken(asked.projectId, owner.trim(), password);
                close(true);
            } else {
                setError("Wrong owner name or password.");
            }
        } catch (err) {
            setError(err instanceof ApiError ? err.message : "Could not check the credential.");
        } finally {
            setBusy(false);
        }
    };

    return (
        <Modal
            open={!!asked}
            title={
                <Space>
                    <LockOutlined />
                    Unlock this project to make changes
                </Space>
            }
            okText="Unlock"
            confirmLoading={busy}
            onOk={() => form.submit()}
            onCancel={() => close(false)}
            destroyOnClose
            width={420}
        >
            <Typography.Paragraph type="secondary">
                Everyone can read this project. Editing, running and deleting need the owner
                credential{asked?.owner ? ` set by ${asked.owner}` : ""}. It is kept for this
                browser tab only.
            </Typography.Paragraph>
            {error && <Alert type="error" showIcon message={error} style={{ marginBottom: 12 }} />}
            <Form
                form={form}
                layout="vertical"
                onFinish={submit}
                requiredMark={false}
                preserve={false}
                initialValues={{ owner: asked?.owner ?? "", password: "" }}
            >
                <Form.Item name="owner" label="Owner name" rules={[{ required: true }]}>
                    <Input autoComplete="username" />
                </Form.Item>
                <Form.Item name="password" label="Password" rules={[{ required: true }]}>
                    <Input.Password autoComplete="current-password" autoFocus />
                </Form.Item>
                {/* Enter submits: AntD's Modal footer button sits outside the form element. */}
                <button type="submit" hidden />
            </Form>
        </Modal>
    );
}

/** Password rules shared by the create form and the set/rotate dialog (server: 8 to 128). */
export const OWNER_RULES = [
    { required: true, message: "Give the owner a name" },
    { max: 64, message: "At most 64 characters" },
    { pattern: /^[^:\s][^:]*$/, message: "No colon, and no leading space" },
];
export const PASSWORD_RULES = [
    { required: true, message: "Set a password" },
    { min: 8, message: "At least 8 characters" },
    { max: 128, message: "At most 128 characters" },
];

function CredentialsDialog({
    project,
    open,
    onClose,
}: {
    project: Project;
    open: boolean;
    onClose: () => void;
}) {
    const { message } = App.useApp();
    const save = useSetProjectCredentials(project.id);
    const [form] = Form.useForm<{ owner: string; password: string; confirm: string }>();

    const submit = async (values: { owner: string; password: string }) => {
        try {
            await save.mutateAsync({ owner: values.owner.trim(), password: values.password });
            message.success(
                project.protected ? "Credential changed." : "Project protected. You hold the key.",
            );
            onClose();
        } catch (err) {
            message.error(err instanceof Error ? err.message : "Could not save the credential.");
        }
    };

    return (
        <Modal
            open={open}
            title={project.protected ? "Change the owner credential" : "Protect this project"}
            okText={project.protected ? "Change credential" : "Protect project"}
            confirmLoading={save.isPending}
            onOk={() => form.submit()}
            onCancel={onClose}
            destroyOnClose
            width={440}
        >
            <Typography.Paragraph type="secondary">
                {project.protected
                    ? "The current credential stops working as soon as the new one is saved."
                    : "Right now anyone who can open this app can edit, run and delete this project. With a credential set, everyone still reads it, and only its holder changes it."}{" "}
                There is no password reset in the app, so store it somewhere safe.
            </Typography.Paragraph>
            <Form
                form={form}
                layout="vertical"
                onFinish={submit}
                requiredMark={false}
                initialValues={{ owner: project.owner ?? "" }}
                preserve={false}
            >
                <Form.Item name="owner" label="Owner name" rules={OWNER_RULES}>
                    <Input autoComplete="username" />
                </Form.Item>
                <Form.Item name="password" label="New password" rules={PASSWORD_RULES}>
                    <Input.Password autoComplete="new-password" />
                </Form.Item>
                <Form.Item
                    name="confirm"
                    label="Repeat the password"
                    dependencies={["password"]}
                    rules={[
                        { required: true, message: "Repeat the password" },
                        ({ getFieldValue }) => ({
                            validator: (_, value) =>
                                !value || value === getFieldValue("password")
                                    ? Promise.resolve()
                                    : Promise.reject(new Error("The two passwords differ")),
                        }),
                    ]}
                >
                    <Input.Password autoComplete="new-password" />
                </Form.Item>
                <button type="submit" hidden />
            </Form>
        </Modal>
    );
}

/** Lock state and its controls, shown beside the project title. */
export function ProjectLockBadge({ project }: { project: Project }) {
    const unlocked = useIsUnlocked(project.id);
    const [editing, setEditing] = useState(false);

    if (!project.protected) {
        return (
            <Space size={4} data-testid="project-lock">
                <Tooltip title="No owner credential is set: anyone who can open this app can edit, run and delete this project.">
                    <Tag color="warning" bordered={false} icon={<UnlockOutlined />}>
                        Open to everyone
                    </Tag>
                </Tooltip>
                <Button size="small" type="link" onClick={() => setEditing(true)}>
                    Protect
                </Button>
                <CredentialsDialog
                    project={project}
                    open={editing}
                    onClose={() => setEditing(false)}
                />
            </Space>
        );
    }
    if (!unlocked) {
        return (
            <Space size={4} data-testid="project-lock">
                <Tooltip title="You can read everything. Changes, runs and deletes need the owner credential.">
                    <Tag bordered={false} icon={<LockOutlined />}>
                        Read-only · owner {project.owner}
                    </Tag>
                </Tooltip>
                <Button
                    size="small"
                    type="link"
                    onClick={() =>
                        void requestUnlock({
                            projectId: project.id,
                            owner: project.owner ?? null,
                            reason: "locked",
                        })
                    }
                >
                    Unlock to edit
                </Button>
            </Space>
        );
    }
    return (
        <Space size={4} data-testid="project-lock">
            <Tag color="success" bordered={false} icon={<UnlockOutlined />}>
                Unlocked · you can edit
            </Tag>
            <Button size="small" type="link" onClick={() => clearToken(project.id)}>
                Lock
            </Button>
            <Tooltip title="Change the owner name or password">
                <Button
                    size="small"
                    type="text"
                    aria-label="Change the owner credential"
                    icon={<EditOutlined />}
                    onClick={() => setEditing(true)}
                />
            </Tooltip>
            <CredentialsDialog project={project} open={editing} onClose={() => setEditing(false)} />
        </Space>
    );
}
