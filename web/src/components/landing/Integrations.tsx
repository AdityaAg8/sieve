import Link from "next/link";

import {
  IconArrow,
  IconExport,
  IconFile,
  IconGate,
  IconScan,
  IconServer,
  IconTerminal,
} from "@/components/shell/Icons";
import { Reveal } from "@/components/shell/Reveal";

/**
 * Names, not logos: reproducing a third party's mark would be a brand-usage decision this
 * project has not earned. Each card says what the integration actually does, in the same
 * vocabulary the rest of the app uses, and points at the page that explains it.
 */
const INTEGRATIONS = [
  {
    name: "cisco-ai-mcp-scanner",
    role: "Read its YARA findings and triage them.",
    href: "/docs/quickstart",
    Icon: IconScan,
  },
  {
    name: "snyk agent-scan",
    role: "Read its report; never run a server for it.",
    href: "/docs/quickstart",
    Icon: IconFile,
  },
  {
    name: "SARIF 2.1.0",
    role: "Export findings into GitHub code scanning.",
    href: "/docs/cli",
    Icon: IconExport,
  },
  {
    name: "GitHub Action",
    role: "Fail a build on confirmed findings only.",
    href: "/docs/action",
    Icon: IconGate,
  },
  {
    name: "MCP server",
    role: "Let your own agent check a server before trusting it.",
    href: "/docs/mcp-server",
    Icon: IconServer,
  },
  {
    name: "CLI",
    role: "Run offline in an air-gapped pipeline, no API key.",
    href: "/docs/cli",
    Icon: IconTerminal,
  },
] as const;

export function Integrations() {
  return (
    <section aria-labelledby="integrations-heading" className="band">
      <div className="wrap flex flex-col gap-12">
        <Reveal className="flex max-w-[640px] flex-col gap-3">
          <p className="eyebrow-rail with-mark">Integrations · where it sits</p>
          <h2 id="integrations-heading" className="t-h2 text-ink-0">
            Fits into the pipeline you already run
          </h2>
          <p className="t-body text-ink-2 max-w-[56ch]">
            Sieve reads the scanner you have and writes the format your code scanning expects.
            It adds a verdict; it does not replace a tool.
          </p>
        </Reveal>

        <Reveal as="ul" stagger={80} className="grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
          {INTEGRATIONS.map(({ name, role, href, Icon }) => (
            <li key={name} className="card card-lift reveal">
              <span className="card-icon">
                <Icon size={20} />
              </span>
              <h3 className="t-h3 text-ink-0 break-words">{name}</h3>
              <p className="t-body text-ink-1">{role}</p>
              <Link href={href} className="link-arrow mt-auto">
                Read how
                <IconArrow size={16} />
              </Link>
            </li>
          ))}
        </Reveal>
      </div>
    </section>
  );
}
