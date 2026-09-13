import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CopyLink } from "./CopyLink";

function clipboard(writeText: (text: string) => Promise<void>): void {
  Object.defineProperty(globalThis.navigator, "clipboard", {
    value: { writeText },
    configurable: true,
  });
}

afterEach(() => {
  Reflect.deleteProperty(globalThis.navigator, "clipboard");
});

describe("<CopyLink />", () => {
  it("copies this page's own address", async () => {
    const writeText = vi.fn(async () => {});
    clipboard(writeText);
    // `userEvent.setup()` installs its own clipboard stub; the default `fireEvent` path does not.
    render(<CopyLink />);

    await userEvent.click(screen.getByRole("button", { name: "Copy link" }));
    expect(writeText).toHaveBeenCalledWith(window.location.href);
    expect(await screen.findByText("Link copied.")).toBeInTheDocument();
  });

  it("says so when the browser refuses, rather than claiming a copy that did not happen", async () => {
    clipboard(async () => {
      throw new Error("denied");
    });
    render(<CopyLink />);

    await userEvent.click(screen.getByRole("button", { name: "Copy link" }));
    expect(
      await screen.findByText("The link could not be copied. Copy it from the address bar."),
    ).toBeInTheDocument();
  });

  it("reports the outcome through a live region, so it is not only a colour change", () => {
    render(<CopyLink />);
    expect(screen.getByRole("status")).toBeInTheDocument();
  });
});
