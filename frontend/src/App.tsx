import { Layout } from "antd";
import { Link, Route, Routes, useLocation } from "react-router-dom";

import ReportPage from "./pages/ReportPage";
import QualityPage from "./pages/QualityPage";
import RulesPage from "./pages/RulesPage";
import TaskPage from "./pages/TaskPage";
import UploadPage from "./pages/UploadPage";
import "./styles.css";

const { Sider, Content } = Layout;

const NAV_ITEMS = [
  { path: "/", label: "Проверка" },
  { path: "/reports/example", label: "Отчёты" },
  { path: "/rules", label: "Правила" },
  { path: "/quality", label: "Качество" },
];

function activePath(pathname: string): string {
  if (pathname.startsWith("/reports")) return "/reports/example";
  if (pathname.startsWith("/rules")) return "/rules";
  if (pathname.startsWith("/quality")) return "/quality";
  return "/";
}

export default function App() {
  const { pathname } = useLocation();
  const selected = activePath(pathname);

  return (
    <Layout className="app-shell">
      <Sider width={252} theme="light" className="app-sidebar">
        <Link to="/" className="brand" aria-label="ГРИФ — проверка документа">
          <span className="brand-mark" aria-hidden="true" />
          <span>ГРИФ</span>
        </Link>
        <div className="sidebar-caption">РАБОЧЕЕ ПРОСТРАНСТВО</div>
        <nav className="sidebar-nav" aria-label="Основная навигация">
          {NAV_ITEMS.map((item) => (
            <Link
              key={item.path}
              to={item.path}
              className={`sidebar-link${selected === item.path ? " is-active" : ""}`}
              aria-current={selected === item.path ? "page" : undefined}
            >
              <span className="nav-mark" aria-hidden="true" />
              {item.label}
            </Link>
          ))}
        </nav>
        <div className="sidebar-foot">ИИ-аудитор документов</div>
      </Sider>
      <Layout className="app-main">
        <header className="mobile-header">
          <Link to="/" className="mobile-brand" aria-label="ГРИФ — проверка документа">
            <span className="brand-mark" aria-hidden="true" />
            <span>ГРИФ</span>
          </Link>
          <span>{NAV_ITEMS.find((item) => item.path === selected)?.label}</span>
          <details className="mobile-menu">
            <summary aria-label="Открыть разделы"><span aria-hidden="true" /></summary>
            <nav aria-label="Основная навигация">
              {NAV_ITEMS.map((item) => (
                <Link key={item.path} to={item.path} className={selected === item.path ? "is-active" : ""}>
                  {item.label}
                </Link>
              ))}
            </nav>
          </details>
        </header>
        <Content className="app-content">
          <Routes>
            <Route path="/" element={<UploadPage />} />
            <Route path="/tasks/:taskId" element={<TaskPage />} />
            <Route path="/reports/:reportId" element={<ReportPage />} />
            <Route path="/rules" element={<RulesPage />} />
            <Route path="/quality" element={<QualityPage />} />
          </Routes>
        </Content>
      </Layout>
    </Layout>
  );
}
