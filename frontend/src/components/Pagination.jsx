export default function Pagination({ total, limit, offset, onChange }) {
  if (total <= limit) return null;
  const page = Math.floor(offset / limit) + 1;
  const pageCount = Math.ceil(total / limit);
  const canPrev = offset > 0;
  const canNext = offset + limit < total;

  return (
    <div className="pagination">
      <button
        type="button"
        className="pagination-btn"
        disabled={!canPrev}
        onClick={() => onChange(Math.max(0, offset - limit))}
      >
        ← Previous
      </button>
      <span className="pagination-status">
        Page {page} of {pageCount} · {total} total
      </span>
      <button
        type="button"
        className="pagination-btn"
        disabled={!canNext}
        onClick={() => onChange(offset + limit)}
      >
        Next →
      </button>
    </div>
  );
}
