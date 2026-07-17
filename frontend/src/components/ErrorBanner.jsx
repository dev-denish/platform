export default function ErrorBanner({ message, onRetry }) {
  if (!message) return null;
  return (
    <div className="error-banner" role="alert">
      <span className="error-banner-glyph" aria-hidden="true">
        !
      </span>
      <span className="error-banner-text">{message}</span>
      {onRetry ? (
        <button type="button" className="link-button" onClick={onRetry}>
          Retry
        </button>
      ) : null}
    </div>
  );
}
