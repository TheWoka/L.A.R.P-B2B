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
    <ConfigProvider locale={ruRU}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </ConfigProvider>
  </StrictMode>,
);
