import { Grid, Layout, Menu, Typography } from "antd";
import { Link, Route, Routes, useLocation } from "react-router-dom";

import ReportPage from "./pages/ReportPage";
import QualityPage from "./pages/QualityPage";
import RulesPage from "./pages/RulesPage";
import TaskPage from "./pages/TaskPage";
import UploadPage from "./pages/UploadPage";

const { Header, Content, Footer } = Layout;

const MENU_ITEMS = [
  { key: "/", label: <Link to="/">Проверка документа</Link> },
  { key: "/rules", label: <Link to="/rules">Правила</Link> },
  { key: "/quality", label: <Link to="/quality">Качество</Link> },
];

export default function App() {
  const { pathname } = useLocation();
  const screens = Grid.useBreakpoint();
  const narrow = !screens.md;
  const selected = pathname.startsWith("/rules") ? "/rules" : pathname.startsWith("/quality") ? "/quality" : "/";

  return (
    <Layout style={{ minHeight: "100vh" }}>
      <Header style={{ display: "flex", alignItems: "center", gap: narrow ? 12 : 32, padding: narrow ? "0 12px" : "0 24px", minWidth: 0 }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 10, whiteSpace: "nowrap" }}>
          <Typography.Title level={3} style={{ color: "#fff", margin: 0 }}>Гриф</Typography.Title>
          {!narrow && <Typography.Text style={{ color: "#d9d9d9", fontSize: 12 }}>ИИ-аудитор документов</Typography.Text>}
        </div>
        {narrow ? (
          <nav aria-label="Основная навигация" style={{ display: "flex", alignItems: "center", gap: 12, minWidth: 0 }}>
            {[
              { path: "/", label: "Проверка" },
              { path: "/rules", label: "Правила" },
              { path: "/quality", label: "Качество" },
            ].map((item) => (
              <Link key={item.path} to={item.path} aria-current={selected === item.path ? "page" : undefined} style={{ color: selected === item.path ? "#fff" : "#bfc6d1", fontSize: 12, whiteSpace: "nowrap" }}>
                {item.label}
              </Link>
            ))}
          </nav>
        ) : (
          <Menu
            theme="dark"
            mode="horizontal"
            selectedKeys={[selected]}
            items={MENU_ITEMS}
            style={{ flex: 1, minWidth: 0 }}
          />
        )}
      </Header>
      <Content style={{ padding: narrow ? "16px 12px" : "24px clamp(16px, 4vw, 48px)", minWidth: 0 }}>
        <Routes>
          <Route path="/" element={<UploadPage />} />
          <Route path="/tasks/:taskId" element={<TaskPage />} />
          <Route path="/reports/:reportId" element={<ReportPage />} />
          <Route path="/rules" element={<RulesPage />} />
          <Route path="/quality" element={<QualityPage />} />
        </Routes>
      </Content>
      <Footer style={{ textAlign: "center" }}>Хакатон B2B-РТС · AI Procurement Copilot</Footer>
    </Layout>
  );
}
