import { Button, Result } from "antd";
import { Link } from "react-router-dom";

export function NotFoundPage() {
    return (
        <Result
            status="404"
            title="This page does not exist"
            extra={
                <Link to="/projects">
                    <Button type="primary">Go to projects</Button>
                </Link>
            }
        />
    );
}
