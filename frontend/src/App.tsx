import { Layout, Menu, Typography } from "antd";
import { Link, Route, Routes, useLocation } from "react-router-dom";

import ReportPage from "./pages/ReportPage";
import RulesPage from "./pages/RulesPage";
import TaskPage from "./pages/TaskPage";
import UploadPage from "./pages/UploadPage";

const { Header, Content, Footer } = Layout;

const MENU_ITEMS = [
  { key: "/", label: <Link to="/">Проверка документа</Link> },
  { key: "/rules", label: <Link to="/rules">Правила</Link> },
];

export default function App() {
  const { pathname } = useLocation();
  const selected = pathname.startsWith("/rules") ? "/rules" : "/";

  return (
    <Layout style={{ minHeight: "100vh" }}>
      <Header style={{ display: "flex", alignItems: "center", gap: 32 }}>
        <Typography.Title level={4} style={{ color: "#fff", margin: 0, whiteSpace: "nowrap" }}>
          ИИ-аудитор документов
        </Typography.Title>
        <Menu
          theme="dark"
          mode="horizontal"
          selectedKeys={[selected]}
          items={MENU_ITEMS}
          style={{ flex: 1, minWidth: 0 }}
        />
      </Header>
      <Content style={{ padding: "24px 48px" }}>
        <Routes>
          <Route path="/" element={<UploadPage />} />
          <Route path="/tasks/:taskId" element={<TaskPage />} />
          <Route path="/reports/:reportId" element={<ReportPage />} />
          <Route path="/rules" element={<RulesPage />} />
        </Routes>
      </Content>
      <Footer style={{ textAlign: "center" }}>Хакатон B2B-РТС · AI Procurement Copilot</Footer>
    </Layout>
  );
}
