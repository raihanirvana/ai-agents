import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { CrashGuard } from "./components/ui";
import "./style.css";

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <CrashGuard><App /></CrashGuard>
  </React.StrictMode>,
);
