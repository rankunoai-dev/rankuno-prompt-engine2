/** Routes. Pages are lazy so each route ships its own chunk. */
import { Suspense, lazy } from "react";
import { Skeleton } from "antd";
import { Navigate, Route, Routes } from "react-router-dom";
import { AppShell } from "./app/AppShell";
import { NotFoundPage } from "./pages/NotFoundPage";

const ProjectsPage = lazy(() =>
    import("./pages/projects/ProjectsPage").then((m) => ({ default: m.ProjectsPage })),
);
const ProjectLayout = lazy(() =>
    import("./pages/project/ProjectLayout").then((m) => ({ default: m.ProjectLayout })),
);
const OverviewPage = lazy(() =>
    import("./pages/project/OverviewPage").then((m) => ({ default: m.OverviewPage })),
);
const BattlegroundPage = lazy(() =>
    import("./pages/project/BattlegroundPage").then((m) => ({ default: m.BattlegroundPage })),
);
const PromptsPage = lazy(() =>
    import("./pages/project/PromptsPage").then((m) => ({ default: m.PromptsPage })),
);
const RunsPage = lazy(() =>
    import("./pages/project/RunsPage").then((m) => ({ default: m.RunsPage })),
);
const ActionsPage = lazy(() =>
    import("./pages/project/ActionsPage").then((m) => ({ default: m.ActionsPage })),
);
const AtlasPage = lazy(() =>
    import("./pages/atlas/AtlasPage").then((m) => ({ default: m.AtlasPage })),
);
const CostsPage = lazy(() =>
    import("./pages/costs/CostsPage").then((m) => ({ default: m.CostsPage })),
);

const fallback = <Skeleton active paragraph={{ rows: 6 }} />;

export function App() {
    return (
        <Routes>
            <Route element={<AppShell />}>
                <Route index element={<Navigate to="/projects" replace />} />
                <Route
                    path="/projects"
                    element={
                        <Suspense fallback={fallback}>
                            <ProjectsPage />
                        </Suspense>
                    }
                />
                <Route
                    path="/projects/:id"
                    element={
                        <Suspense fallback={fallback}>
                            <ProjectLayout />
                        </Suspense>
                    }
                >
                    <Route index element={<Navigate to="overview" replace />} />
                    <Route
                        path="overview"
                        element={
                            <Suspense fallback={fallback}>
                                <OverviewPage />
                            </Suspense>
                        }
                    />
                    <Route
                        path="battleground"
                        element={
                            <Suspense fallback={fallback}>
                                <BattlegroundPage />
                            </Suspense>
                        }
                    />
                    <Route
                        path="prompts"
                        element={
                            <Suspense fallback={fallback}>
                                <PromptsPage />
                            </Suspense>
                        }
                    />
                    <Route
                        path="runs"
                        element={
                            <Suspense fallback={fallback}>
                                <RunsPage />
                            </Suspense>
                        }
                    />
                    <Route
                        path="actions"
                        element={
                            <Suspense fallback={fallback}>
                                <ActionsPage />
                            </Suspense>
                        }
                    />
                </Route>
                <Route
                    path="/atlas"
                    element={
                        <Suspense fallback={fallback}>
                            <AtlasPage />
                        </Suspense>
                    }
                />
                <Route
                    path="/costs"
                    element={
                        <Suspense fallback={fallback}>
                            <CostsPage />
                        </Suspense>
                    }
                />
                <Route path="*" element={<NotFoundPage />} />
            </Route>
        </Routes>
    );
}
