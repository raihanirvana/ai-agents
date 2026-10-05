import { expect, test } from '@playwright/test';
import { API, CORS, project } from './review-fixtures';

test('existing onboarding submits an explicit patch with pinned SHA and displays baseline failure honestly', async ({ page }) => {
  let current = { ...project, mode: 'existing' as const };
  let submitted: unknown;
  await page.route(`${API}/**`, async route => {
    const path = new URL(route.request().url()).pathname;
    if (route.request().method() === 'OPTIONS') return route.fulfill({ status: 204, headers: CORS });
    const json = (body: unknown) => route.fulfill({ json: body, headers: CORS });
    if (path === '/auth/session') return json({ csrf_token: 'csrf' });
    if (path === '/projects') return json({ projects: [current] });
    if (path.endsWith('/onboarding')) {
      submitted = route.request().postDataJSON();
      expect(route.request().headers()['idempotency-key']).toBeTruthy();
      current = { ...current, revision: 5, onboarding: 'ready_with_baseline_failures', accepted_tip: 'b'.repeat(40) };
      return json({ project: current, job_id: 'onboarding-1' });
    }
    if (path.endsWith('/tickets')) return json({ project: { ...current, ...(current.accepted_tip ? { onboarding_detail: {
      job_id: 'onboarding-1', source_sha: 'a'.repeat(40), baseline_sha: 'b'.repeat(40), dirty: true,
      dirty_status: [' M src/main.jsx'], patch_applied: true, required_checks: 'failed', report_artifact_id: 'baseline-1' } } : {}) },
      tickets: [], runs: [], preview: null, cursor: 5 });
    if (path.endsWith('/messages')) return json({ messages: [], cursor: 5 });
    if (path.endsWith('/events')) return route.abort();
    return json({});
  });
  await page.goto('/#/p/p1');
  await expect(page.getByText('Onboarding repo existing')).toBeVisible();
  await page.getByLabel('Runner manifest JSON').fill('{"runner":"react-vite"}');
  await page.getByLabel('Source HEAD SHA').fill('a'.repeat(40));
  await page.getByLabel('Patch eksplisit (opsional)').fill('chosen patch');
  await page.getByRole('button', { name: /Mulai onboarding/ }).click();
  await expect.poll(() => submitted).toEqual({ expected_revision: 4, manifest: { runner: 'react-vite' },
    source_sha: 'a'.repeat(40), patch: 'chosen patch' });
  // Completed details are collapsed; open them to inspect pinned identities and failed checks.
  await page.locator('summary').filter({ hasText: 'Onboarding repo existing' }).click();
  await expect(page.getByText('Required baseline checks: failed.', { exact: false })).toBeVisible();
  await expect(page.getByText('Patch eksplisit telah diterapkan.', { exact: false })).toBeVisible();
  await expect(page.getByRole('link', { name: 'Laporan baseline dan bukti commands' })).toHaveAttribute('href', /baseline-1\/content$/);
});
