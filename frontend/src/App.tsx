import { useState } from "react";
import type { ReactNode } from "react";
import { Button, Drawer, Layout } from "antd";
import { Link, Route, Routes, useLocation } from "react-router-dom";

import ReportPage from "./pages/ReportPage";
import HistoryPage from "./pages/HistoryPage";
import NotFoundPage from "./pages/NotFoundPage";
import QualityPage from "./pages/QualityPage";
import RulesPage from "./pages/RulesPage";
import TaskPage from "./pages/TaskPage";
import UploadPage from "./pages/UploadPage";
import "./styles.css";

const { Sider, Content } = Layout;

type NavIcon = "document" | "history" | "rules" | "quality" | "report";
const NAV_ITEMS: { path: string; label: string; icon: NavIcon }[] = [
  { path: "/", label: "Проверка", icon: "document" },
  { path: "/history", label: "История", icon: "history" },
  { path: "/rules", label: "Правила", icon: "rules" },
  { path: "/quality", label: "Качество", icon: "quality" },
  { path: "/reports/example", label: "Пример отчёта", icon: "report" },
];

function NavSymbol({ name }: { name: NavIcon }) {
  const paths: Record<NavIcon, ReactNode> = {
    document: <><path d="M6 2.5h8l4 4V21.5H6a2 2 0 0 1-2-2v-15a2 2 0 0 1 2-2Z" /><path d="M14 2.5v4h4M8 14l2.5 2.5L15.5 11" /></>,
    history: <><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>,
    rules: <><path d="M7 5h13M7 12h13M7 19h13" /><path d="m2.5 5 1 1 2-2M2.5 12l1 1 2-2M2.5 19l1 1 2-2" /></>,
    quality: <><path d="M3 20.5h18M5 17v-5h3v5M10.5 17V7h3v10M16 17V3h3v14" /></>,
    report: <><path d="M6 2.5h9l4 4v13a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2v-15a2 2 0 0 1 2-2Z" /><path d="M15 2.5v4h4M8 11h8M8 15h8M8 19h5" /></>,
  };
  return <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">{paths[name]}</svg>;
}

function activePath(pathname: string): string | null {
  if (pathname === "/reports/example") return "/reports/example";
  if (pathname === "/history") return "/history";
  if (pathname.startsWith("/rules")) return "/rules";
  if (pathname.startsWith("/quality")) return "/quality";
  if (pathname === "/" || pathname.startsWith("/tasks/") || pathname.startsWith("/reports/")) return "/";
  return null;
}

export default function App() {
  const { pathname } = useLocation();
  const [menuOpen, setMenuOpen] = useState(false);
  const selected = activePath(pathname);
  const links = NAV_ITEMS.map((item) => (
    <Link key={item.path} to={item.path} className={`sidebar-link${selected === item.path ? " is-active" : ""}`} aria-current={selected === item.path ? "page" : undefined} onClick={() => setMenuOpen(false)}>
      <NavSymbol name={item.icon} />
      <span>{item.label}</span>
    </Link>
  ));

  return (
    <Layout className="app-shell">
      <Sider width={252} theme="light" className="app-sidebar">
        <Link to="/" className="brand" aria-label="Гриф — новая проверка"><span className="brand-mark" aria-hidden="true" /><span>Гриф</span></Link>
        <div className="sidebar-caption">РАБОЧЕЕ ПРОСТРАНСТВО</div>
        <nav className="sidebar-nav" aria-label="Основная навигация">{links}</nav>
        <div className="sidebar-foot">ИИ-аудитор документов</div>
      </Sider>
      <Layout className="app-main">
        <header className="mobile-header">
          <Link to="/" className="mobile-brand" aria-label="Гриф — новая проверка"><span className="brand-mark" aria-hidden="true" /><span>Гриф</span></Link>
          <Button className="mobile-menu-button" type="text" aria-label="Меню" aria-expanded={menuOpen} onClick={() => setMenuOpen(true)}>
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true"><path d="M3 6h18M3 12h18M3 18h18" /></svg>
          </Button>
        </header>
        <Content className="app-content">
          <Routes>
            <Route path="/" element={<UploadPage />} />
            <Route path="/history" element={<HistoryPage />} />
            <Route path="/tasks/:taskId" element={<TaskPage />} />
            <Route path="/reports/:reportId" element={<ReportPage />} />
            <Route path="/rules" element={<RulesPage />} />
            <Route path="/quality" element={<QualityPage />} />
            <Route path="*" element={<NotFoundPage />} />
          </Routes>
        </Content>
      </Layout>
      <Drawer title="Разделы" placement="right" width={280} open={menuOpen} onClose={() => setMenuOpen(false)} className="mobile-nav-drawer">
        <nav className="sidebar-nav" aria-label="Мобильная навигация">{links}</nav>
      </Drawer>
    </Layout>
  );
}
