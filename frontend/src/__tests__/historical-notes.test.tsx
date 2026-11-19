import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { HistoricalNotes } from "../components/HistoricalNotes";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });
it("shows provenance and searches full original context without truncation", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({
    digest: "a".repeat(64), provenance_status: "retained_extraction_unverified",
    source_sha256: "b".repeat(64), notes: [
      { source_key: "one", year: 2020, sheet_name: "Example history", cell_reference: "B8",
        text: "First line\n" + "context ".repeat(200) + "unique ending", timestamp: null },
      { source_key: "two", year: 2021, sheet_name: "Other sheet", cell_reference: "C3", text: "Unrelated", timestamp: null },
    ],
  }), { status: 200, headers: { "Content-Type": "application/json" } }));
  render(<HistoricalNotes ready />);
  expect(await screen.findByText(/unique ending/)).toBeTruthy();
  expect(screen.getByText(/source workbook has not been reverified/i)).toBeTruthy();
  expect(screen.getByText(/2020.*Example history.*B8/)).toBeTruthy();
  await userEvent.type(screen.getByRole("searchbox", { name: "Search historical notes" }), "unique ending");
  expect(screen.queryByText("Unrelated")).toBeNull();
  expect(screen.getByText(/unique ending/).textContent).toContain("First line\n");
});
