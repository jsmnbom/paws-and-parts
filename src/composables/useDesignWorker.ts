import type { Shapes } from 'three-cad-viewer'
import type { InjectionKey } from 'vue'
import type {
  FieldError,
  JsonSchema,
  Preset,
  PrintabilityReport,
  WorkerRequest,
  WorkerResponse,
} from '../lib/protocol'
import { inject, onScopeDispose, ref, shallowRef } from 'vue'
import { resolveSchema } from '../lib/resolveSchema'

/**
 * `idle` is the pre-boot state: the composable is live but no SharedWorker exists yet,
 * so not a byte of the ~41 MB CAD kernel has been requested. `connect()` leaves it.
 */
export type BootStatus = 'idle' | 'booting' | 'ready' | 'error'

/** The reactive worker bundle App.vue provides to the views. */
export type DesignWorker = ReturnType<typeof useDesignWorker>

/** provide/inject key so App.vue can share the one worker with every route. */
export const designWorkerKey: InjectionKey<DesignWorker> = Symbol('designWorker')

/** Inject the shared worker inside a route component (App.vue provides it). */
export function injectDesignWorker(): DesignWorker {
  const worker = inject(designWorkerKey)
  if (!worker)
    throw new Error('designWorker not provided — App.vue must provide() it above <RouterView>.')
  return worker
}

const BUILD_DEBOUNCE_MS = 120
const RELOAD_DEBOUNCE_MS = 250

export function useDesignWorker() {
  const status = ref<BootStatus>('idle')
  const bootStage = ref('Starting…')
  const bootProgress = ref(0)
  const bootError = ref<string | null>(null)

  const schema = ref<JsonSchema | null>(null)
  const presets = ref<Preset[]>([])
  // What the active design's presets vary ("Link shape"), for the picker's title.
  const presetLabel = ref('Preset')
  const shapes = shallowRef<Shapes | null>(null)
  const building = ref(false)
  const fieldErrors = ref<FieldError[]>([])
  const report = ref<PrintabilityReport | null>(null)
  // The active design's source is exec'd in the worker; a compile/exec traceback
  // (from a bad Code-tab edit) lands here for the editor to surface inline.
  const reloadError = ref<string | null>(null)
  // True while a design swap is in flight (gallery open / Code edit) — the form
  // shows a loading state until the fresh schema arrives.
  const reloading = ref(false)

  // One SharedWorker backs every tab of the origin, so a second tab connects to
  // the already-booted Python instead of re-paying the tens-of-seconds boot. We
  // talk to it over its MessagePort. SharedWorker (module type) is Baseline; a
  // browser too old to have it can't run the app, so surface a clear message
  // instead of a blank screen (no dedicated-worker fallback).
  function unsupported(message: string) {
    status.value = 'error'
    bootError.value = message
    return {
      status,
      bootStage,
      bootProgress,
      bootError,
      schema,
      presets,
      presetLabel,
      shapes,
      building,
      fieldErrors,
      report,
      reloadError,
      reloading,
      connect: () => {},
      build: () => {},
      exportModel: () => Promise.reject(new Error('SharedWorker unsupported')),
      reloadDesign: () => {},
    }
  }

  if (typeof SharedWorker === 'undefined') {
    return unsupported('This app needs a current browser (SharedWorker support). Please update.')
  }
  // Milestone logging so a confused user can screenshot the console for us. Kept
  // to lifecycle events (not per-build) to avoid drowning the log. The worker is
  // a SharedWorker, whose own console lives in a separate inspector context — so
  // these page-thread logs are what actually shows up in a screenshot.
  let bootStart = 0
  // eslint-disable-next-line no-console
  const log = (...args: unknown[]) => console.info('[lab]', ...args)

  // Nothing is fetched until this is called. Constructing the SharedWorker is what
  // kicks off the multi-megabyte kernel download, so it's deliberately *not* a side
  // effect of calling the composable — App.vue decides when (see useDataConsent).
  // Idempotent: repeat calls (a second tab, an accepted prompt after a retry) no-op.
  let port: MessagePort | null = null
  function connect() {
    if (port || status.value === 'error')
      return
    bootStart = performance.now()
    status.value = 'booting'
    log('connecting to worker…')
    try {
      const sw = new SharedWorker(new URL('../worker/pyodide.worker.ts', import.meta.url), {
        type: 'module',
      })
      port = sw.port
      port.onmessage = handleMessage
      port.start()
    }
    catch {
      // Has SharedWorker but rejects module workers — same "please update" path.
      status.value = 'error'
      bootError.value = 'This app needs a current browser (module SharedWorker support). Please update.'
    }
  }

  let nextId = 1
  let latestBuildId = 0
  let latestReloadId = 0
  let buildTimer: ReturnType<typeof setTimeout> | null = null
  let reloadTimer: ReturnType<typeof setTimeout> | null = null
  const pendingExports = new Map<
    number,
    { resolve: (b: Uint8Array) => void, reject: (e: Error) => void, format: string }
  >()

  function send(msg: WorkerRequest, transfer?: Transferable[]) {
    port?.postMessage(msg, transfer ?? [])
  }

  function handleMessage(e: MessageEvent<WorkerResponse>) {
    const msg = e.data
    switch (msg.type) {
      case 'boot':
        bootStage.value = msg.stage
        bootProgress.value = msg.progress
        break
      case 'boot-error':
        // First line only — the full trace is on screen and in bootError.
        log('boot FAILED:', msg.message.split('\n')[0])
        status.value = 'error'
        bootError.value = msg.message
        break
      case 'ready':
        // The engine is warm; no design is loaded yet (the active view selects one
        // by slug via reloadDesign, whose reply carries the schema).
        log(`engine ready in ${((performance.now() - bootStart) / 1000).toFixed(1)}s`)
        status.value = 'ready'
        break
      case 'shapes':
        // ocp-tessellate already emits the nested-array Shapes tree that
        // three-cad-viewer.render() consumes directly — just parse it.
        try {
          shapes.value = JSON.parse(msg.data) as Shapes
        }
        catch (err) {
          console.error('Failed to parse shapes', err)
        }
        break
      case 'build-result':
        if (msg.id === latestBuildId) {
          fieldErrors.value = msg.ok ? [] : (msg.errors ?? [])
          report.value = msg.report ?? null
          building.value = false
        }
        break
      case 'export-result': {
        const p = pendingExports.get(msg.id)
        if (p) {
          pendingExports.delete(msg.id)
          if (msg.bytes)
            p.resolve(msg.bytes)
          else p.reject(new Error(msg.error ?? 'Export failed'))
        }
        break
      }
      case 'log':
        // Surface Python stdout/stderr for debugging. Use console.log (not
        // console.debug) for stdout — Chrome hides the Verbose level by default,
        // so debug-level prints never show up.
        // eslint-disable-next-line no-console
        (msg.stream === 'stderr' ? console.warn : console.log)('[py]', msg.text)
        break
      case 'design-reloaded': {
        // Reply to a reloadDesign request (gallery open or Code-tab edit) or a dev
        // HMR reload (no id). A stale reply (superseded by a newer reload) is
        // ignored so an out-of-order success/failure can't clobber current state.
        if (msg.id !== undefined && msg.id !== latestReloadId)
          break
        reloading.value = false
        if (msg.ok) {
          // Dereference pydantic's `$ref`/`$defs` into the flat schema the form
          // renderer reads (see resolveSchema). Updating `schema` drives the
          // active view to (re)seed params, preserving existing values.
          reloadError.value = null
          schema.value = msg.schema ? resolveSchema(msg.schema) : null
          presets.value = msg.presets ?? []
          presetLabel.value = msg.presetLabel ?? 'Preset'
        }
        else {
          // Keep the last-good schema on screen; surface the traceback inline.
          reloadError.value = msg.error ?? 'Design failed to load.'
        }
        break
      }
      case 'reload':
        // Dev-only: the SharedWorker is closing itself because its code changed
        // (HMR). Reload onto fresh code; the reload spawns a new worker.
        location.reload()
        break
    }
  }

  /** Debounced, latest-wins build request. */
  function build(params: Record<string, unknown>) {
    if (status.value !== 'ready')
      return
    if (buildTimer)
      clearTimeout(buildTimer)
    buildTimer = setTimeout(() => {
      latestBuildId = nextId++
      building.value = true
      // Snapshot to a plain object: a Vue reactive proxy isn't structured-cloneable.
      send({ type: 'build', id: latestBuildId, params: { ...params } })
    }, BUILD_DEBOUNCE_MS)
  }

  /**
   * Debounced, latest-wins design swap. `source` is a `design.py` (a bundled
   * design's source on gallery-open, or the Code editor's contents). Its reply
   * updates `schema`/`presets` on success or `reloadError` on failure.
   */
  function reloadDesign(source: string, slug: string) {
    if (status.value !== 'ready')
      return
    reloading.value = true
    if (reloadTimer)
      clearTimeout(reloadTimer)
    reloadTimer = setTimeout(() => {
      latestReloadId = nextId++
      send({ type: 'reload-design', id: latestReloadId, source, slug })
    }, RELOAD_DEBOUNCE_MS)
  }

  function exportModel(format: string, params: Record<string, unknown>): Promise<Uint8Array> {
    return new Promise((resolve, reject) => {
      const id = nextId++
      pendingExports.set(id, { resolve, reject, format })
      send({ type: 'export', id, format, params: { ...params } })
    })
  }

  // Tell the shared worker to drop this tab's per-port state and close our port.
  // The worker keeps running for other tabs (the browser tears it down once the
  // last port closes). `pagehide` covers tab close / navigation, which does not
  // trigger onScopeDispose. No-ops when we never connected (consent declined).
  function disconnect() {
    if (!port)
      return
    port.postMessage({ type: 'disconnect' })
    port.close()
    port = null
  }
  window.addEventListener('pagehide', disconnect)

  onScopeDispose(() => {
    if (buildTimer)
      clearTimeout(buildTimer)
    if (reloadTimer)
      clearTimeout(reloadTimer)
    window.removeEventListener('pagehide', disconnect)
    disconnect()
  })

  return {
    status,
    bootStage,
    bootProgress,
    bootError,
    schema,
    presets,
    presetLabel,
    shapes,
    building,
    fieldErrors,
    report,
    reloadError,
    reloading,
    connect,
    build,
    exportModel,
    reloadDesign,
  }
}
