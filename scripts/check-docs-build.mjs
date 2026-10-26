import { access, readdir, readFile } from 'node:fs/promises';
import { dirname, extname, join, resolve } from 'node:path';

const output = resolve('docs-dist');

async function walk(directory) {
  const entries = await readdir(directory, { withFileTypes: true });
  const files = [];
  for (const entry of entries) {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) files.push(...await walk(path));
    else files.push(path);
  }
  return files;
}

function targetFor(url, htmlFile) {
  let clean;
  try {
    clean = decodeURIComponent(url.split(/[?#]/, 1)[0]);
  } catch {
    throw new Error(`malformed URL encoding: ${url}`);
  }
  if (!clean || clean.startsWith('#') || /^(?:https?:|mailto:|data:|javascript:)/.test(clean)) return null;
  if (clean.startsWith('/docs/')) {
    const local = clean.slice('/docs/'.length);
    return clean.endsWith('/') ? join(output, local, 'index.html') : join(output, local);
  }
  if (clean === '/docs') return join(output, 'index.html');
  if (clean.startsWith('/')) throw new Error(`absolute URL escapes /docs: ${url}`);
  const target = resolve(dirname(htmlFile), clean);
  if (clean.endsWith('/')) return join(target, 'index.html');
  return extname(target) ? target : `${target}.html`;
}

function cssTargetFor(url, cssFile) {
  if (/^data:/i.test(url)) return null;
  if (/^(?:https?:|\/\/)/i.test(url)) {
    throw new Error(`external CSS resource is not allowed: ${url}`);
  }
  return targetFor(url, cssFile);
}

const files = await walk(output);
const htmlFiles = files.filter((file) => file.endsWith('.html'));
const failures = [];
for (const file of htmlFiles) {
  const html = await readFile(file, 'utf8');
  for (const match of html.matchAll(
    /<(?:script|img|source|iframe|audio|video|link)\b[^>]*?\b(?:src|href)=["']([^"']+)["']/gi,
  )) {
    if (/^(?:https?:)?\/\//i.test(match[1])) {
      failures.push(`${file}: external document resource is not allowed: ${match[1]}`);
    }
  }
  for (const match of html.matchAll(/(?:href|src)=["']([^"']+)["']/g)) {
    const url = match[1];
    let target;
    try { target = targetFor(url, file); }
    catch (error) {
      failures.push(`${file}: ${error.message}`);
      continue;
    }
    if (!target) continue;
    try { await access(target); }
    catch { failures.push(`${file}: broken internal URL ${url}`); }
  }
}
for (const file of files.filter((candidate) => candidate.endsWith('.css'))) {
  const css = await readFile(file, 'utf8');
  for (const match of css.matchAll(/url\(\s*(?:"([^"]*)"|'([^']*)'|([^)]*))\s*\)/g)) {
    const url = (match[1] ?? match[2] ?? match[3]).trim();
    let target;
    try { target = cssTargetFor(url, file); }
    catch (error) {
      failures.push(`${file}: ${error.message}`);
      continue;
    }
    if (!target) continue;
    try { await access(target); }
    catch { failures.push(`${file}: broken CSS URL ${url}`); }
  }
}
for (const required of ['index.html', 'en/index.html', 'favicon.svg',
                        'pagefind/wasm.en.pagefind']) {
  try { await access(join(output, required)); }
  catch { failures.push(`missing build artifact: ${required}`); }
}
if (failures.length) {
  console.error(failures.join('\n'));
  process.exit(1);
}
console.log(`docs links: ${htmlFiles.length} HTML pages checked`);
