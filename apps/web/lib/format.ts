export const eur = (n: number | null | undefined) => (n == null ? "–" : new Intl.NumberFormat("en-IE", { style: "currency", currency: "EUR", maximumFractionDigits: 0 }).format(n));
export const int = (n: number | null | undefined) => (n == null ? "–" : new Intl.NumberFormat("en-IE").format(n));
export const pct = (n: number | null | undefined, d = 1) => (n == null ? "–" : `${(n * 100).toFixed(d)}%`);
export const when = (s: string | null | undefined) => (s ? new Date(s).toISOString().replace("T", " ").slice(0, 16) + " UTC" : "–");
