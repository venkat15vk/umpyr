import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import App from "./App";

describe("App shell", () => {
  it("renders the header and sandbox links", () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve({ ok: true, status: 200, text: () => Promise.resolve("[]") })));
    render(<App />);
    expect(screen.getByText("Umpyr")).toBeInTheDocument();
    expect(screen.getAllByText(/Rockler/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/BJ's Wholesale Club/).length).toBeGreaterThanOrEqual(2);
  });
});
