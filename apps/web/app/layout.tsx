import type { Metadata } from "next";
import { Nav } from "@/components/Nav";
import "./globals.css";

export const metadata: Metadata = { title: "FinSight", description: "Banking fraud analytics on synthetic data" };
export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <a href="#main" className="skip">Skip to content</a>
        <div className="shell"><Nav /><main id="main">{children}</main></div>
      </body>
    </html>
  );
}
