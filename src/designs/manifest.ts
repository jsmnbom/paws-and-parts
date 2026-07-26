// The design manifest — the other half of the engine-extraction seam. The engine
// depends only on this interface (slug + source + display metadata), never on a
// specific design's internals. Paws & Parts is just this `src/designs/` set.
//
// Each `src/designs/<slug>/design.py` is bundled as raw source (sent to the
// worker via `reload-design`) and its display metadata is scraped from the
// module docstring (first line = name, rest = description) plus the `__author__`
// / `__urls__` / `__blank__` dunders — co-located with the design so they can't
// drift, and cheap to read without booting Python.

/** A per-design outbound footer link. `icon` is resolved here (see ICON_BY_HOST). */
export interface DesignLink {
  label: string
  url: string
  icon: string
}

export interface DesignMeta {
  /** URL slug and directory name, e.g. "chain" → /m/chain. */
  slug: string
  /** Display name (the docstring's first line, falls back to the slug). */
  name: string
  author?: string
  description?: string
  /** The verbatim `design.py` source, handed to the worker to build. */
  source: string
  /** Author-provided thumbnail URL, if a `thumb.png` sits in the design dir. */
  thumb?: string
  /** The "start from scratch" starter — rendered distinctly, opens on the Code tab. */
  blank: boolean
  /** Per-design footer links (from `__urls__`); empty if none declared. */
  links: DesignLink[]
}

// Eager raw-source glob: `../designs/<slug>/design.py` → source string.
const sources = import.meta.glob('./*/design.py', {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>

// Optional per-design thumbnail → resolved asset URL.
const thumbs = import.meta.glob('./*/thumb.png', {
  query: '?url',
  import: 'default',
  eager: true,
}) as Record<string, string>

/** Slug (directory name) from a glob key like `./chain/design.py`. */
function slugOf(path: string): string {
  return path.split('/')[1]
}

/** Read a module-level string constant (`__author__ = "…"`). */
function pyString(source: string, name: string): string | undefined {
  const m = source.match(new RegExp(`^${name}\\s*=\\s*(['"])([\\s\\S]*?)\\1`, 'm'))
  return m?.[2]
}

/** Read a module-level truthy boolean flag (`__blank__ = True`). */
function pyFlag(source: string, name: string): boolean {
  return new RegExp(`^${name}\\s*=\\s*True\\b`, 'm').test(source)
}

/**
 * The module docstring, split into the design's name (its first line) and
 * description (everything after, with the blank separator line collapsed) — the
 * same convention `proto.py`'s `Choice` uses for its members. A module docstring
 * is by definition the first statement, so it's anchored to the file start.
 */
function pyDocstring(source: string): { name?: string, description?: string } {
  const m = source.match(/^\s*(?:[a-zA-Z]*)("""|''')([\s\S]*?)\1/)
  if (!m)
    return {}
  const [first, ...rest] = m[2].trim().split('\n')
  const description = rest.join('\n').trim()
  return { name: first.trim() || undefined, description: description || undefined }
}

// Host → brand icon. Written as literal icon names so the build-time
// NuxtIconBundle scanner (see vite.config.ts) actually bundles them — there is no
// runtime Iconify fetch, so an icon name that never appears as a literal here
// would render blank. The `simple-icons` collection's prefix contains a hyphen,
// so it MUST use the `prefix:name` colon form: Nuxt UI's Icon strips a leading
// `i-` and hands the rest to Iconify, whose dash parser would otherwise split
// `simple-icons-printables` into prefix `simple` / name `icons-printables` and
// render blank. Colon-form parses unambiguously. Unknown hosts fall back to a
// generic lucide link icon (single-word prefix, dash-form is fine). All slugs
// verified to exist in @iconify-json/simple-icons.
const ICON_BY_HOST: Record<string, string> = {
  'printables.com': 'simple-icons:printables',
  'thingiverse.com': 'simple-icons:thingiverse',
  'etsy.com': 'simple-icons:etsy',
  'youtube.com': 'simple-icons:youtube',
  'github.com': 'simple-icons:github',
  'patreon.com': 'simple-icons:patreon',
  'gumroad.com': 'simple-icons:gumroad',
  'ko-fi.com': 'simple-icons:kofi',
  'instagram.com': 'simple-icons:instagram',
  'discord.com': 'simple-icons:discord',
  'discord.gg': 'simple-icons:discord',
}
const FALLBACK_ICON = 'i-lucide-external-link'

/** Pick a brand icon for a link URL by its host (leading `www.` stripped). */
function iconForUrl(url: string): string {
  let host: string
  try {
    host = new URL(url).hostname.replace(/^www\./, '')
  }
  catch {
    return FALLBACK_ICON
  }
  // Suffix match so `shop.printables.com` still resolves to printables.
  for (const [domain, icon] of Object.entries(ICON_BY_HOST)) {
    if (host === domain || host.endsWith(`.${domain}`))
      return icon
  }
  return FALLBACK_ICON
}

/**
 * Scrape the module-level `__urls__ = {"Printables": "https://…", …}` dict into
 * footer links. Values are flat strings (no nesting), so a non-greedy match to
 * the first `}` closes it; per-link icons are resolved from the URL host.
 */
function pyLinks(source: string): DesignLink[] {
  const block = source.match(/^__urls__\s*=\s*\{([\s\S]*?)\}/m)
  if (!block)
    return []
  const links: DesignLink[] = []
  for (const kv of block[1].matchAll(/(['"])(.*?)\1\s*:\s*(['"])(.*?)\3/g))
    links.push({ label: kv[2], url: kv[4], icon: iconForUrl(kv[4]) })
  return links
}

function toMeta(path: string, source: string): DesignMeta {
  const slug = slugOf(path)
  const { name, description } = pyDocstring(source)
  return {
    slug,
    name: name ?? slug,
    author: pyString(source, '__author__'),
    description,
    source,
    thumb: thumbs[`./${slug}/thumb.png`],
    blank: pyFlag(source, '__blank__'),
    links: pyLinks(source),
  }
}

/** All designs, real ones first and any blank starter(s) last (gallery order). */
export const designs: DesignMeta[] = Object.entries(sources)
  .map(([path, source]) => toMeta(path, source))
  .sort((a, b) => Number(a.blank) - Number(b.blank) || a.slug.localeCompare(b.slug))

const bySlug = new Map(designs.map(d => [d.slug, d]))

export function getDesign(slug: string): DesignMeta | undefined {
  return bySlug.get(slug)
}
