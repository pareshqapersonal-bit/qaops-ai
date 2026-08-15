import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderAt } from "../test/render";
import { UploadPage } from "./UploadPage";

const navigateMock = vi.fn();
vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<typeof import("react-router-dom")>("react-router-dom");
  return { ...actual, useNavigate: () => navigateMock };
});

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return { ...actual, createDesignRun: vi.fn(), createTicketRun: vi.fn() };
});
const { createDesignRun } = await import("../api/client");

afterEach(() => vi.clearAllMocks());

function fileInput(): HTMLInputElement {
  return document.querySelector('input[type="file"]') as HTMLInputElement;
}

describe("UploadPage clarify toggle", () => {
  it("renders the clarify checkbox unchecked by default", () => {
    renderAt(<UploadPage />);
    const checkbox = screen.getAllByRole("checkbox", { name: /clarify requirements first/i })[0];
    expect(checkbox).not.toBeChecked();
  });

  it("submits clarify=false by default", async () => {
    vi.mocked(createDesignRun).mockResolvedValue({ run_id: "r1", status: "queued" });
    renderAt(<UploadPage />);
    const file = new File(["content"], "req.md", { type: "text/markdown" });
    await userEvent.upload(fileInput(), file);
    await userEvent.click(screen.getByRole("button", { name: /start run/i }));
    await waitFor(() => expect(createDesignRun).toHaveBeenCalled());
    expect(createDesignRun).toHaveBeenCalledWith(file, false);
  });

  it("submits clarify=true when the checkbox is checked", async () => {
    vi.mocked(createDesignRun).mockResolvedValue({ run_id: "r1", status: "queued" });
    renderAt(<UploadPage />);
    const file = new File(["content"], "req.md", { type: "text/markdown" });
    await userEvent.upload(fileInput(), file);
    const checkbox = screen.getAllByRole("checkbox", { name: /clarify requirements first/i })[0];
    await userEvent.click(checkbox);
    await userEvent.click(screen.getByRole("button", { name: /start run/i }));
    await waitFor(() => expect(createDesignRun).toHaveBeenCalledWith(file, true));
  });
});
