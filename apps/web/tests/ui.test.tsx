import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DataState } from "@/components/DataState";
import { DecisionForm } from "@/components/Report";
import { ResultView } from "@/components/ResultView";
import { Pager, Table } from "@/components/ui";
import { api } from "@/lib/api";
import Analyst from "@/app/analyst/page";
import Transactions from "@/app/transactions/page";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }), usePathname: () => "/", useSearchParams: () => new URLSearchParams() }));
vi.mock("recharts", () => {
  const Box = ({ children }: { children?: React.ReactNode }) => <div>{children}</div>;
  return { ResponsiveContainer: Box, BarChart: Box, LineChart: Box, Bar: Box, Line: Box, XAxis: Box, YAxis: Box, CartesianGrid: Box, Tooltip: Box, Legend: Box };
});
const ok = (body: unknown) => Promise.resolve(new Response(JSON.stringify(body), { status: 200 }));
const calls = () => (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls.map((c) => String(c[0]));
afterEach(() => vi.restoreAllMocks());

describe("DataState", () => {
  it("shows loading, error with retry, empty and content", () => {
    const retry = vi.fn();
    const { rerender } = render(<DataState loading error={null}>content</DataState>);
    expect(screen.getByRole("status")).toHaveTextContent("Loading");
    rerender(<DataState loading={false} error="boom" onRetry={retry}>content</DataState>);
    expect(screen.getByRole("alert")).toHaveTextContent("boom");
    fireEvent.click(screen.getByText("Retry"));
    expect(retry).toHaveBeenCalled();
    rerender(<DataState loading={false} error={null} empty emptyText="Nothing here">content</DataState>);
    expect(screen.getByText("Nothing here")).toBeInTheDocument();
    rerender(<DataState loading={false} error={null}>content</DataState>);
    expect(screen.getByText("content")).toBeInTheDocument();
  });
});

describe("api client", () => {
  it("surfaces API error messages, not stack traces", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(new Response(JSON.stringify({ error: { message: "Database unavailable" } }), { status: 503 }))));
    await expect(api("x")).rejects.toThrow("Database unavailable");
  });
});

describe("Transactions page", () => {
  it("renders API rows and applies filters as query parameters", async () => {
    const row = { transaction_id: "txn_1", event_time: "2026-01-01T10:00:00Z", account_id: "acc_1", amount: 12.5, channel: "pos", risk_score: 0.7, signals: [{ rule: "velocity" }], model_version: "rules-v1", status: "approved" };
    vi.stubGlobal("fetch", vi.fn(() => ok({ total: 1, limit: 25, offset: 0, items: [row] })));
    render(<Transactions />);
    expect(await screen.findByText("txn_1")).toBeInTheDocument();
    expect(screen.getByText("velocity")).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Account"), "acc_1");
    await userEvent.type(screen.getByLabelText("Min risk (0–1)"), "0.5");
    await userEvent.click(screen.getByText("Apply"));
    await waitFor(() => expect(calls().some((u) => u.includes("account_id=acc_1") && u.includes("min_risk=0.5"))).toBe(true));
  });
  it("shows an empty state and an error state", async () => {
    vi.stubGlobal("fetch", vi.fn(() => ok({ total: 0, limit: 25, offset: 0, items: [] })));
    const { unmount } = render(<Transactions />);
    expect(await screen.findByText(/No transactions match/)).toBeInTheDocument();
    unmount();
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(new Response("{}", { status: 500 }))));
    render(<Transactions />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Request failed (500)");
  });
});

describe("AI analyst page", () => {
  it("tells the user when live LLM access is unavailable and runs labelled reference queries", async () => {
    const reference = { status: "answered", mode: "demo_reference_sql", sql: "SELECT 1", columns: [{ name: "n", type: "number" }], rows: [[5]], chart: { type: "table" }, masked_columns: [], attempt_errors: [], suppressed_rows: 0 };
    vi.stubGlobal("fetch", vi.fn((url: string, init?: RequestInit) => {
      if (url.endsWith("bi/status")) return ok({ llm_available: false, message: "Set ANTHROPIC_API_KEY." });
      if (url.endsWith("bi/demo")) return ok({ queries: [{ id: "d1", question: "Demo question" }] });
      if (url.endsWith("bi/demo/d1") && init?.method === "POST") return ok(reference);
      return ok({});
    }));
    render(<Analyst />);
    expect(await screen.findByText(/Live LLM unavailable/)).toBeInTheDocument();
    await userEvent.click(await screen.findByText("Demo question"));
    expect(await screen.findByLabelText("Generated SQL")).toHaveTextContent("SELECT 1");
    expect(screen.getAllByText("Reference SQL (no LLM)").length).toBeGreaterThan(0);
    expect(screen.getByText("5")).toBeInTheDocument();
  });
  it("renders rejections, masking and suppression notes from the API", () => {
    render(<ResultView r={{ status: "rejected", mode: "claude", model: "m", message: "Could not produce a valid query", columns: [], rows: [], masked_columns: ["acct"], suppressed_rows: 2, attempts: 3, attempt_errors: [{ stage: "policy", code: "table_not_allowed", message: "no" }], input_tokens: 1, output_tokens: 1, llm_ms: 5, db_ms: 0 }} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Could not produce");
    expect(screen.getByText(/pseudonymised: acct/)).toBeInTheDocument();
    expect(screen.getByText(/2 small-group row/)).toBeInTheDocument();
  });
});

describe("Analyst decision controls", () => {
  const run = { run_id: "r1", status: "pending_review", report: { human_decision: null } };
  it("requires a decision, shows the server-side identity, and needs a second click to confirm", async () => {
    const done = vi.fn();
    vi.stubGlobal("fetch", vi.fn((u: string) => ok(String(u).endsWith("whoami") ? { identity: "analyst-demo", role: "analyst" } : {})));
    render(<DecisionForm run={run} onDone={done} />);
    expect(await screen.findByText("analyst-demo (analyst)")).toBeInTheDocument();
    const btn = screen.getByRole("button", { name: "Record decision" });
    expect(btn).toBeDisabled();
    await userEvent.selectOptions(screen.getByLabelText("Decision"), "confirm_fraud");
    expect(btn).toBeEnabled();
    await userEvent.click(btn);
    expect((fetch as unknown as ReturnType<typeof vi.fn>).mock.calls.some((c) => String(c[0]).includes("/decision"))).toBe(false);
    await userEvent.click(screen.getByRole("button", { name: /confirm decision/ }));
    await waitFor(() => expect(done).toHaveBeenCalled());
    const [url, init] = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls.find((c) => String(c[0]).includes("/decision"))!;
    expect(String(url)).toContain("investigations/r1/decision");
    const sent = JSON.parse((init as RequestInit).body as string);
    expect(sent).toMatchObject({ decision: "confirm_fraud" });
    expect(sent).not.toHaveProperty("analyst");   // identity is never taken from the browser
  });
  it("shows the server error and hides the form once decided", async () => {
    vi.stubGlobal("fetch", vi.fn((u: string) => String(u).endsWith("whoami") ? ok({ identity: "analyst-demo", role: "analyst" }) : Promise.resolve(new Response(JSON.stringify({ detail: "investigation already decided" }), { status: 409 }))));
    const { rerender } = render(<DecisionForm run={run} onDone={vi.fn()} />);
    await userEvent.selectOptions(screen.getByLabelText("Decision"), "escalate");
    await userEvent.click(screen.getByRole("button", { name: "Record decision" }));
    await userEvent.click(screen.getByRole("button", { name: /confirm decision/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("already decided");
    rerender(<DecisionForm run={{ ...run, status: "escalated", report: { human_decision: { decision: "escalate", analyst: "Dana" } } }} onDone={vi.fn()} />);
    expect(screen.getByText(/Decided: escalate by Dana/)).toBeInTheDocument();
    expect(screen.queryByLabelText("Analyst decision")).toBeNull();
  });
});

describe("table and pager", () => {
  it("is keyboard operable and paginates", async () => {
    const onRow = vi.fn(); const onPage = vi.fn();
    render(<><Table rows={[{ a: 1 }]} cols={[{ key: "a", label: "A" }]} onRow={onRow} /><Pager total={50} limit={20} offset={20} onChange={onPage} /></>);
    fireEvent.keyDown(screen.getByRole("row", { name: /1/ }), { key: "Enter" });
    expect(onRow).toHaveBeenCalled();
    await userEvent.click(screen.getByText("Next →"));
    expect(onPage).toHaveBeenCalledWith(40);
  });
});
