import { Reveal } from "@/components/shell/Reveal";

/**
 * The one place numbering is justified: these are three real stages of one pipeline, and the
 * order carries information the reader needs (a tool only reaches the judge if the classifier
 * put it in the band). Each step reaches toward the next with a dashed rule.
 */
const STAGES = [
  {
    n: "1",
    title: "Ingest",
    body: "Paste a tools/list dump, upload an mcp.json, or hand Sieve a scanner report. Every definition is parsed as data and redacted before it is stored. No server is started and no tool is called.",
  },
  {
    n: "2",
    title: "Classify",
    body: "A fine-tuned encoder reads the serialised definition and returns a probability over three classes: poisoned, imperative-benign, and benign. The two thresholds come from the model artefact, not from a constant in the code.",
  },
  {
    n: "3",
    title: "Judge",
    body: "Only tools that land between the thresholds reach the LLM judge, which must quote the span it relied on. Everything else is decided by the encoder alone — which is why most scans cost nothing.",
  },
] as const;

export function HowItWorks() {
  return (
    <section aria-labelledby="how-it-works" className="band">
      <div className="wrap flex flex-col gap-12">
        <Reveal className="flex max-w-[640px] flex-col gap-3">
          <p className="eyebrow-rail with-mark">Pipeline · three stages</p>
          <h2 id="how-it-works" className="t-h2 text-ink-0">
            How it works
          </h2>
          <p className="t-body text-ink-2 max-w-[56ch]">
            A tool passes three gates in order. Most are decided at the second and never cost a
            model call.
          </p>
        </Reveal>

        <Reveal as="ol" stagger={110} className="steps">
          {STAGES.map((stage) => (
            <li key={stage.n} className="step reveal">
              <p className="step-num" aria-hidden="true">
                {stage.n}
              </p>
              <h3 className="t-h3 text-ink-0">
                <span className="sr-only">Stage {stage.n}: </span>
                {stage.title}
              </h3>
              <p className="t-body text-ink-1 max-w-[46ch]">{stage.body}</p>
            </li>
          ))}
        </Reveal>
      </div>
    </section>
  );
}
