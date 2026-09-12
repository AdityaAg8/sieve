// Copies the repository fixtures MSW serves into `src/mocks/data/` so the web app never
// reaches outside `web/` at build time. Run by `predev`, `prebuild`, `pretest`, `pree2e`
// and `postinstall`; `src/mocks/data/` is generated and git-ignored.
import { copyFileSync, mkdirSync } from "node:fs";
import { fileURLToPath } from "node:url";

const here = (p) => fileURLToPath(new URL(p, import.meta.url));
const OUT = here("../src/mocks/data");

const FIXTURES = [
  ["../../fixtures/verdicts/sample-27.json", "verdicts-sample-27.json"],
  ["../../fixtures/tools/sample-27.json", "tools-sample-27.json"],
];

mkdirSync(OUT, { recursive: true });
for (const [from, name] of FIXTURES) {
  copyFileSync(here(from), `${OUT}/${name}`);
  console.log(`fixtures → src/mocks/data/${name}`);
}
