import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderAt } from "../test/render";
import { UploadPage } from "./UploadPage";

const navigateMock = vi.fn();
vi.mock("react-router-dom", async () => {
  const actual =
    await vi.importActual<typeof import("react-router-dom")>("react-router-dom");
  return { ...actual, useNavigate: () => navigateMock };
});

vi.mock("../api/client", async () => {
  const actual =
    await vi.importActual<typeof import("../api/client")>("../api/client");
  return { ...actual, createTicketRun: vi.fn() };
});
const { createTicketRun } = await import("../api/client");

afterEach(() => {
  vi.clearAllMocks();
});

async function openTicketMode() {
  const user = userEvent.setup();
  renderAt(<UploadPage />);
  await user.click(screen.getByRole("tab", { name: /ticket/i }));
  return user;
}

describe("UploadPage ticket mode (Phase 35)", () => {
  it("shows the ticket fields without an Acceptance Criteria field", async () => {
    await openTicketMode();
    expect(screen.getByLabelText(/title/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/description/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/priority/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/labels/i)).toBeInTheDocument();
    // Acceptance Criteria has been removed from the primary ticket UI.
    expect(screen.queryByLabelText(/acceptance criteria/i)).toBeNull();
  });

  it("exposes an optional design / reference attachment input", async () => {
    await openTicketMode();
    const input = screen.getByLabelText(/design \/ reference material/i);
    expect(input).toBeInTheDocument();
    expect(input).toHaveAttribute("type", "file");
  });

  it("keeps Start run disabled until title and description are set", async () => {
    const user = await openTicketMode();
    expect(screen.getByRole("button", { name: /start run/i })).toBeDisabled();
    await user.type(screen.getByLabelText(/title/i), "Add OTP login");
    await user.type(screen.getByLabelText(/description/i), "Users log in with OTP.");
    expect(screen.getByRole("button", { name: /start run/i })).toBeEnabled();
  });

  it("submits ticket-only (no attachment) and navigates", async () => {
    vi.mocked(createTicketRun).mockResolvedValue({
      run_id: "run_ticket_1",
      status: "queued",
    });
    const user = await openTicketMode();
    await user.type(screen.getByLabelText(/title/i), "Add OTP login");
    await user.type(screen.getByLabelText(/description/i), "Users log in with OTP.");
    await user.click(screen.getByRole("button", { name: /start run/i }));
    await waitFor(() =>
      expect(navigateMock).toHaveBeenCalledWith("/runs/run_ticket_1"),
    );
    const [ticketArg, attachmentsArg] = vi.mocked(createTicketRun).mock.calls[0];
    expect(ticketArg.title).toBe("Add OTP login");
    // No attachment selected -> empty array (or nullish).
    expect(attachmentsArg ?? []).toEqual([]);
  });

  it("submits ticket + attachment, passing the File array to createTicketRun", async () => {
    vi.mocked(createTicketRun).mockResolvedValue({
      run_id: "run_ticket_2",
      status: "queued",
    });
    const user = await openTicketMode();
    await user.type(screen.getByLabelText(/title/i), "Ratings");
    await user.type(screen.getByLabelText(/description/i), "Show ratings on PDP.");
    const file = new File(["design evidence"], "design.txt", { type: "text/plain" });
    await user.upload(screen.getByLabelText(/design \/ reference material/i), file);
    await waitFor(() =>
      expect(screen.getByText(/Attached: design.txt/i)).toBeInTheDocument(),
    );
    await user.click(screen.getByRole("button", { name: /start run/i }));
    await waitFor(() =>
      expect(navigateMock).toHaveBeenCalledWith("/runs/run_ticket_2"),
    );
    const [, attachmentsArg] = vi.mocked(createTicketRun).mock.calls[0];
    expect(attachmentsArg).toHaveLength(1);
    expect((attachmentsArg as File[])[0].name).toBe("design.txt");
  });

  it("submits ticket + multiple attachments, preserving order", async () => {
    vi.mocked(createTicketRun).mockResolvedValue({
      run_id: "run_ticket_3",
      status: "queued",
    });
    const user = await openTicketMode();
    await user.type(screen.getByLabelText(/title/i), "Ratings");
    await user.type(screen.getByLabelText(/description/i), "Show ratings.");
    const a = new File(["A"], "a.txt", { type: "text/plain" });
    const b = new File(["B"], "b.md", { type: "text/markdown" });
    await user.upload(screen.getByLabelText(/design \/ reference material/i), [a, b]);
    await waitFor(() =>
      expect(screen.getByText(/Attached: a.txt, b.md/i)).toBeInTheDocument(),
    );
    await user.click(screen.getByRole("button", { name: /start run/i }));
    await waitFor(() =>
      expect(navigateMock).toHaveBeenCalledWith("/runs/run_ticket_3"),
    );
    const [, attachmentsArg] = vi.mocked(createTicketRun).mock.calls[0];
    expect((attachmentsArg as File[]).map((f) => f.name)).toEqual([
      "a.txt",
      "b.md",
    ]);
  });
});
