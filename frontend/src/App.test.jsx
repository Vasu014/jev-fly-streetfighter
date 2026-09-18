import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { App } from "./App";


afterEach(() => {
  vi.restoreAllMocks();
});

test("renders backend dependency states without claiming stopped runtimes are active", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
    ok: true,
    json: async () => ({
      dependencies: {
        typesafe: { status: "configured_unchecked", ready: false },
        flybrain: { status: "dependencies_ready", ready: false },
        mame: { status: "rom_not_configured", ready: false },
      },
    }),
  }));

  render(<App />);

  expect(screen.getByText("Checking backend…")).toBeInTheDocument();
  await waitFor(() => expect(screen.getByText("Backend online")).toBeInTheDocument());
  expect(screen.getByText("configured unchecked")).toBeInTheDocument();
  expect(screen.getByText("dependencies ready")).toBeInTheDocument();
  expect(screen.getByText("rom not configured")).toBeInTheDocument();
  expect(screen.getByText("Idle by design")).toBeInTheDocument();
  expect(fetch).toHaveBeenCalledWith("/api/health", expect.any(Object));
});

test("shows a useful failure state when health cannot be reached", async () => {
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));

  render(<App />);

  await waitFor(() => expect(screen.getByText("Backend unavailable")).toBeInTheDocument());
});
