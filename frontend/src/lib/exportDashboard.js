import * as XLSX from "xlsx";

/**
 * Client-side-only workbook of the two Dashboard cards that are actually
 * tabular (composition + data quality) - a VVB doesn't need the chart pixels,
 * it needs the numbers behind them.
 */
export function exportDashboardXlsx({ projectName, compositionRows, qualityRows }) {
  const wb = XLSX.utils.book_new();
  XLSX.utils.book_append_sheet(wb, XLSX.utils.json_to_sheet(compositionRows), "Land cover composition");
  XLSX.utils.book_append_sheet(wb, XLSX.utils.json_to_sheet(qualityRows), "Data quality");
  XLSX.writeFile(wb, `${projectName}-dashboard.xlsx`);
}
