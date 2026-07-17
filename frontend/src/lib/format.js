export function formatHectares(value) {
  if (value == null) return "—";
  return `${new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 }).format(value)} ha`;
}

export function formatNumber(value, digits = 2) {
  if (value == null) return "—";
  return new Intl.NumberFormat("en-US", { maximumFractionDigits: digits }).format(value);
}

export function formatDate(value) {
  if (!value) return "—";
  try {
    return new Date(value).toLocaleDateString("en-US", {
      year: "numeric",
      month: "short",
      day: "numeric",
    });
  } catch {
    return value;
  }
}

/** "class_area_mixed_forest" -> "Mixed forest"; "total_area" -> "Total area" */
export function humanizeMetricName(name) {
  const stripped = name.startsWith("class_area_") ? name.slice("class_area_".length) : name;
  const spaced = stripped.replace(/_/g, " ");
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}
