import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { App } from "./App";
import { healthyBackend, modelsAvailable, noProviders } from "./test/fixtures";

vi.mock("./api/client", async () => {
  const actual = await vi.importActual<typeof import("./api/client")>("./api/client");
  return { ...actual, getHealth: vi.fn(), getModels: vi.fn() };
});
const { getHealth, getModels, NetworkError } = await import("./api/client");
const getHealthMock = getHealth as ReturnType<typeof vi.fn>;
const getModelsMock = getModels as ReturnType<typeof vi.fn>;

function renderApp() {
  return render(
    <MemoryRouter initialEntries={["/"]}>
      <App />
    </MemoryRouter>,
  );
}

afterEach(() => {
  vi.clearAllMocks();
});

describe("App shell + backend status", () => {
  it("shows the brand and an upload entry point", () => {
    getHealthMock.mockResolvedValue(healthyBackend);
    getModelsMock.mockResolvedValue(modelsAvailable);
    renderApp();
    expect(screen.getByText("QAOps AI")).toBeInTheDocument();
    expect(screen.getByText(/start run/i)).toBeInTheDocument();
  });

  it("reports the backend online with version and provider count", async () => {
    getHealthMock.mockResolvedValue(healthyBackend);
    getModelsMock.mockResolvedValue(modelsAvailable);
    renderApp();
    await waitFor(() =>
      expect(screen.getByText(new RegExp(healthyBackend.version))).toBeInTheDocument(),
    );
    // modelsAvailable has two providers.
    expect(screen.getByText(/2 providers/i)).toBeInTheDocument();
  });

  it("reports offline when health fails", async () => {
    getHealthMock.mockRejectedValue(new NetworkError());
    getModelsMock.mockResolvedValue(modelsAvailable);
    renderApp();
    await waitFor(() => expect(screen.getByText(/backend offline/i)).toBeInTheDocument());
  });

  it("stays online even if models fails, as long as health responds", async () => {
    getHealthMock.mockResolvedValue(healthyBackend);
    getModelsMock.mockRejectedValue(new NetworkError());
    renderApp();
    await waitFor(() =>
      expect(screen.getByText(new RegExp(healthyBackend.version))).toBeInTheDocument(),
    );
  });

  it("handles a backend with zero providers", async () => {
    getHealthMock.mockResolvedValue(healthyBackend);
    getModelsMock.mockResolvedValue(noProviders);
    renderApp();
    await waitFor(() =>
      expect(screen.getByText(new RegExp(healthyBackend.version))).toBeInTheDocument(),
    );
    expect(screen.getByText(/0 providers/i)).toBeInTheDocument();
  });
});
