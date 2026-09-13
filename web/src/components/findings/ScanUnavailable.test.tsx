import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

import { ScanUnavailable } from "./ScanUnavailable";

// `next/link` needs an app router mounted; this route has no navigation under test, only an
// address, so an anchor is the whole of what the assertions care about.
vi.mock("next/link", () => ({
  default: ({ href, children, ...rest }: { href: string; children: ReactNode }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

describe("<ScanUnavailable /> — expired", () => {
  it("names the day the scan expired when the API still knows it", () => {
    render(<ScanUnavailable state="expired" expiresAt="2026-09-12T14:00:00Z" />);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(
      "This scan expired on 12 September 2026.",
    );
  });

  /**
   * A purged scan is a 404 and a 404 carries no date. Inventing one — "seven days after a
   * creation date nobody recorded" — would be exactly the invented number PRD §14.2 forbids, so
   * the sentence loses its clause rather than gaining a guess.
   */
  it("says the scan expired without a date when the API no longer has the row", () => {
    render(<ScanUnavailable state="expired" expiresAt={null} />);
    const heading = screen.getByRole("heading", { level: 1 });
    expect(heading).toHaveTextContent("This scan expired.");
    expect(heading.textContent).not.toMatch(/\d{4}/);
  });

  it("offers a new scan, because there is nothing here to retry", () => {
    render(<ScanUnavailable state="expired" expiresAt={null} />);
    expect(screen.getByRole("link", { name: "Start a new scan" })).toHaveAttribute("href", "/scan");
  });
});

describe("<ScanUnavailable /> — failed", () => {
  it("states the worker's own error beside the word 'failed'", () => {
    render(<ScanUnavailable state="failed" error="RemoteFetchError: connection refused" />);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(
      "Scan failed: RemoteFetchError: connection refused",
    );
  });

  it("still says the scan failed when the worker recorded no detail", () => {
    render(<ScanUnavailable state="failed" error={null} />);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(
      "Scan failed: no reason was recorded.",
    );
  });

  it("offers a retry link and a way to read the scan again", async () => {
    const onRetry = vi.fn();
    render(<ScanUnavailable state="failed" error="boom" onRetry={onRetry} />);

    expect(screen.getByRole("link", { name: "Start a new scan" })).toHaveAttribute("href", "/scan");
    await userEvent.click(screen.getByRole("button", { name: "Check this scan again" }));
    expect(onRetry).toHaveBeenCalledOnce();
  });

  it("renders an error string as text, never as markup (PRD SC-7)", () => {
    render(<ScanUnavailable state="failed" error="<img src=x onerror=alert(1)>" />);
    expect(screen.getByRole("heading", { level: 1 }).querySelector("img")).toBeNull();
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(
      "<img src=x onerror=alert(1)>",
    );
  });
});
