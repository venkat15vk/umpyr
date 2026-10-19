import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { Client } from "./pages/Client";

describe("Client page", () => {
  it("renders the toggles and examples", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string) => {
        const body = String(url).includes("/examples")
          ? JSON.stringify([{ category_key: "damaged_on_arrival", text: "My bit arrived chipped", expected: { decision: "approve", remedy: "replacement", amount: "1" } }])
          : JSON.stringify({ id: "s1", workspace_id: "rockler", channel: "email", mode: "autonomous", auto_approve_limit: 100, status: "open", turn: 0, category_key: null });
        return Promise.resolve({ ok: true, status: 200, text: () => Promise.resolve(body) });
      }),
    );
    render(
      <MemoryRouter initialEntries={["/rockler"]}>
        <Routes>
          <Route path="/:client" element={<Client />} />
        </Routes>
      </MemoryRouter>,
    );
    expect(screen.getByText("Rockler")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText(/arrived chipped/)).toBeInTheDocument());
  });
});
