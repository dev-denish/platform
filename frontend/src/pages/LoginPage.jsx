import { useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext.jsx";
import ErrorBanner from "../components/ErrorBanner.jsx";

export default function LoginPage() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(e) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await login(username, password);
      navigate(location.state?.from?.pathname ?? "/", { replace: true });
    } catch (err) {
      setError(err.message ?? "Sign in failed.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="login-screen">
      <ContourField />
      <div className="login-card">
        <div className="login-brand">
          <span className="shell-brand-mark" aria-hidden="true" />
          <div>
            <div className="shell-brand-name">dMRV</div>
            <div className="shell-brand-sub">Analytical Platform</div>
          </div>
        </div>

        <h1 className="login-title">Sign in</h1>
        <p className="login-subtitle">Verified land-cover, biomass, and carbon analytics.</p>

        <form onSubmit={handleSubmit} className="login-form">
          <label className="field">
            <span className="field-label">Username</span>
            <input
              className="field-input"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete="username"
              autoFocus
              required
            />
          </label>
          <label className="field">
            <span className="field-label">Password</span>
            <input
              className="field-input"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
              required
            />
          </label>

          <ErrorBanner message={error} />

          <button type="submit" className="primary-button" disabled={submitting}>
            {submitting ? "Signing in…" : "Sign in"}
          </button>
        </form>
      </div>
    </div>
  );
}

/** Signature background: irregular topographic contour lines, faint, evoking
 * the terrain rasters this platform measures. Pure decoration - aria-hidden. */
function ContourField() {
  const paths = [
    "M-50 400 C 150 340, 250 460, 450 380 S 750 300, 950 380",
    "M-50 320 C 180 260, 260 380, 480 300 S 760 220, 950 300",
    "M-50 240 C 210 180, 270 300, 510 220 S 770 150, 950 220",
    "M-50 160 C 230 110, 280 220, 540 150 S 780 90, 950 150",
    "M-50 480 C 130 430, 300 520, 420 460 S 700 400, 950 460",
  ];
  return (
    <svg className="contour-field" viewBox="0 0 900 560" preserveAspectRatio="xMidYMid slice" aria-hidden="true">
      {paths.map((d, i) => (
        <path key={i} d={d} fill="none" stroke="currentColor" strokeWidth="1" />
      ))}
    </svg>
  );
}
