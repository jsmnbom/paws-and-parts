import type { Plugin } from 'vite'
import { execSync } from 'node:child_process'
import { copyFileSync, existsSync, readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { resolve as resolvePath } from 'node:path'
import process from 'node:process'
import { fileURLToPath } from 'node:url'
import { NuxtIconBundle } from '@nuxt/icon/vite'
import ui from '@nuxt/ui/vite'
import vue from '@vitejs/plugin-vue'
import webpackStatsPlugin from 'rollup-plugin-webpack-stats'
import { defineConfig } from 'vite'
import vueDevTools from 'vite-plugin-vue-devtools'

// GitHub Pages has no server-side rewrite, so a deep-link refresh (e.g.
// /reponame/m/chain) would 404. The convention is to serve a copy of index.html
// as 404.html: Pages returns it for any unmatched path, the SPA boots, and
// vue-router resolves the real route client-side. Vite injects base-prefixed
// absolute asset URLs into index.html, so the copy loads assets correctly under
// the project's base. Runs after the bundle is written.
function spaFallback(): Plugin {
  return {
    name: 'spa-404-fallback',
    apply: 'build',
    closeBundle() {
      const dist = fileURLToPath(new URL('./dist', import.meta.url))
      const index = resolvePath(dist, 'index.html')
      if (existsSync(index))
        copyFileSync(index, resolvePath(dist, '404.html'))
    },
  }
}

// three-cad-viewer bakes its toolbar/tree icons into CSS custom properties as
// inline SVG data URIs (`--tcv-icon-iso: url("data:image/svg+xml,…")`), each with
// tcv's stock blue hardcoded inside it — so the --tcv-* overrides in Viewer.vue
// reskin every bit of the viewer chrome *except* the glyphs you actually look at.
// The colors used across all 37 icons are a closed set, so we remap them here as
// the stylesheet is imported: decode each data URI, swap the palette, re-encode.
// Values are rewritten in place, so tcv's own selectors and cascade survive and
// Viewer.vue needs no icon rules of its own.
//
// Unlike Viewer.vue's rules, these can't be written as var(--ui-color-…): a data
// URI is opaque to the CSS engine, so a var() inside one is never substituted and
// just renders as an invalid color. The token therefore has to be resolved to a
// literal at build time — read out of Tailwind's own theme, so --ui-color-* stays
// the single source rather than a hex pasted in here.
const UI_COLORS = { primary: 'pink', neutral: 'zinc' } as const // = ui({ colors })

const tailwindTheme = readFileSync(
  createRequire(import.meta.url).resolve('tailwindcss/theme.css'),
  'utf8',
)

function uiColor(role: keyof typeof UI_COLORS, shade: number): string {
  const name = `--color-${UI_COLORS[role]}-${shade}`
  const value = new RegExp(`${name}:\\s*([^;]+)`).exec(tailwindTheme)?.[1]
  if (!value)
    throw new Error(`tcvIcons: ${name} not found in tailwindcss/theme.css`)
  return value.trim()
}

const TCV_ICON_COLORS = {
  /** Filled faces, and strokes that *are* the drawing. Matches --tcv-theme-blue. */
  accent: uiColor('primary', 500),
  /** Hairline separating one filled face from the next. */
  outline: uiColor('neutral', 500),
  /** Hidden / empty tree glyphs, which have to read as "off". */
  off: uiColor('neutral', 50),
}

// tcv's own colors, by the role they play inside the glyphs.
const TCV_ACCENT = 'rgb(83,160,227)'
const TCV_INK = ['#444', '#333']
const TCV_OFF = '#ddd'

function encodeSvg(svg: string): string {
  return encodeURIComponent(svg).replace(/[()]/g, c => (c === '(' ? '%28' : '%29'))
}

function recolorIcon(svg: string): string {
  return svg.replace(/<(path|rect|ellipse|text|circle)\b([^>]*)>/g, (_, tag: string, attrs: string) => {
    const mapped = attrs
      .replace(/fill="([^"]*)"/g, (m, v: string) => {
        // Ink as a *fill* is a solid glyph rather than an outline — the tree's
        // collapse chevrons are a filled triangle and nothing else — so it takes
        // the accent, same as an ink stroke on a fill="none" element.
        if (v === TCV_ACCENT || TCV_INK.includes(v))
          return `fill="${TCV_ICON_COLORS.accent}"`
        if (v === TCV_OFF)
          return `fill="${TCV_ICON_COLORS.off}"`
        return m
      })
      .replace(/stroke="([^"]*)"/g, (m, v: string) => {
        if (TCV_INK.includes(v))
          return `stroke="${TCV_ICON_COLORS.outline}"`
        if (v === TCV_ACCENT)
          return `stroke="${TCV_ICON_COLORS.accent}"`
        if (v === TCV_OFF)
          return `stroke="${TCV_ICON_COLORS.off}"`
        return m
      })
    return `<${tag}${mapped}>`
  })
}

// Matches one `--tcv-icon-*: url("data:image/svg+xml,<payload>")` declaration.
// The payload is percent-encoded (tcv writes `#` as %23 and `,` as %2C), so it's
// decoded before substitution rather than matched raw. The `\s*` before the colon
// is load-bearing: upstream writes `--tcv-icon-nav_closed  :` with stray padding.
const TCV_ICON_DECL = /(--tcv-icon-[\w-]+\s*:\s*url\("data:image\/svg\+xml,)([^"]*)(")/g

// tcv ships both a light and a dark icon set, and their source palettes collide —
// `#ddd` is light's hidden-glyph grey but dark's outline stroke — so a single flat
// map can't serve both. main.ts pins the app to light mode, so only that block is
// remapped; the dark set is left as upstream ships it.
const TCV_LIGHT_BLOCK = /\[data-theme="light"\]\s*\{[\s\S]*?\n\}/

// tcv's stylesheet also styles the whole page — `body { user-select: none;
// overflow: hidden; background-color: … }`, written for a page that is nothing but
// the viewer. Once a generator has loaded it, it stays for the rest of the session,
// so the gallery could no longer scroll (and no text anywhere could be selected).
// The viewer's own chrome has its own user-select rules, so the page-level rule is
// dropped here.
const TCV_BODY_RULE = /^body\s*\{[^}]*\}/m

function tcvIcons(): Plugin {
  return {
    name: 'tcv-icons',
    // Run ahead of vite:css so the stylesheet is still raw text.
    enforce: 'pre',
    transform: {
      // Declarative filters are evaluated natively by Rolldown, so the handler
      // below is never called for any other module.
      filter: {
        id: /three-cad-viewer[\\/]dist[\\/]three-cad-viewer\.css/,
        code: '--tcv-icon-',
      },
      handler(code) {
        let recolored = 0
        const themed = code.replace(TCV_LIGHT_BLOCK, block =>
          block.replace(TCV_ICON_DECL, (_, head: string, payload: string, tail: string) => {
            recolored++
            return head + encodeSvg(recolorIcon(decodeURIComponent(payload))) + tail
          }))

        const unscoped = themed.replace(TCV_BODY_RULE, '')
        if (unscoped === themed) {
          this.warn(
            'three-cad-viewer\'s CSS no longer has the expected page-level `body` rule; '
            + 'check it does not lock page scrolling — see TCV_BODY_RULE in vite.config.ts.',
          )
        }

        // A tcv upgrade that restructures the theme block would otherwise revert
        // the icons to stock blue with no other symptom.
        if (recolored === 0) {
          this.warn(
            'No --tcv-icon-* declarations were recolored; three-cad-viewer\'s CSS '
            + 'no longer matches the expected shape. Viewer icons will render in '
            + 'tcv\'s stock blue — see tcvIcons() in vite.config.ts.',
          )
        }

        // The icon swaps stay within their lines and the body rule is a few
        // dropped lines, so there is nothing meaningful to map.
        return { code: unscoped, map: null }
      },
    },
  }
}

// Short git hash of the build, surfaced in the UI (links back to the commit on
// GitHub). CI passes it via GIT_HASH (from the workflow); locally we fall back to
// asking git, then to 'dev' outside a git checkout.
function gitHash(): string {
  if (process.env.GIT_HASH)
    return process.env.GIT_HASH
  try {
    return execSync('git rev-parse HEAD').toString().trim()
  }
  catch {
    return 'dev'
  }
}

// Plain Vue 3 + Vite. Fully client-side (no SSR). Pyodide runs in a web worker.
export default defineConfig({
  base: process.env.BASE_URL ?? '/',

  define: {
    __GIT_HASH__: JSON.stringify(gitHash()),
  },

  build: {
    sourcemap: true,
    rollupOptions: {
      output: {
        // Use a supported file pattern for Vite 5/Rollup 4
        // @doc https://relative-ci.com/documentation/guides/vite-config
        assetFileNames: 'assets/[name].[hash][extname]',
        chunkFileNames: 'assets/[name].[hash].js',
        entryFileNames: 'assets/[name].[hash].js',
      },
    },
  },

  plugins: [
    spaFallback(),
    tcvIcons(),
    vueDevTools(),
    vue(),
    ui({
      ui: {
        colors: UI_COLORS,
        // Help text is paragraphs, not a label: Nuxt UI's stock tooltip is one
        // truncated line (`h-6`, `truncate`). Let it wrap, keep the paragraph
        // breaks proto.py leaves in descriptions, and allow selecting the text.
        // `content` merges with the stock classes; `truncate` in `text` isn't a
        // class tailwind-merge knows to drop for `whitespace-*`, so those win with `!`.
        tooltip: {
          slots: {
            content: 'h-auto max-w-xs items-start py-1.5 select-text',
            text: 'whitespace-pre-line! text-wrap overflow-visible',
          },
        },
      },
    }),
    NuxtIconBundle({
      scan: {
        globInclude: [
          'src/**/*.{vue,jsx,tsx,ts,md,mdc,mdx}',
          'node_modules/@nuxt/ui/dist/**/*.{mjs,js}',
        ],
        globExclude: ['**/*.d.ts', '**/*.d.mts'],
      },
    }),
    webpackStatsPlugin(),
  ],
  worker: {
    format: 'es',
  },
  server: {
    // The uv-managed Python dev env lives in ./.venv; don't let its thousands of
    // files churn the dev-server file watcher.
    watch: { ignored: ['**/.venv/**'] },
  },
  // Pyodide + OCP.wasm fetch large assets from a CDN at runtime; nothing to
  // pre-bundle for them here.
})
