import { screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";

import { mockModels } from "@/mocks/handlers";
import { server } from "@/mocks/server";
import { renderWithProviders } from "@/test/render";

import { ScanTabs } from "./ScanTabs";

// The panels start a scan and navigate to it; nothing here exercises that, and jsdom has no
// app router to mount.
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: () => {} }) }));

function modelsSay(flags: { remote_enabled: boolean }): void {
  server.use(
    http.get("*/api/v1/models", () => HttpResponse.json({ ...mockModels, ...flags })),
  );
}

describe("<ScanTabs />", () => {
  it("hides the remote tab when the deployment has remote MCP off", async () => {
    modelsSay({ remote_enabled: false });
    renderWithProviders(<ScanTabs />);

    expect(await screen.findByRole("tab", { name: "Paste JSON" })).toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "Remote URL" })).not.toBeInTheDocument();
  });

  it("shows the remote tab when the deployment reports remote MCP on", async () => {
    modelsSay({ remote_enabled: true });
    renderWithProviders(<ScanTabs />);

    await waitFor(() =>
      expect(screen.getByRole("tab", { name: "Remote URL" })).toBeInTheDocument(),
    );
  });

  it("hides the remote tab while the deployment has not answered yet", () => {
    modelsSay({ remote_enabled: true });
    renderWithProviders(<ScanTabs />);
    // Off is the safe default: the feature is off by default, and a tab that appears and then
    // vanishes is worse than one that arrives a moment late.
    expect(screen.queryByRole("tab", { name: "Remote URL" })).not.toBeInTheDocument();
  });
});
