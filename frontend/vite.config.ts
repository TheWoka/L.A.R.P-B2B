import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Локальная разработка: `npm run dev` (порт 5173), запросы /api уходят на бэкенд.
// В docker-compose ту же роль играет nginx (nginx.conf).
export default defineConfig({
  plugins: [react()],
  build: {
    // Ant Design крупный; предупреждение о размере чанка поднимаем до 1 МБ.
    chunkSizeWarningLimit: 1000,
  },
  server: {
    port: 5173,
    proxy: {
      "/api": "http://localhost:8000",
    },
  },
});
