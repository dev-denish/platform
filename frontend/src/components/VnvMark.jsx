// VNV company mark: recreated from a text description as a scalable inline
// SVG (no raster asset available) — a radial burst of petals plus a
// vertical "Value / Network / Ventures" wordmark with each word's leading
// letter picked out in a brand token color. Distinct from the dMRV product
// brand (see AppShell's .shell-brand) — this is the company mark, dMRV is
// the platform name.
const PETAL_COUNT = 11;
const PETAL_PATH = "M0,-6 C3,-17 3,-33 0,-42 C-3,-33 -3,-17 0,-6 Z";

const WORDS = [
  { lead: "V", rest: "alue", color: "var(--accent)" },
  { lead: "N", rest: "etwork", color: "var(--accent-secondary)" },
  { lead: "V", rest: "entures", color: "var(--accent)" },
];

export default function VnvLogo({ size = 28, className = "" }) {
  return (
    <div className={`vnv-logo ${className}`} role="img" aria-label="VNV — Value Network Ventures">
      <svg viewBox="-50 -50 100 100" width={size} height={size} aria-hidden="true">
        {Array.from({ length: PETAL_COUNT }, (_, i) => (
          <path
            key={i}
            className="vnv-mark-petal"
            d={PETAL_PATH}
            transform={`rotate(${(360 / PETAL_COUNT) * i})`}
          />
        ))}
        <circle r="4.5" className="vnv-mark-petal" />
      </svg>
      <div className="vnv-wordmark" aria-hidden="true">
        {WORDS.map((w, i) => (
          <div key={i} className="vnv-word">
            <span className="vnv-word-lead" style={{ color: w.color }}>
              {w.lead}
            </span>
            {w.rest}
          </div>
        ))}
      </div>
    </div>
  );
}
