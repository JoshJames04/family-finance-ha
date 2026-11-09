import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import { ingressBasename } from "./ingress";
import "./theme/index.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter basename={ingressBasename()}>
      <App />
    </BrowserRouter>
  </StrictMode>
);
