import { Suspense, lazy } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";

// 路由级懒加载：/column 与 /admin 拆分为独立 chunk（A-13）
const ColumnPage = lazy(() => import("./pages/column/ColumnPage"));
const ColumnDetailPage = lazy(() => import("./pages/column/ColumnDetailPage"));
const AdminLoginPage = lazy(() => import("./pages/admin/AdminLoginPage"));
const AdminLayout = lazy(() => import("./pages/admin/AdminLayout"));
const DashboardPage = lazy(() => import("./pages/admin/DashboardPage"));
const ReviewPage = lazy(() => import("./pages/admin/ReviewPage"));
const SourcesPage = lazy(() => import("./pages/admin/SourcesPage"));
const RulesPage = lazy(() => import("./pages/admin/RulesPage"));
const ApisPage = lazy(() => import("./pages/admin/ApisPage"));
const SchedulePage = lazy(() => import("./pages/admin/SchedulePage"));

function PageFallback() {
  return (
    <div style={{ padding: 48, textAlign: "center", color: "#64748b" }}>
      页面加载中...
    </div>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <Suspense fallback={<PageFallback />}>
        <Routes>
          <Route path="/" element={<Navigate to="/column" replace />} />
          <Route path="/column" element={<ColumnPage />} />
          <Route path="/column/:id" element={<ColumnDetailPage />} />

          <Route path="/admin/login" element={<AdminLoginPage />} />
          <Route path="/admin" element={<AdminLayout />}>
            <Route index element={<Navigate to="/admin/review" replace />} />
            <Route path="dashboard" element={<DashboardPage />} />
            <Route path="review" element={<ReviewPage />} />
            <Route path="sources" element={<SourcesPage />} />
            <Route path="rules" element={<RulesPage />} />
            <Route path="apis" element={<ApisPage />} />
            <Route path="schedule" element={<SchedulePage />} />
          </Route>

          <Route path="*" element={<Navigate to="/column" replace />} />
        </Routes>
      </Suspense>
    </BrowserRouter>
  );
}
