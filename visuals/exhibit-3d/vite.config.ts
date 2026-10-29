import { defineConfig, type PluginOption } from 'vite';

// `base: './'` keeps every asset path relative, so dist/ works from any subfolder and inside an
// iframe. The dev server answers on localhost only. `npm run build:single` (vite build --mode single)
// inlines every script, style and font into one shareable HTML file.
export default defineConfig(async ({ mode }) => {
  const plugins: PluginOption[] = [];
  if (mode === 'single') {
    const { viteSingleFile } = await import('vite-plugin-singlefile');
    plugins.push(viteSingleFile());
  }
  return {
    base: './',
    plugins,
    server: { host: 'localhost', port: 5173, strictPort: true },
    preview: { host: 'localhost', port: 4173, strictPort: true },
    build: { target: 'es2024', assetsInlineLimit: mode === 'single' ? 100_000_000 : 4096 },
  };
});
