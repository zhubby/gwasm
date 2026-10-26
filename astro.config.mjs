import { defineConfig } from 'astro/config';
import starlight from '@astrojs/starlight';

const zh = [
  { label: '快速开始', items: [
    { label: '项目状态与限制', slug: 'getting-started/' },
    { label: '安装与首次启动', slug: 'getting-started/install/' },
    { label: '配置与运行模式', slug: 'getting-started/configuration/' },
  ] },
  { label: '系统架构', items: [
    { label: '系统全景', slug: 'architecture/' },
    { label: '启动与更新', slug: 'architecture/startup/' },
    { label: 'Manifest 与 Chunk 管线', slug: 'architecture/patch-pipeline/' },
    { label: '快照与双层缓存', slug: 'architecture/snapshot-cache/' },
    { label: '浏览器宿主契约', slug: 'architecture/host-runtime/' },
    { label: 'Relay、网络与安全', slug: 'architecture/relay-security/' },
    { label: 'Wasm 分析工具链', slug: 'architecture/wasm-tooling/' },
  ] },
  { label: '部署运维', items: [
    { label: '拓扑与前置条件', slug: 'deployment/' },
    { label: 'Docker Compose', slug: 'deployment/docker/' },
    { label: '本机 Python 与源码构建', slug: 'deployment/python-source/' },
    { label: '数据、备份与回滚', slug: 'deployment/data-lifecycle/' },
    { label: '运行、安全与就绪', slug: 'deployment/operations/' },
    { label: '故障排查与验收', slug: 'deployment/troubleshooting/' },
  ] },
  { label: '贡献开发', items: [
    { label: '仓库结构', slug: 'contributing/' },
    { label: '测试、CI 与发布', slug: 'contributing/testing-release/' },
    { label: '文档维护', slug: 'contributing/docs/' },
  ] },
  { label: '参考资料', items: [
    { label: 'CLI 与环境变量', slug: 'reference/cli-env/' },
    { label: 'HTTP 与宿主契约', slug: 'reference/http-host-contract/' },
    { label: '制品与法律声明', slug: 'reference/artifacts-legal/' },
  ] },
];

const en = [
  { label: 'Getting started', items: [
    { label: 'Status and limitations', slug: 'en/getting-started/' },
    { label: 'Install and first run', slug: 'en/getting-started/install/' },
    { label: 'Configuration and modes', slug: 'en/getting-started/configuration/' },
  ] },
  { label: 'Architecture', items: [
    { label: 'System overview', slug: 'en/architecture/' },
    { label: 'Startup and update', slug: 'en/architecture/startup/' },
    { label: 'Manifest and chunk pipeline', slug: 'en/architecture/patch-pipeline/' },
    { label: 'Snapshot and two-tier cache', slug: 'en/architecture/snapshot-cache/' },
    { label: 'Browser host contract', slug: 'en/architecture/host-runtime/' },
    { label: 'Relay, network, and security', slug: 'en/architecture/relay-security/' },
    { label: 'Wasm analysis toolchain', slug: 'en/architecture/wasm-tooling/' },
  ] },
  { label: 'Deployment and operations', items: [
    { label: 'Topology and prerequisites', slug: 'en/deployment/' },
    { label: 'Docker Compose', slug: 'en/deployment/docker/' },
    { label: 'Bare Python and source builds', slug: 'en/deployment/python-source/' },
    { label: 'Data, backup, and rollback', slug: 'en/deployment/data-lifecycle/' },
    { label: 'Operations, security, readiness', slug: 'en/deployment/operations/' },
    { label: 'Troubleshooting and acceptance', slug: 'en/deployment/troubleshooting/' },
  ] },
  { label: 'Contributing', items: [
    { label: 'Repository layout', slug: 'en/contributing/' },
    { label: 'Tests, CI, and releases', slug: 'en/contributing/testing-release/' },
    { label: 'Documentation', slug: 'en/contributing/docs/' },
  ] },
  { label: 'Reference', items: [
    { label: 'CLI and environment', slug: 'en/reference/cli-env/' },
    { label: 'HTTP and host contract', slug: 'en/reference/http-host-contract/' },
    { label: 'Artifacts and legal', slug: 'en/reference/artifacts-legal/' },
  ] },
];

export const docsNavigation = { zh, en };

const sidebar = zh.map((group, groupIndex) => ({
  label: group.label,
  translations: { en: en[groupIndex].label },
  items: group.items.map((item, itemIndex) => ({
    label: item.label,
    translations: { en: en[groupIndex].items[itemIndex].label },
    slug: item.slug.replace(/\/$/, ''),
  })),
}));

export default defineConfig({
  base: '/docs',
  output: 'static',
  outDir: './docs-dist',
  publicDir: './docs/public',
  srcDir: './docs/src',
  trailingSlash: 'always',
  integrations: [
    starlight({
      title: 'gwasm',
      description: 'Guild Wars browser interoperability research and runtime documentation',
      defaultLocale: 'root',
      locales: {
        root: { label: '简体中文', lang: 'zh-CN' },
        en: { label: 'English', lang: 'en' },
      },
      sidebar,
      social: [{ icon: 'github', label: 'GitHub', href: 'https://github.com/zhubby/gwasm' }],
      customCss: ['./docs/src/styles/custom.css'],
      head: [
        { tag: 'meta', attrs: { name: 'theme-color', content: '#f6f4ef' } },
        { tag: 'script', attrs: { src: '/docs/docs-heartbeat.js', defer: true } },
      ],
      lastUpdated: true,
      pagination: true,
      editLink: { baseUrl: 'https://github.com/zhubby/gwasm/edit/main/' },
    }),
  ],
});
