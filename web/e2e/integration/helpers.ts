import { expect, type APIRequestContext, type Page } from "@playwright/test";

import tools from "../../src/mocks/data/tools-sample-27.json";

import { API_BASE_URL, LIVE_BASE_URL, SEEDED_SCAN_ID } from "../../playwright.config";

export { API_BASE_URL, LIVE_BASE_URL, SEEDED_SCAN_ID };

/**
 * `fixtures/tools/sample-27.json`, the same capture the seeded scan was built from, copied into
 * `src/mocks/data/` by `scripts/copy-fixtures.mjs`. The integration suite pastes it into the real
 * app so the worker triages it for real; nothing here is served by a mock.
 */
export const SAMPLE_TOOLS_JSON = JSON.stringify(tools, null, 2);
export const SAMPLE_TOOL_COUNT = (tools as { tools: unknown[] }).tools.length;

/** The deployment's own answer, read once so a test asserts against the API, not a literal. */
export async function models(request: APIRequestContext): Promise<{
  judge_enabled: boolean;
  remote_enabled: boolean;
  model_version: string;
}> {
  const response = await request.get(`${API_BASE_URL}/api/v1/models`);
  expect(response.ok(), "GET /models").toBeTruthy();
  return response.json();
}

/**
 * Pastes the sample into `/scan` and waits for the app to land on the new scan's results page.
 * Returns the scan id the API minted — never one this file chose.
 */
export async function startSampleScan(page: Page): Promise<string> {
  await page.goto("/scan");
  await page.getByLabel("Tools JSON").fill(SAMPLE_TOOLS_JSON);
  await expect(page.getByText(`Ready to triage ${SAMPLE_TOOL_COUNT} tools`)).toBeVisible();

  await page.getByRole("button", { name: `Triage ${SAMPLE_TOOL_COUNT} tools` }).click();
  await page.waitForURL(/\/scan\/[0-9a-f-]{36}$/, { timeout: 30_000 });

  const id = new URL(page.url()).pathname.split("/").pop();
  expect(id, "the API minted a scan id").toBeTruthy();
  return id as string;
}
