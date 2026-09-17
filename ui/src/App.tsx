import { Navigate, Route, Routes } from "react-router-dom";
import { AppShell } from "./app/AppShell";
import { ProjectsPage } from "./pages/projects/ProjectsPage";
import { ProjectLayout } from "./pages/project/ProjectLayout";
import { OverviewPage } from "./pages/project/OverviewPage";
import { BattlegroundPage } from "./pages/project/BattlegroundPage";
import { PromptsPage } from "./pages/project/PromptsPage";
import { RunsPage } from "./pages/project/RunsPage";
import { ActionsPage } from "./pages/project/ActionsPage";
import { AtlasPage } from "./pages/atlas/AtlasPage";
import { CostsPage } from "./pages/costs/CostsPage";
import { NotFoundPage } from "./pages/NotFoundPage";

export function App() {
    return (
        <Routes>
            <Route element={<AppShell />}>
                <Route index element={<Navigate to="/projects" replace />} />
                <Route path="/projects" element={<ProjectsPage />} />
                <Route path="/projects/:id" element={<ProjectLayout />}>
                    <Route index element={<Navigate to="overview" replace />} />
                    <Route path="overview" element={<OverviewPage />} />
                    <Route path="battleground" element={<BattlegroundPage />} />
                    <Route path="prompts" element={<PromptsPage />} />
                    <Route path="runs" element={<RunsPage />} />
                    <Route path="actions" element={<ActionsPage />} />
                </Route>
                <Route path="/atlas" element={<AtlasPage />} />
                <Route path="/costs" element={<CostsPage />} />
                <Route path="*" element={<NotFoundPage />} />
            </Route>
        </Routes>
    );
}
