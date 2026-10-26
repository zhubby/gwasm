import { readdir, readFile } from 'node:fs/promises';
import { extname, join, relative, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { docsNavigation } from '../astro.config.mjs';

const root = fileURLToPath(new URL('../docs/src/content/docs/', import.meta.url));

async function walk(directory) {
  const entries = await readdir(directory, { withFileTypes: true });
  const files = [];
  for (const entry of entries) {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) files.push(...await walk(path));
    else if (['.md', '.mdx'].includes(extname(entry.name))) files.push(path);
  }
  return files;
}

const files = await walk(root);
const normalized = files.map((file) => relative(root, file).split(sep).join('/'));
const zh = normalized.filter((file) => !file.startsWith('en/')).sort();
const en = normalized.filter((file) => file.startsWith('en/')).map((file) => file.slice(3)).sort();
const failures = [];

const pageRoute = (file) => {
  const route = file.replace(/\.(?:md|mdx)$/, '');
  if (route === 'index') return '';
  return route.replace(/\/index$/, '');
};
const sidebarRoutes = (groups, prefix = '') => groups.flatMap((group) =>
  group.items.map((item) => item.slug.replace(prefix, '').replace(/\/$/, '')),
);
const zhSidebar = sidebarRoutes(docsNavigation.zh);
const enSidebar = sidebarRoutes(docsNavigation.en, /^en\//);
const contentRoutes = zh.map(pageRoute).filter(Boolean).sort();

if (JSON.stringify(zhSidebar) !== JSON.stringify(enSidebar)) {
  failures.push('Chinese and English sidebar routes or ordering differ');
}
for (const route of contentRoutes.filter((name) => !zhSidebar.includes(name))) {
  failures.push(`page missing from sidebar: ${route}`);
}
for (const route of zhSidebar.filter((name) => !contentRoutes.includes(name))) {
  failures.push(`sidebar route has no page: ${route}`);
}
if (new Set(zhSidebar).size !== zhSidebar.length) {
  failures.push('sidebar contains duplicate routes');
}

for (const file of zh.filter((name) => !en.includes(name))) failures.push(`missing English page: en/${file}`);
for (const file of en.filter((name) => !zh.includes(name))) failures.push(`missing Chinese page: ${file}`);
for (const name of normalized) {
  const source = await readFile(join(root, name), 'utf8');
  const end = source.indexOf('\n---', 4);
  const frontmatter = end > 0 ? source.slice(4, end) : '';
  if (!/^title:\s*.+$/m.test(frontmatter) ||
      !/^description:\s*.+$/m.test(frontmatter)) {
    failures.push(`missing title/description frontmatter: ${name}`);
  }
  const mate = name.startsWith('en/') ? name.slice(3) : `en/${name}`;
  if (!name.startsWith('en/') && normalized.includes(mate)) {
    const mateSource = await readFile(join(root, mate), 'utf8');
    const diagramTypes = (value) => [...value.matchAll(
      /<ArchitectureDiagram\b[^>]*\btype=["']([^"']+)["']/g,
    )].map((match) => match[1]);
    const diagrams = diagramTypes(source);
    const mateDiagrams = diagramTypes(mateSource);
    if (JSON.stringify(diagrams) !== JSON.stringify(mateDiagrams)) {
      failures.push(`diagram mismatch: ${name} (${diagrams.join(', ') || 'none'}) vs ${mate} (${mateDiagrams.join(', ') || 'none'})`);
    }
  }
}

if (failures.length) {
  console.error(failures.join('\n'));
  process.exit(1);
}
console.log(`docs parity: ${zh.length} Chinese pages and ${en.length} English pages`);
