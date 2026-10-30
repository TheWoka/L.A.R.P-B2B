import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { ConfigProvider } from "antd";
import ruRU from "antd/locale/ru_RU";
import dayjs from "dayjs";
import "dayjs/locale/ru";

import App from "./App";

dayjs.locale("ru");

const root = document.getElementById("root");
if (!root) throw new Error("Не найден элемент #root");

createRoot(root).render(
  <StrictMode>
    <ConfigProvider locale={ruRU} theme={{
      token: {
        colorPrimary: "#087ff5",
        colorInfo: "#087ff5",
        colorText: "#1c1c1e",
        colorTextSecondary: "#77777c",
        colorBgLayout: "#f5f5f7",
        borderRadius: 12,
        fontFamily: 'Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
      },
    }}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </ConfigProvider>
  </StrictMode>,
);
