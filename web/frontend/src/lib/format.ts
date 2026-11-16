export function bytes(value?: number | null) {
  if (!value) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const order = Math.min(
    Math.floor(Math.log(Math.max(1, value)) / Math.log(1024)),
    4,
  );
  return `${(value / 1024 ** order).toFixed(order ? 1 : 0)} ${units[order]}`;
}
export function duration(value?: number | null) {
  if (!value) return "—";
  const seconds = Math.max(0, Math.round(value));
  return seconds >= 3600
    ? `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`
    : `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}
