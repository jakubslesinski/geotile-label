import { lazy, Suspense, type ReactElement } from "react";
import { Routes, Route, Navigate } from "react-router-dom";
import AppLayout from "./layouts/AppLayout";
import ProjectsView from "./views/ProjectsView";
import ProjectDashboard from "./views/ProjectDashboard";
import LabelView from "./views/LabelView";
import DatasetView from "./views/DatasetView";
import TrainingView from "./views/TrainingView";
import ResultsView from "./views/ResultsView";
import AnalysisView from "./views/AnalysisView";
import SettingsView from "./views/SettingsView";
import { isLiteEdition } from "./config/edition";

const AnnotationRendererBenchmarkView = import.meta.env.DEV
  ? lazy(() => import("./views/AnnotationRendererBenchmarkView"))
  : null;

export default function App() {
  // Edycja "lite": trasy zaawansowanych sekcji przekierowują do Datasetu, żeby ukryte
  // zakładki nie były osiągalne przez bezpośredni URL (nawigacja i tak ich nie pokazuje).
  const advanced = (element: ReactElement) =>
    isLiteEdition ? <Navigate to="../dataset" replace /> : element;

  return (
    <Routes>
      <Route element={<AppLayout />}>
        <Route path="/" element={<Navigate to="/projects" replace />} />
        <Route path="/projects" element={<ProjectsView />} />
        <Route path="/projects/:id" element={<ProjectDashboard />} />
        <Route path="/projects/:id/scenes/:sceneId/label" element={<LabelView />} />
        <Route path="/projects/:id/dataset" element={<DatasetView />} />
        <Route path="/projects/:id/analysis" element={advanced(<AnalysisView />)} />
        <Route path="/projects/:id/training" element={advanced(<TrainingView />)} />
        <Route path="/projects/:id/results" element={advanced(<ResultsView />)} />
        <Route path="/settings" element={<SettingsView />} />
        {AnnotationRendererBenchmarkView && (
          <Route
            path="/__benchmarks/annotation-renderer"
            element={(
              <Suspense fallback={null}>
                <AnnotationRendererBenchmarkView />
              </Suspense>
            )}
          />
        )}
      </Route>
    </Routes>
  );
}
