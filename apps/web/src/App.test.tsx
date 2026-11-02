import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import App from "./App";

describe("App shell", () => {
  it("renders the header and sandbox links", () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve({ ok: true, status: 200, text: () => Promise.resolve("[]") })));
    render(<App />);
    expect(screen.getByText("Umpyr Resolve")).toBeInTheDocument();
    expect(screen.getAllByText(/Rockler/).length).toBeGreaterThan(0);
    expect(screen.getByText("BJ's: Muse order lifecycle, Part 1, Check (before payment)")).toBeInTheDocument();
    expect(screen.getByText("BJ's: Muse order lifecycle, Part 2, Resolve (after the order)")).toBeInTheDocument();
    expect(screen.getByText("BJ's: End-to-end Muse order, agent-to-agent")).toBeInTheDocument();
    expect(screen.getAllByText("Check Risk & Intent").length).toBeGreaterThan(0);
    expect(screen.getAllByText("End-to-end").length).toBeGreaterThan(0);
  });
});
