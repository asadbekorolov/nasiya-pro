import "./index.css";

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { ToastHost } from "@/components/toast";
import { RoleGuard } from "@/features/auth/RoleGuard";
import { initTelegram } from "@/telegram/twa";

initTelegram();

const root = document.getElementById("root");
if (!root) throw new Error("#root element is missing");

createRoot(root).render(
  <StrictMode>
    <RoleGuard />
    <ToastHost />
  </StrictMode>,
);
