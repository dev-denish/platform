export default function Legend({ items, title }) {
  if (!items || items.length === 0) return null;
  return (
    <div className="legend">
      {title ? <span className="legend-title">{title}</span> : null}
      <ul className="legend-list">
        {items.map((item) => (
          <li key={item.label} className="legend-item">
            <span className="legend-swatch" style={{ background: item.color }} aria-hidden="true" />
            {item.label}
          </li>
        ))}
      </ul>
    </div>
  );
}
