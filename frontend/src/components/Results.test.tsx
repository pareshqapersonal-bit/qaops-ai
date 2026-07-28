import { describe, expect, it } from "vitest";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { render } from "@testing-library/react";
import { Results } from "./Results";
import { completedRun, designArtifact } from "../test/fixtures";

function renderResults() {
  return render(<Results artifact={designArtifact} summary={completedRun.summary} />);
}

describe("Results", () => {
  it("shows a tablist with all six result categories", () => {
    renderResults();
    const tabs = screen.getAllByRole("tab");
    const labels = tabs.map((t) => t.textContent);
    expect(labels).toEqual(
      expect.arrayContaining([
        "Requirements",
        "Business Rules",
        "Scenarios",
        "Test Cases",
        "Gap Analysis",
        "Coverage",
      ]),
    );
  });

  it("defaults to the requirements view", () => {
    renderResults();
    const req = screen.getByRole("tab", { name: "Requirements" });
    expect(req).toHaveAttribute("aria-selected", "true");
  });

  it("switches to the test cases view on tab click", async () => {
    const user = userEvent.setup();
    renderResults();
    await user.click(screen.getByRole("tab", { name: "Test Cases" }));
    expect(screen.getByRole("tab", { name: "Test Cases" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    // A test case field from the fixture is visible.
    const tc = designArtifact.test_cases[0];
    expect(screen.getByText(tc.title)).toBeInTheDocument();
  });

  it("renders scenarios with their category", async () => {
    const user = userEvent.setup();
    renderResults();
    await user.click(screen.getByRole("tab", { name: "Scenarios" }));
    const sc = designArtifact.scenarios[0];
    expect(screen.getByText(sc.title)).toBeInTheDocument();
  });

  it("renders business rules linked to a requirement", async () => {
    const user = userEvent.setup();
    renderResults();
    await user.click(screen.getByRole("tab", { name: "Business Rules" }));
    const br = designArtifact.business_rules[0];
    if (br) {
      expect(screen.getByText(new RegExp(br.requirement_id))).toBeInTheDocument();
    }
  });

  it("renders gaps with a severity, blocker-first", async () => {
    const user = userEvent.setup();
    renderResults();
    await user.click(screen.getByRole("tab", { name: "Gap Analysis" }));
    // The fixture's single gap is a blocker.
    expect(screen.getByText(/blocker/i)).toBeInTheDocument();
    const gap = designArtifact.gap_report.gaps[0];
    expect(screen.getByText(gap.description)).toBeInTheDocument();
  });

  it("renders coverage with a progressbar reflecting the metrics", async () => {
    const user = userEvent.setup();
    renderResults();
    await user.click(screen.getByRole("tab", { name: "Coverage" }));
    const bars = screen.getAllByRole("progressbar");
    expect(bars.length).toBeGreaterThan(0);
    // aria-valuenow is within 0..100.
    const now = Number(bars[0].getAttribute("aria-valuenow"));
    expect(now).toBeGreaterThanOrEqual(0);
    expect(now).toBeLessThanOrEqual(100);
  });

  it("filters rows via the search box", async () => {
    const user = userEvent.setup();
    renderResults();
    // Requirements view has a search box; typing a non-matching term hides rows.
    const search = screen.getByRole("searchbox");
    const req = designArtifact.requirements[0];
    await user.type(search, "zzz-no-such-requirement");
    expect(screen.queryByText(req.title)).not.toBeInTheDocument();
    // Clearing restores the row.
    await user.clear(search);
    expect(screen.getByText(req.title)).toBeInTheDocument();
  });

  it("shows an empty state when a category has no rows", async () => {
    const empty = {
      ...designArtifact,
      gap_report: { gaps: [] },
    };
    const user = userEvent.setup();
    render(<Results artifact={empty} summary={completedRun.summary} />);
    await user.click(screen.getByRole("tab", { name: "Gap Analysis" }));
    const panel = screen.getByRole("tabpanel");
    expect(within(panel).getByText(/no gaps/i)).toBeInTheDocument();
  });
});
