"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";

const LINKS = [["/", "Overview"], ["/transactions", "Transactions"], ["/alerts", "Alerts"], ["/analytics", "Analytics"],
  ["/analyst", "AI analyst"], ["/investigations", "Investigations"], ["/models", "Model ops"], ["/operations", "Operations"]];
export function Nav() {
  const path = usePathname();
  return (
    <nav aria-label="Main">
      <div className="brand">FinSight<small>synthetic data</small></div>
      {LINKS.map(([href, label]) => (
        <Link key={href} href={href} className={path === href ? "active" : ""} aria-current={path === href ? "page" : undefined}>{label}</Link>
      ))}
    </nav>
  );
}
