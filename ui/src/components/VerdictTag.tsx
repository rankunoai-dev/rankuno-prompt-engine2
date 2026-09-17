import { Tag } from "antd";
import {
    CheckCircleFilled,
    CloseCircleFilled,
    EyeInvisibleOutlined,
    MinusCircleFilled,
} from "@ant-design/icons";
import type { HealthVerdict } from "@/api/endpoints";

const STYLE: Record<HealthVerdict, { color: string; icon: React.ReactNode; label: string }> = {
    winning: { color: "success", icon: <CheckCircleFilled />, label: "Winning" },
    present: { color: "warning", icon: <MinusCircleFilled />, label: "Present" },
    invisible: { color: "default", icon: <EyeInvisibleOutlined />, label: "Invisible" },
    losing: { color: "error", icon: <CloseCircleFilled />, label: "Losing" },
};

export function asVerdict(v: string): HealthVerdict {
    return (["winning", "present", "invisible", "losing"] as const).includes(v as HealthVerdict)
        ? (v as HealthVerdict)
        : "invisible";
}

export function VerdictTag({ verdict, losingTo }: { verdict: string; losingTo?: string | null }) {
    const s = STYLE[asVerdict(verdict)];
    return (
        <Tag color={s.color} icon={s.icon} style={{ marginInlineEnd: 0 }}>
            {verdict === "losing" && losingTo ? `Losing to ${losingTo}` : s.label}
        </Tag>
    );
}
