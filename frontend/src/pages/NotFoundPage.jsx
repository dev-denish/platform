import { Link } from "react-router-dom";

export default function NotFoundPage() {
  return (
    <div className="full-screen-center">
      <div className="empty-state">
        <p className="empty-state-title">Page not found</p>
        <p className="empty-state-detail">The page you're looking for doesn't exist.</p>
        <Link to="/" className="primary-button">
          Back to portfolio
        </Link>
      </div>
    </div>
  );
}
