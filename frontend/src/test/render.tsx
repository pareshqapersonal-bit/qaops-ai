import type { ReactElement } from "react";
import { render } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

// Render a component inside a router at a chosen path, for route-aware pages.
export function renderAt(ui: ReactElement, path = "/") {
  return render(<MemoryRouter initialEntries={[path]}>{ui}</MemoryRouter>);
}

// Render a component mounted on a route pattern so useParams() binds correctly
// (e.g. routePath="/runs/:runId", at="/runs/run_1").
export function renderRoute(ui: ReactElement, routePath: string, at: string) {
  return render(
    <MemoryRouter initialEntries={[at]}>
      <Routes>
        <Route path={routePath} element={ui} />
      </Routes>
    </MemoryRouter>,
  );
}
