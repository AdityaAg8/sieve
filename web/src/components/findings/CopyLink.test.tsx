import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { COPIED, COPY_REFUSED, CopyLink } from "./CopyLink";

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
    const onResult = vi.fn();
    clipboard(writeText);
    render(<CopyLink onResult={onResult} />);

    await userEvent.click(screen.getByRole("button", { name: "Copy link" }));
    expect(writeText).toHaveBeenCalledWith(window.location.href);
    expect(onResult).toHaveBeenCalledWith(COPIED);
  });

  it("says so when the browser refuses, rather than claiming a copy that did not happen", async () => {
    const onResult = vi.fn();
    clipboard(async () => {
      throw new Error("denied");
    });
    render(<CopyLink onResult={onResult} />);

    await userEvent.click(screen.getByRole("button", { name: "Copy link" }));
    expect(onResult).toHaveBeenCalledWith(COPY_REFUSED);
  });

  /**
   * `navigator.clipboard` is undefined outside a secure context, so the call throws a
   * `TypeError` before any promise exists. That is a refusal like any other, not a crash.
   */
  it("survives a browser with no clipboard API at all", async () => {
    const onResult = vi.fn();
    render(<CopyLink onResult={onResult} />);

    await userEvent.click(screen.getByRole("button", { name: "Copy link" }));
    expect(onResult).toHaveBeenCalledWith(COPY_REFUSED);
  });

  it("announces nothing of its own: the results page owns the one status slot", () => {
    render(<CopyLink onResult={() => {}} />);
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });
});
