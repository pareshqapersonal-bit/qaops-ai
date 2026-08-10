import { describe, expect, it, vi } from "vitest";
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

describe("UploadPage ticket mode", () => {
  it("switches to ticket mode and shows the ticket form", async () => {
    const user = userEvent.setup();
    renderAt(<UploadPage />);
    await user.click(screen.getByRole("tab", { name: /ticket/i }));
    expect(screen.getByLabelText(/title/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/description/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/acceptance criteria/i)).toBeInTheDocument();
  });

  it("keeps Start run disabled until title and description are set", async () => {
    const user = userEvent.setup();
    renderAt(<UploadPage />);
    await user.click(screen.getByRole("tab", { name: /ticket/i }));
    expect(screen.getByRole("button", { name: /start run/i })).toBeDisabled();
    await user.type(screen.getByLabelText(/title/i), "Add OTP login");
    await user.type(screen.getByLabelText(/description/i), "Users log in with OTP.");
    expect(screen.getByRole("button", { name: /start run/i })).toBeEnabled();
  });

  it("submits the ticket and navigates to the run", async () => {
    vi.mocked(createTicketRun).mockResolvedValue({
      run_id: "run_ticket_1",
      status: "queued",
    });
    const user = userEvent.setup();
    renderAt(<UploadPage />);
    await user.click(screen.getByRole("tab", { name: /ticket/i }));
    await user.type(screen.getByLabelText(/title/i), "Add OTP login");
    await user.type(
      screen.getByLabelText(/description/i),
      "Users log in with OTP.",
    );
    await user.type(
      screen.getByLabelText(/acceptance criteria/i),
      "OTP is sent.\nValid OTP logs in.",
    );
    await user.click(screen.getByRole("button", { name: /start run/i }));
    await waitFor(() =>
      expect(navigateMock).toHaveBeenCalledWith("/runs/run_ticket_1"),
    );
    const arg = vi.mocked(createTicketRun).mock.calls[0][0];
    expect(arg.title).toBe("Add OTP login");
    expect(arg.acceptance_criteria).toEqual([
      "OTP is sent.",
      "Valid OTP logs in.",
    ]);
  });
});
