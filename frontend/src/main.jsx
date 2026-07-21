import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";

// Phase 3 Wave E (GEE-style redesign): Inter, not "Google Sans" - that name is
// proprietary/trademarked, Inter is open-licensed and visually equivalent for
// this purpose. Self-hosted via @fontsource (same pattern already used for
// IBM Plex below) rather than a Google Fonts <link>: same-origin, no extra
// DNS/connection round trip, and index.css's --font-body/--font-display
// already list "Inter" first - it was just never actually loaded, silently
// falling through to IBM Plex Sans. Only latin-400/500/600 are imported,
// matching the weights this app actually uses (no 700+ headings anywhere).
import "@fontsource/inter/latin-400.css";
import "@fontsource/inter/latin-500.css";
import "@fontsource/inter/latin-600.css";
import "@fontsource/ibm-plex-mono/latin-400.css";
import "@fontsource/ibm-plex-mono/latin-500.css";
import "./index.css";

import App from "./App.jsx";

createRoot(document.getElementById("root")).render(
  <StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </StrictMode>
);
