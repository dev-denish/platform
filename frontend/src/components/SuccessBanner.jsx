export default function SuccessBanner({ message }) {
  if (!message) return null;
  return (
    <div className="success-banner" role="status">
      <span className="success-banner-glyph" aria-hidden="true">
        ✓
      </span>
      <span className="success-banner-text">{message}</span>
    </div>
  );
}
