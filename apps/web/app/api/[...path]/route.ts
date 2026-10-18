// Server-side proxy: the browser only talks to this origin; the API key (if any) never reaches client code.
import { NextRequest, NextResponse } from "next/server";

const API_URL = process.env.API_URL ?? "http://localhost:8000";

async function forward(req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  const { path } = await ctx.params;
  if (path[0] === "metrics") return NextResponse.json({ error: { code: "not_found", message: "Not found" } }, { status: 404 });  // never expose metrics via the dashboard
  const reveal = req.nextUrl.searchParams.has("reveal_ids"); // the dashboard never requests raw identifiers
  if (reveal) return NextResponse.json({ error: { code: "forbidden", message: "reveal_ids is not available through the dashboard" } }, { status: 403 });
  const url = `${API_URL}/${path.join("/")}${req.nextUrl.search}`;
  const headers: Record<string, string> = { "content-type": req.headers.get("content-type") ?? "application/json" };
  if (process.env.API_KEY) headers["x-api-key"] = process.env.API_KEY;
  try {
    const res = await fetch(url, {
      method: req.method,
      headers,
      body: ["GET", "HEAD"].includes(req.method) ? undefined : await req.text(),
      cache: "no-store",
    });
    return new NextResponse(await res.text(), { status: res.status, headers: { "content-type": res.headers.get("content-type") ?? "application/json" } });
  } catch {
    return NextResponse.json({ error: { code: "upstream_unreachable", message: "API unreachable" } }, { status: 502 });
  }
}
export { forward as GET, forward as POST };
export const dynamic = "force-dynamic";
