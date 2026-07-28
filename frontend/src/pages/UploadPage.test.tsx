import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderAt } from "../test/render";
import { UploadPage } from "./UploadPage";

const navigateMock = vi.fn();
vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<typeof import("react-router-dom")>(
    "react-router-dom",
  );
  return { ...actual, useNavigate: () => navigateMock };
});

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return { ...actual, createDesignRun: vi.fn() };
});
const { createDesignRun } = await import("../api/client");

afterEach(() => {
  vi.clearAllMocks();
});

function fileInput(): HTMLInputElement {
  return document.querySelector('input[type="file"]') as HTMLInputElement;
}

describe("UploadPage", () => {
  it("renders the upload experience", () => {
    renderAt(<UploadPage />);
    expect(screen.getByText(/start run/i)).toBeInTheDocument();
  });

  it("disables Start Analysis until a valid file is selected", () => {
    renderAt(<UploadPage />);
    expect(screen.getByRole("button", { name: /start run/i })).toBeDisabled();
  });

  it("accepts a valid file and enables Start Analysis", async () => {
    const user = userEvent.setup();
    renderAt(<UploadPage />);
    const file = new File(["# requirements"], "reqs.md", { type: "text/markdown" });
    await user.upload(fileInput(), file);
    expect(screen.getByText("reqs.md")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /start run/i })).toBeEnabled();
  });

  it("rejects an unsupported file type", async () => {
    renderAt(<UploadPage />);
    // The file picker has an accept filter, so userEvent.upload won't deliver a
    // .rtf. A drag-drop (or a programmatic change) bypasses accept, which is
    // exactly the path the in-app validation guards. Fire the change directly.
    const input = fileInput();
    const file = new File(["x"], "notes.rtf", { type: "application/rtf" });
    Object.defineProperty(input, "files", { value: [file], configurable: true });
    input.dispatchEvent(new Event("change", { bubbles: true }));
    expect(await screen.findByText(/unsupported file type/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /start run/i })).toBeDisabled();
  });

  it("submits the run and navigates to the run view", async () => {
    (createDesignRun as ReturnType<typeof vi.fn>).mockResolvedValue({
      run_id: "run_42",
      status: "queued",
    });
    const user = userEvent.setup();
    renderAt(<UploadPage />);
    const file = new File(["# reqs"], "reqs.md", { type: "text/markdown" });
    await user.upload(fileInput(), file);
    await user.click(screen.getByRole("button", { name: /start run/i }));
    await waitFor(() => expect(navigateMock).toHaveBeenCalledWith("/runs/run_42"));
  });

  it("prevents double submission", async () => {
    let resolve: (v: unknown) => void = () => {};
    (createDesignRun as ReturnType<typeof vi.fn>).mockReturnValue(
      new Promise((r) => {
        resolve = r;
      }),
    );
    const user = userEvent.setup();
    renderAt(<UploadPage />);
    const file = new File(["# reqs"], "reqs.md", { type: "text/markdown" });
    await user.upload(fileInput(), file);
    const button = screen.getByRole("button", { name: /start run/i });
    await user.click(button);
    expect(button).toBeDisabled();
    await user.click(button);
    expect(createDesignRun).toHaveBeenCalledTimes(1);
    resolve({ run_id: "run_1", status: "queued" });
  });

  it("shows a clear error when submission fails", async () => {
    const { ApiError } = await import("../api/client");
    (createDesignRun as ReturnType<typeof vi.fn>).mockRejectedValue(
      new ApiError(400, "Uploaded file is empty."),
    );
    const user = userEvent.setup();
    renderAt(<UploadPage />);
    const file = new File(["# reqs"], "reqs.md", { type: "text/markdown" });
    await user.upload(fileInput(), file);
    await user.click(screen.getByRole("button", { name: /start run/i }));
    await waitFor(() =>
      expect(screen.getByText(/uploaded file is empty/i)).toBeInTheDocument(),
    );
  });

  it("lets the user remove a selected file", async () => {
    const user = userEvent.setup();
    renderAt(<UploadPage />);
    const file = new File(["# reqs"], "reqs.md", { type: "text/markdown" });
    await user.upload(fileInput(), file);
    expect(screen.getByText("reqs.md")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /remove/i }));
    expect(screen.queryByText("reqs.md")).not.toBeInTheDocument();
  });
});
