<script setup lang="ts">
import type { DesignWorker } from '../composables/useDesignWorker'
import type { JsonSchema, Preset } from '../lib/protocol'
import { onKeyStroke, useDebounceFn, useLocalStorage, useMediaQuery } from '@vueuse/core'
import { computed, defineAsyncComponent, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { getDesign } from '../designs/manifest'
import { decodeParams, encodeParams, exportFilename } from '../lib/paramsUrl'
import GeneratorHeader from './GeneratorHeader.vue'
import ParamForm from './ParamForm.vue'
import PrintabilityPanel from './PrintabilityPanel.vue'

// The generator body for one design: the parameters form on the left + the 3D
// viewer. The design's source is a read-only layer that slides over the form on
// demand. The engine (worker) is already booted; this component is keyed by slug
// so each design gets a clean slate. It copes with a not-yet-loaded schema (the
// design's source is exec'd asynchronously).
const props = defineProps<{
  worker: DesignWorker
  slug: string
  designName: string
}>()

// Heavy, lazily-loaded panes — kept out of the main bundle.
const Viewer = defineAsyncComponent(() => import('./Viewer.vue'))
const CodeEditor = defineAsyncComponent(() => import('./CodeEditor.vue'))

const {
  schema,
  presets,
  presetLabel,
  shapes,
  buildStage,
  buildPending,
  fieldErrors,
  report,
  reloadError,
  reloading,
  build,
  exportModel,
} = props.worker

const route = useRoute()
const router = useRouter()

const params = ref<Record<string, number | string>>({})
const exporting = ref<string | null>(null)

// Below `lg` the two panes stack (viewer over form) instead of sitting side by side,
// which is also where Nuxt UI's own dashboard theme switches — its panels are
// `w-full lg:w-(--width)` and its drag handle `hidden lg:block`. Matching that
// breakpoint means we only have to correct the flex direction, not fight the library.
// This is a viewport question, deliberately separate from the device question
// useDataConsent asks: a half-width desktop window belongs in the stacked layout too.
const isDesktop = useMediaQuery('(min-width: 1024px)')

// Handle on the (async) Viewer, so the Screenshot button can grab a canvas PNG
// and big parameter changes can ask for the default framing back.
const viewerRef = ref<{
  captureScreenshot: () => Promise<string | null>
  resetView: () => void
} | null>(null)

// The source view is a layer over the form, not a mode you switch into. Monaco is
// mounted on first open and then kept alive (hidden with v-show), so reopening is
// instant and the form underneath never loses its scroll position.
const showCode = ref(false)
const codeMounted = ref(false)
function openCode() {
  codeMounted.value = true
  showCode.value = true
}
onKeyStroke('Escape', () => {
  if (showCode.value)
    showCode.value = false
})

// The design's source, from the bundled manifest. Shown read-only for now — live
// editing hot-swapped the design on every keystroke, which the error surface isn't
// set up to handle.
const source = getDesign(props.slug)?.source ?? ''

// Per-design footer links: the design's own `__urls__` (Printables, shop, …) plus
// the manifest's auto-generated link to this `design.py` at the build's commit,
// which is also what the source view's ↗ button opens.
const designLinks = getDesign(props.slug)?.links ?? []
const sourceUrl = getDesign(props.slug)?.sourceUrl

// Split-button: the primary action remembers the last format the user picked.
const exportFormat = useLocalStorage<'3MF' | 'STEP'>('lab-export-format', '3MF')
const exportItems = computed(() =>
  [[
    {
      label: '3MF',
      description: 'Works with most 3D programs and slicers. Recommended.',
      icon: 'i-lucide-download',
      onSelect: () => downloadExport('3MF'),
    },
    {
      label: 'STEP',
      description: 'CAD interchange format.',
      icon: 'i-lucide-download',
      onSelect: () => downloadExport('STEP'),
    },
  ]],
)

function defaultsFor(s: JsonSchema): Record<string, number | string> {
  const defaults: Record<string, number | string> = {}
  for (const [name, prop] of Object.entries(s.properties)) {
    defaults[name] = (prop.default as number | string) ?? prop.sliderMin ?? prop.minimum ?? 0
  }
  return defaults
}

// --- Presets: one curated set of values for the fields a design says it owns. -//
//
// The design marks the parameters that are the *user's* (`preset_stable`: the
// chain's link count, layout, center skip); a preset covers everything else and
// is applied by replacement, so picking one always yields exactly the thing its
// name promises without disturbing the chain you asked for.

// The preset the current params came from, if any. Not just a click record: it's
// re-derived when the form seeds, so a shared URL lights the right chip.
const activePreset = ref<string | null>(null)

// The fields a preset owns — the complement of the stable ones.
const presetFields = computed(() =>
  Object.entries(schema.value?.properties ?? {})
    .filter(([, p]) => !p.preset_stable)
    .map(([name]) => name),
)

/** What the form would hold if `p` were applied: defaults with the preset over them. */
function presetTarget(p: Preset): Record<string, number | string> {
  return { ...defaultsFor(schema.value!), ...p.params }
}

function matchesPreset(p: Preset): boolean {
  const target = presetTarget(p)
  return presetFields.value.every(name => params.value[name] === target[name])
}

/** The preset (if any) the current params are an exact match for. */
function detectPreset(): string | null {
  if (!schema.value)
    return null
  return presets.value.find(matchesPreset)?.name ?? null
}

function applyPreset(p: Preset) {
  if (!schema.value)
    return
  const next = presetTarget(p)
  // Hand the user's own parameters back, untouched.
  for (const [name, prop] of Object.entries(schema.value.properties)) {
    if (prop.preset_stable && params.value[name] !== undefined)
      next[name] = params.value[name]
  }
  params.value = next
  activePreset.value = p.name
  // A preset rewrites everything the user didn't pin, so the view it was framed
  // for is gone — start the new one centred.
  viewerRef.value?.resetView()
}

// Tweaking a preset's field doesn't clear the selection, it marks it — the chip
// stays lit as "where this came from" and offers a way back.
const presetEdited = computed(() => {
  const p = presets.value.find(x => x.name === activePreset.value)
  return !!p && !matchesPreset(p)
})

const activeDescription = computed(() =>
  presets.value.find(p => p.name === activePreset.value)?.description,
)

// Titles the rule ParamForm draws above the fields presets write. Falls away
// entirely for a design that defines no presets.
const presetGroupLabel = computed(() => {
  if (!presets.value.length)
    return undefined
  if (!activePreset.value)
    return presetLabel.value
  return `${presetLabel.value} · ${activePreset.value}${presetEdited.value ? ', edited' : ''}`
})

// Seed the form whenever the schema arrives or changes. On the first load, URL
// query params layer over the defaults so a shared link boots into its exact
// configuration. On an in-place schema change (a Code-tab edit that alters
// Parameters), current values are *preserved* for fields that still exist — a
// keystroke must not reset the form — with defaults filling any newly-added
// field. Seeding kicks off a build via the params watcher below.
watch(schema, (s) => {
  if (!s)
    return
  const defaults = defaultsFor(s)
  const urlParams = decodeParams(route.query, s)
  const kept: Record<string, number | string> = {}
  for (const name of Object.keys(s.properties)) {
    if (params.value[name] !== undefined)
      kept[name] = params.value[name]
  }
  params.value = { ...defaults, ...urlParams, ...kept }
  // Light whichever preset these values are, so a shared link (or an untouched
  // form) shows what it's built from rather than a blank strip.
  activePreset.value = detectPreset()
}, { immediate: true })

// Mirror the current params into the URL query (debounced), carrying only values
// that differ from the defaults so shared links stay short. The path (the design
// slug) is owned by the router; we only rewrite the query.
const writeUrl = useDebounceFn((p: Record<string, number | string>) => {
  if (!schema.value)
    return
  const query = encodeParams(p, schema.value)
  router.replace({ name: 'generator', params: { slug: props.slug }, query })
}, 200)

// Every param change (initial seed included) requests a debounced, latest-wins
// build and syncs the URL.
watch(
  params,
  (p) => {
    if (Object.keys(p).length) {
      build(p)
      writeUrl(p)
    }
  },
  { deep: true },
)

// A few parameters rearrange the whole design rather than tweak it — the design
// flags them `recenter` (for the chain, its layout). The viewer otherwise holds
// the camera across rebuilds, which is right for nudging a dimension and wrong
// here, so reframe on the result. Keyed on the flagged fields' values only, so
// every other slider still keeps the view.
const recenterKey = computed(() =>
  Object.entries(schema.value?.properties ?? {})
    .filter(([, prop]) => prop.recenter)
    .map(([name]) => `${name}=${params.value[name]}`)
    .join('|'),
)
watch(recenterKey, () => viewerRef.value?.resetView())

// Save a screenshot of the current viewer render as a PNG (named after the design
// + params, like exports). Doubles as the source for a gallery thumbnail: rename it
// to `thumb.png` in `src/designs/<slug>/`. Silently no-ops if nothing is rendered.
async function saveScreenshot() {
  const dataUrl = await viewerRef.value?.captureScreenshot()
  if (!dataUrl)
    return
  const base = schema.value ? exportFilename(params.value, schema.value, props.slug) : props.slug
  const a = document.createElement('a')
  a.href = dataUrl
  a.download = `${base}.png`
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
}

async function downloadExport(format: 'STEP' | '3MF') {
  if (exporting.value)
    return
  exportFormat.value = format
  exporting.value = format
  try {
    const bytes = await exportModel(format, params.value)
    const blob = new Blob([bytes as BlobPart], { type: 'application/octet-stream' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    const base = schema.value ? exportFilename(params.value, schema.value, props.slug) : props.slug
    a.download = `${base}.${format.toLowerCase()}`
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
  }
  catch (err) {
    console.error('Export failed', err)
  }
  finally {
    exporting.value = null
  }
}
</script>

<template>
  <!-- Wide: two resizable panes (drag the handle between them), sizes in rem and
       persisted to localStorage. Narrow: the same two panes stacked, viewer on top —
       `flex-col-reverse` gets it above the controls without reordering the DOM, so tab
       order stays header → form → export → viewer. The group's own base class is
       `fixed inset-0`, overridden here because it now sits inside the column that
       carries the mobile header. -->
  <div class="fixed inset-0 flex flex-col overflow-hidden">
    <GeneratorHeader
      v-if="!isDesktop"
      class="shrink-0"
      compact
      :design-name="designName"
      :has-shapes="!!shapes"
      @screenshot="saveScreenshot"
      @open-code="openCode"
    />

    <UDashboardGroup
      storage="local"
      storage-key="lab-layout"
      unit="rem"
      class="relative min-h-0 flex-1 max-lg:flex-col-reverse"
    >
      <!-- Left: tabbed form / code + export -->
      <UDashboardPanel
        id="controls"
        resizable
        :default-size="24"
        :min-size="18"
        :max-size="44"
        class="bg-white max-lg:min-h-0 max-lg:flex-1"
      >
        <GeneratorHeader
          v-if="isDesktop"
          :design-name="designName"
          :has-shapes="!!shapes"
          @screenshot="saveScreenshot"
          @open-code="openCode"
        />

        <!-- Pane body: the form, with the source view layered over it on demand. -->
        <div class="relative flex min-h-0 grow flex-col">
          <!-- The footer (export + project links) is pinned at the bottom of the
               form, so it's specific to a design's parameters. -->
          <div class="flex min-h-0 grow flex-col">
            <div class="min-h-0 grow overflow-y-auto px-5 py-5">
              <UAlert
                v-if="reloadError"
                color="error"
                variant="subtle"
                icon="i-lucide-circle-alert"
                class="mb-5"
                title="Design failed to load"
              >
                <template #description>
                  <pre class="mt-1 max-h-40 overflow-auto whitespace-pre-wrap text-xs">{{ reloadError }}</pre>
                </template>
              </UAlert>
              <div v-if="!schema" class="flex items-center gap-2 text-sm text-neutral-400">
                <UIcon name="i-lucide-loader-circle" class="size-4 animate-spin" />
                Loading design…
              </div>
              <template v-else>
                <!-- Preset strip: the design's curated starting points, bled to the
                     pane edges so it reads as a header for the form below it. -->
                <div
                  v-if="presets.length"
                  class="-mx-5 mb-5 border-b border-neutral-200 bg-neutral-50 px-5 py-3"
                  :class="reloadError ? '' : '-mt-5'"
                >
                  <div class="flex items-baseline justify-between gap-2">
                    <span class="text-[10px] font-medium uppercase tracking-wider text-neutral-500">
                      {{ presetLabel }} preset
                    </span>
                    <UButton
                      v-if="presetEdited && activePreset"
                      size="xs"
                      variant="link"
                      color="primary"
                      class="p-0"
                      :label="`Reset to ${activePreset}`"
                      @click="applyPreset(presets.find((p) => p.name === activePreset)!)"
                    />
                  </div>
                  <div class="mt-2 flex flex-wrap gap-1.5">
                    <UTooltip
                      v-for="p in presets"
                      :key="p.name"
                      :text="p.description"
                      :delay-duration="0"
                    >
                      <UButton
                        size="xs"
                        :color="p.name === activePreset ? 'primary' : 'neutral'"
                        :variant="p.name === activePreset ? 'solid' : 'outline'"
                        @click="applyPreset(p)"
                      >
                        {{ p.name }}
                        <span v-if="p.name === activePreset && presetEdited" class="opacity-75">· edited</span>
                      </UButton>
                    </UTooltip>
                  </div>
                  <p v-if="activeDescription" class="mt-2 text-xs text-neutral-500">
                    {{ activeDescription }}
                  </p>
                </div>

                <ParamForm
                  v-model="params"
                  :schema="schema"
                  :field-errors="fieldErrors"
                  :preset-group-label="presetGroupLabel"
                />

                <div v-if="report" class="mt-6 border-t border-neutral-200 pt-5">
                  <PrintabilityPanel :report="report" />
                </div>
              </template>
            </div>

            <footer class="border-t border-neutral-200 px-5 py-2">
              <!-- Split button: primary action remembers the last-used format; the
                   dropdown chevron switches between 3MF and STEP. -->
              <UFieldGroup class="w-full">
                <UButton
                  class="grow justify-center" color="primary" variant="solid" icon="i-lucide-download"
                  :loading="!!exporting" :disabled="!!exporting || !schema" @click="downloadExport(exportFormat)"
                >
                  {{ exporting ? `Exporting ${exporting}…` : `Export ${exportFormat}` }}
                </UButton>
                <UDropdownMenu :items="exportItems" :disabled="!!exporting || !schema" :content="{ align: 'end' }">
                  <UButton color="primary" variant="solid" icon="i-lucide-chevron-down" :disabled="!!exporting || !schema" />
                </UDropdownMenu>
              </UFieldGroup>

              <!-- Per-design links: the design's own LINKS plus an auto-generated
                   link to its source .py on GitHub. -->
              <div class="mt-2 flex justify-between border-t border-neutral-200 pt-1">
                <div class="flex flex-wrap items-center gap-1">
                  <UButton
                    v-for="link in designLinks"
                    :key="link.url"
                    size="xs"
                    variant="ghost"
                    color="neutral"
                    :icon="link.icon"
                    :label="link.label"
                    :to="link.url"
                    external
                    target="_blank"
                  />
                </div>
                <!-- Stacked, this lives in the header's overflow menu instead. -->
                <UButton
                  size="xs"
                  variant="ghost"
                  color="neutral"
                  icon="i-lucide-code"
                  label="View code"
                  title="View this design's source"
                  class="max-lg:hidden"
                  @click="openCode"
                />
              </div>
            </footer>
          </div>

          <!-- Source view: read-only, slides over the form. Mounted on first open
               and kept alive after that (see `codeMounted`). -->
          <Transition
            enter-active-class="transition-transform duration-200 ease-out"
            leave-active-class="transition-transform duration-150 ease-in"
            enter-from-class="translate-x-full"
            leave-to-class="translate-x-full"
          >
            <div
              v-if="codeMounted"
              v-show="showCode"
              class="absolute inset-0 z-10 flex flex-col bg-white"
            >
              <div
                class="flex items-center gap-1.5 border-b border-neutral-200 py-2 pr-2 pl-3
                       text-xs text-neutral-500"
              >
                <UIcon name="i-lucide-lock" class="size-3 shrink-0" />
                <span class="truncate">Read-only — {{ slug }}/design.py, as it runs.</span>
                <div class="grow" />
                <UButton
                  size="xs"
                  variant="ghost"
                  color="neutral"
                  icon="i-lucide-external-link"
                  :to="sourceUrl"
                  external
                  target="_blank"
                  title="View on GitHub"
                />
                <UButton
                  size="xs"
                  variant="ghost"
                  color="neutral"
                  icon="i-lucide-x"
                  title="Close (Esc)"
                  @click="showCode = false"
                />
              </div>
              <Suspense>
                <CodeEditor
                  :model-value="source"
                  :loading="reloading"
                  class="min-h-0 grow"
                />
                <template #fallback>
                  <div class="flex grow items-center justify-center text-sm text-neutral-400">
                    Loading editor…
                  </div>
                </template>
              </Suspense>
            </div>
          </Transition>
        </div>
      </UDashboardPanel>

      <!-- Right: viewer. Stacked, it gets a fixed slice of the screen — `svh` rather
           than `vh` so it doesn't jump when mobile browser chrome hides on scroll.
           Viewer.vue's ResizeObserver picks the new size up on its own.
           `flex-none` is load-bearing: this panel has no `resizable`, so Nuxt UI's
           theme gives it `flex-1`, and a flex-basis of 0 beats `h-` on the main axis —
           without it the two panes just split the screen evenly. -->
      <UDashboardPanel
        id="viewer"
        class="bg-neutral-100 max-lg:h-[45svh] max-lg:min-h-0 max-lg:flex-none"
      >
        <Suspense>
          <Viewer
            ref="viewerRef"
            :shapes="shapes"
            :build-stage="buildStage"
            :build-pending="buildPending"
          />
          <template #fallback>
            <div class="flex h-full items-center justify-center text-sm text-neutral-400">
              Loading viewer…
            </div>
          </template>
        </Suspense>
      </UDashboardPanel>
    </UDashboardGroup>
  </div>
</template>
