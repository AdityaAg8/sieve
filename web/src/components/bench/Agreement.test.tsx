import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { BenchSummary } from "@/lib/api";
import { labelAuditOf } from "@/lib/bench";
import fixture from "@/mocks/fixtures/bench-summary.json";

import { Agreement } from "./Agreement";
import { HumanReference } from "./HumanReference";

const audit = labelAuditOf(fixture as unknown as BenchSummary);

/**
 * Finding F73: v1 has no second human annotator (PRD M8 is not met, a disclosed deviation).
 * The agreement figures are one human's gold labels against the labelling ensemble on the
 * 104-row sealed audit, and both panels have to say exactly that.
 */
describe("<Agreement />", () => {
  it("labels κ as human vs ensemble on the 104-row sealed audit", () => {
    render(<Agreement audit={audit} ensemble={undefined} />);
    expect(screen.getAllByText(/human vs ensemble, 104-row sealed audit/).length).toBeGreaterThan(0);
    expect(screen.getByText("0.90")).toBeInTheDocument();
    expect(screen.getByText(/95% CI \[0\.81, 0\.98\]/)).toBeInTheDocument();
  });

  it("never calls the ensemble a second annotator", () => {
    const { container } = render(<Agreement audit={audit} ensemble={undefined} />);
    expect(container.textContent).not.toMatch(/second annotator/i);
  });

  it("keeps checker-vs-generator agreement out of the κ family", () => {
    render(
      <Agreement audit={audit} ensemble={{ synthetic: { checker_agreement_round0: 0.86 } }} />,
    );
    const terms = screen.getAllByTestId("agreement-term").map((t) => t.textContent ?? "");
    const checker = terms.find((t) => t.startsWith("Checker-vs-generator"));
    expect(checker).toBeDefined();
    expect(checker).not.toContain("κ");
    expect(screen.getByText("0.86")).toBeInTheDocument();
  });

  it("renders dashes, not zeros, before the audit exists", () => {
    render(<Agreement audit={{}} ensemble={undefined} />);
    expect(screen.getAllByText("metric to confirm").length).toBeGreaterThan(0);
    expect(screen.queryByText("0.00")).not.toBeInTheDocument();
  });
});

describe("<HumanReference />", () => {
  it("reports the ensemble's error against the human on the sealed audit", () => {
    render(<HumanReference audit={audit} />);
    expect(screen.getByText(/human vs ensemble, 104-row sealed audit/)).toBeInTheDocument();
    expect(screen.getAllByText("3.8%").length).toBe(2);
    expect(screen.getByText("[1.1%, 9.6%]")).toBeInTheDocument();
    expect(screen.getByText("4 of 104")).toBeInTheDocument();
  });

  it("never claims a second annotator's recall", () => {
    const { container } = render(<HumanReference audit={audit} />);
    expect(container.textContent).not.toMatch(/second annotator/i);
  });

  it("says the audit has not run when there is none", () => {
    render(<HumanReference audit={{}} />);
    expect(screen.getByText(/No audit yet/)).toBeInTheDocument();
  });
});
