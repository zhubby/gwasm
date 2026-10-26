// Browser acceptance for the built bilingual Starlight portal.

const fs = require('fs');
const http = require('http');
const os = require('os');
const path = require('path');
const { spawn } = require('child_process');
const { chromium } = require('playwright-core');

const REPO = path.join(__dirname, '..');
const results = [];
const check = (name, condition, detail = '') => {
  results.push(condition);
  console.log(`  ${condition ? 'PASS' : 'FAIL'}  ${name}${condition ? '' : ` <- ${detail}`}`);
};

function findChrome() {
  if (process.env.CHROME_PATH) return process.env.CHROME_PATH;
  const installed = chromium.executablePath();
  if (fs.existsSync(installed)) return installed;
  const roots = [
    path.join(os.homedir(), '.cache', 'ms-playwright'),
    path.join(os.homedir(), 'Library', 'Caches', 'ms-playwright'),
  ];
  for (const root of roots) {
    if (!fs.existsSync(root)) continue;
    for (const directory of fs.readdirSync(root)) {
      for (const suffix of [
        'chrome-linux64/chrome', 'chrome-headless-shell-linux64/chrome-headless-shell',
        'chrome-linux/chrome', 'chrome-headless-shell-linux/headless_shell',
        'chrome-mac/Chromium.app/Contents/MacOS/Chromium',
        'chrome-headless-shell-mac-arm64/chrome-headless-shell',
      ]) {
        const candidate = path.join(root, directory, suffix);
        if (fs.existsSync(candidate)) return candidate;
      }
    }
  }
  return null;
}

function waitFor(predicate, timeout = 20_000) {
  return new Promise((resolve) => {
    const deadline = Date.now() + timeout;
    (function poll() {
      Promise.resolve(predicate()).then((value) => {
        if (value) return resolve(value);
        if (Date.now() > deadline) return resolve(null);
        setTimeout(poll, 150);
      }).catch(() => setTimeout(poll, 150));
    })();
  });
}

async function visibleSelect(page, element) {
  let select = page.locator(`${element} select:visible`).first();
  if (await select.count()) return select;
  await page.locator('button[popovertarget="starlight__sidebar"]:visible').click();
  select = page.locator(`${element} select:visible`).first();
  await select.waitFor();
  return select;
}

(async () => {
  if (!fs.existsSync(path.join(REPO, 'docs-dist', 'index.html'))) {
    throw new Error('docs-dist is missing; run npm run docs:build first');
  }
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'gwasm-docs-e2e-'));
  const server = spawn('python3', [
    path.join(REPO, 'gwasm.py'), '--dir', path.join(temporary, 'dist'),
    '--cache', path.join(temporary, 'cache'), '--port', '0', '--no-browser',
    '--offline', '--no-update',
  ], { cwd: REPO, stdio: ['ignore', 'pipe', 'pipe'] });
  const logs = [];
  server.stdout.on('data', (data) => logs.push(String(data)));
  server.stderr.on('data', (data) => logs.push(String(data)));

  let port;
  const started = await waitFor(() => new Promise((resolve) => {
    const match = logs.join('').match(/at http:\/\/127\.0\.0\.1:(\d+)\//);
    if (!match) return resolve(false);
    port = Number(match[1]);
    http.get({ host: '127.0.0.1', port, path: '/docs/' },
      (response) => resolve(response.statusCode === 200)).on('error', () => resolve(false));
  }));
  check('documentation server starts', !!started, logs.join(''));

  let browser;
  try {
    const executablePath = findChrome();
    check('Chromium is available', !!executablePath,
          'set CHROME_PATH or install the pinned Playwright browser');
    if (!executablePath) throw new Error('Chromium unavailable');
    browser = await chromium.launch({ executablePath, args: ['--no-sandbox'] });

    for (const viewport of [
      { name: 'desktop', width: 1440, height: 900 },
      { name: 'mobile', width: 390, height: 844 },
    ]) {
      const context = await browser.newContext({ viewport });
      const page = await context.newPage();
      const errors = [];
      const requests = [];
      page.on('console', (message) => {
        if (message.type() === 'error') errors.push(message.text());
      });
      page.on('pageerror', (error) => errors.push(error.message));
      page.on('request', (request) => requests.push(request.url()));

      await page.goto(`http://127.0.0.1:${port}/docs/`, { waitUntil: 'networkidle' });
      check(`${viewport.name}: Chinese home renders`,
            await page.locator('main').getByText('认证尚未实现').count() === 1);
      check(`${viewport.name}: hero image loads`, await page.locator('.home-hero__image').evaluate(
        (image) => image.complete && image.naturalWidth > 0));
      check(`${viewport.name}: no horizontal overflow`, await page.evaluate(
        () => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1));
      check(`${viewport.name}: docs heartbeat reaches /ping`,
            !!await waitFor(() => requests.some((url) => url.includes('/ping?id='))));

      await page.goto(`http://127.0.0.1:${port}/docs/architecture/`, { waitUntil: 'networkidle' });
      check(`${viewport.name}: semantic diagram renders`,
            await page.locator('figure.arch-diagram figcaption').count() === 1);
      check(`${viewport.name}: diagram nodes remain visible`,
            await page.locator('.arch-node').count() === 4);
      check(`${viewport.name}: deep page has no overflow`, await page.evaluate(
        () => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1));

      await page.goBack({ waitUntil: 'networkidle' });
      check(`${viewport.name}: browser history restores the home page`,
            await page.locator('main').getByText('认证尚未实现').count() === 1);
      check(`${viewport.name}: restored page adopts a fresh heartbeat ID`,
            !!await waitFor(() => new Set(requests
              .filter((url) => url.includes('/ping?id='))
              .map((url) => new URL(url).searchParams.get('id'))).size >= 3));
      await page.goto(`http://127.0.0.1:${port}/docs/architecture/`,
        { waitUntil: 'networkidle' });

      const language = await visibleSelect(page, 'starlight-lang-select');
      await language.selectOption('/docs/en/architecture/');
      await page.waitForURL('**/docs/en/architecture/');
      check(`${viewport.name}: native language switch`,
            await page.locator('h1').getByText('System overview').count() === 1);

      const theme = await visibleSelect(page, 'starlight-theme-select');
      await theme.selectOption('light');
      check(`${viewport.name}: light theme switch`,
            await page.locator('html').getAttribute('data-theme') === 'light');
      await theme.selectOption('dark');
      check(`${viewport.name}: dark theme switch`,
            await page.locator('html').getAttribute('data-theme') === 'dark');
      await page.keyboard.press('Escape');

      await page.locator('[data-open-modal]').click();
      const search = page.locator('dialog input').first();
      await search.fill('snapshot');
      check(`${viewport.name}: English Pagefind search`, !!await waitFor(async () =>
        (await page.locator('dialog').innerText()).toLowerCase().includes('snapshot')));
      await page.keyboard.press('Escape');
      await page.keyboard.press('Tab');
      check(`${viewport.name}: keyboard focus is visible`, await page.evaluate(() => {
        const active = document.activeElement;
        return active && active !== document.body && getComputedStyle(active).outlineStyle !== 'none';
      }));

      check(`${viewport.name}: no console or CSP errors`, errors.length === 0, errors.join(' | '));
      await context.close();
    }

    const chineseContext = await browser.newContext({ viewport: { width: 1280, height: 800 } });
    const chinese = await chineseContext.newPage();
    await chinese.goto(`http://127.0.0.1:${port}/docs/`, { waitUntil: 'networkidle' });
    await chinese.locator('[data-open-modal]').click();
    await chinese.locator('dialog input').first().fill('快照');
    check('Chinese Pagefind search', !!await waitFor(async () =>
      (await chinese.locator('dialog').innerText()).includes('快照')));
    await chineseContext.close();

    const lifecycleContext = await browser.newContext({ viewport: { width: 1280, height: 800 } });
    const lifecycle = await lifecycleContext.newPage();
    const lifecycleRequests = [];
    lifecycle.on('request', (request) => lifecycleRequests.push(request.url()));
    await lifecycle.goto(`http://127.0.0.1:${port}/`, { waitUntil: 'domcontentloaded' });
    await lifecycle.locator('#docs-link:not([hidden])').waitFor();
    await lifecycle.locator('#docs-link').click();
    await lifecycle.waitForURL('**/docs/');
    await lifecycle.goBack({ waitUntil: 'domcontentloaded' });
    check('game/docs history restores with a fresh heartbeat ID',
          !!await waitFor(() => new Set(lifecycleRequests
            .filter((url) => url.includes('/ping?id='))
            .map((url) => new URL(url).searchParams.get('id'))).size >= 3));
    await lifecycle.waitForTimeout(3_500);
    check('history restoration keeps the server alive',
          (await lifecycle.request.get(`http://127.0.0.1:${port}/ping`)).status() === 204);
    await lifecycleContext.close();

    const wasm = await new Promise((resolve) => http.get({
      host: '127.0.0.1', port, path: '/docs/pagefind/wasm.en.pagefind', method: 'HEAD',
    }, (response) => resolve(response.headers['content-type'])));
    check('Pagefind Wasm MIME', wasm === 'application/wasm', wasm);
  } finally {
    if (browser) await browser.close();
    server.kill('SIGTERM');
    await waitFor(() => server.exitCode !== null, 5000);
    fs.rmSync(temporary, { recursive: true, force: true });
  }

  const failures = results.filter((result) => !result).length;
  console.log(`\n${failures} failure(s)`);
  process.exitCode = failures ? 1 : 0;
})().catch((error) => {
  console.error(error.stack || error);
  process.exitCode = 1;
});
