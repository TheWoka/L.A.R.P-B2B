import { Layout, Menu, Typography } from "antd";
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
  const selected = pathname.startsWith("/rules") ? "/rules" : pathname.startsWith("/quality") ? "/quality" : "/";

  return (
    <Layout style={{ minHeight: "100vh" }}>
      <Header style={{ display: "flex", alignItems: "center", gap: 32 }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 10, whiteSpace: "nowrap" }}>
          <Typography.Title level={3} style={{ color: "#fff", margin: 0 }}>Гриф</Typography.Title>
          <Typography.Text style={{ color: "#d9d9d9", fontSize: 12 }}>ИИ-аудитор документов</Typography.Text>
        </div>
        <Menu
          theme="dark"
          mode="horizontal"
          selectedKeys={[selected]}
          items={MENU_ITEMS}
          style={{ flex: 1, minWidth: 0 }}
        />
      </Header>
      <Content style={{ padding: "24px clamp(16px, 4vw, 48px)" }}>
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
