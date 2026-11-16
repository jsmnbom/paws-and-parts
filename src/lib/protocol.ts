// Message protocol shared between the main thread and the Pyodide worker.

/**
 * A structured validation / build error, surfaced inline on the form.
 *  `loc` is the pydantic error location; `loc[0]` (when present) is the field.
 */
export interface FieldError {
  loc: string[]
  msg: string
  type: string
}

/** Severity of a printability finding; drives its colour in the panel. */
export type ReportStatus = 'ok' | 'warning' | 'error'

/**
 * One measured/derived finding: a labelled value with optional unit, an
 *  explanatory detail line, and a status colour.
 */
export interface ReportItem {
  label: string
  value: number
  unit?: string
  detail?: string
  status?: ReportStatus
}

/**
 * Generic analysis report — the TS mirror of `proto.py`'s `Report.to_dict()`. A
 *  flat list of findings the panel renders as-is, so it is not chain-specific.
 */
export interface PrintabilityReport {
  overall_status: ReportStatus
  summary: string
  items: ReportItem[]
}

export interface BuildResult {
  ok: boolean
  errors?: FieldError[]
  report?: PrintabilityReport
  /** The geometry phase's `@memo` sub-part cache hits/misses (see proto.py). */
  memo?: { hits: number, misses: number }
}

/**
 * The coarse phases of a build, in order — geometry, tessellation, analysis.
 *  Each is one Python call, and the boundaries between them are the only moments
 *  the worker's event loop runs during a build (see `build-stage` below).
 */
export type BuildStage = 'building' | 'tessellating' | 'analyzing'

// --- Main thread -> worker ------------------------------------------------- //
export type WorkerRequest
  = | { type: 'build', id: number, params: Record<string, unknown> }
  // `name` is the params-encoding basename the frontend already computed for the
  // download filename (`exportFilename` in `paramsUrl.ts`); the worker reuses it
  // as the 3MF project's object name too, rather than recomputing which params
  // differ from default in Python as well.
    | { type: 'export', id: number, format: string, params: Record<string, unknown>, name: string }
  // Swap the active design: exec `source` (a design.py) in place and hand back
  // its fresh schema/presets. Drives both gallery design-selection (a bundled
  // design's source) and the Code tab (the editor's contents). Latest-wins, like
  // builds. `slug` identifies the design so dev HMR reloads only when active.
    | { type: 'reload-design', id: number, source: string, slug: string }
  // A tab going away (dispose / pagehide): the SharedWorker drops this port's
  // per-port scheduler state. The browser tears the worker down once the last
  // port closes, so this is cleanup, not shutdown.
    | { type: 'disconnect' }

// --- Worker -> main thread ------------------------------------------------- //
export type WorkerResponse
  = | { type: 'boot', stage: string, progress: number }
    | { type: 'boot-error', message: string }
  // The engine (Pyodide/OCP) finished booting. No design is loaded yet — the main
  // thread selects one by slug and sends `reload-design`, whose reply carries the
  // schema. So the gallery can warm the engine before any design is chosen.
    | { type: 'ready' }
    | { type: 'log', stream: 'stdout' | 'stderr', text: string }
  // Tessellated geometry for build `id`. Emitted from inside the tessellate
  // phase via the host bridge, so it arrives before that build's `build-result`.
    | { type: 'shapes', id: number, data: string }
  // A build entered a new phase. Posted *between* the Python calls a build is
  // made of — while one is running the worker is blocked, so these boundaries
  // are the only progress signal there is (and the only place a build can be
  // given up on; see `build-cancelled`).
    | { type: 'build-stage', id: number, stage: BuildStage }
    | ({ type: 'build-result', id: number } & BuildResult)
  // Build `id` was abandoned at a phase boundary because newer params (or a
  // design swap) had already arrived. No `build-result` follows for that id —
  // the work it would have reported is being redone with the newer params.
    | { type: 'build-cancelled', id: number }
  // How long each phase of build `id` took, in ms (undefined for a phase that
  // never ran — e.g. abandoned before tessellate/analyze). Sent alongside the
  // terminal message (`build-result` or `build-cancelled`) so the page-thread
  // console can log it — the worker's own console is a separate inspector
  // context a user screenshot won't show.
  // `cacheHits`/`cacheMisses` count the geometry phase's `@memo` lookups.
    | { type: 'build-timing', id: number, geometryMs?: number, tessellateMs?: number, analyzeMs?: number, totalMs: number, cacheHits?: number, cacheMisses?: number }
    | { type: 'export-result', id: number, format: string, bytes?: Uint8Array, error?: string }
  // Reply to `reload-design` (and dev HMR of a design.py — then `id` is absent).
  // On success carries the fresh schema/presets; on failure `ok:false` + the
  // Python compile/exec `error` traceback so the Code tab can surface it inline.
  // The previous design stays active on failure.
    | {
      type: 'design-reloaded'
      id?: number
      ok: boolean
      schema?: JsonSchema
      presets?: Preset[]
      // What the presets vary, in the design's words (`Design.PRESET_LABEL`) —
      // titles the preset picker and the rule above the fields presets write.
      presetLabel?: string
      error?: string
    }
  // Dev-only HMR signal: the SharedWorker is about to close itself because its
  // code (or a Python asset) changed, so tabs should reload onto fresh code.
  // Never emitted in a production build (the emitting block is tree-shaken).
    | { type: 'reload' }

// --- JSON Schema (the subset the form renderer walks) ---------------------- //
/** A form parameter's value: what a field holds and what a preset or the URL carries. */
export type ParamValue = number | string | boolean

export interface JsonSchemaProperty {
  type?: string
  title?: string
  default?: unknown
  description?: string
  enum?: unknown[]
  minimum?: number
  maximum?: number
  exclusiveMinimum?: number
  exclusiveMaximum?: number
  // widget hints (json_schema_extra). `widget` is the discriminator the form
  // switches on (see ParamForm.vue): "slider" (numeric), "shape" (choice with a
  // geometry-derived preview SVG), "select" (plain enum/Literal dropdown). New
  // parameter types add a value here + a branch there. Set by proto.py's field
  // helpers — SliderField / ChoiceField / SelectField / CardsField, one per
  // widget. "cards" is a small set of mutually-exclusive options laid out as big
  // inline tiles (not a dropdown); "toggle" is an on/off switch over a boolean.
  widget?: 'slider' | 'shape' | 'select' | 'cards' | 'toggle'
  label?: string
  unit?: string
  // Nuxt UI size for the field's widgets (e.g. "lg" for the shape pickers, "xs"
  // for the minor tilt/brim sliders). Absent means the default size.
  size?: 'xs' | 'sm' | 'md' | 'lg' | 'xl'
  // The slider's comfortable range (as opposed to minimum/maximum, the hard
  // pydantic validation bounds). See ParamForm's SliderField.
  sliderMin?: number
  sliderMax?: number
  // Dynamic slider max: `{ field: { value: max } }`. When the current model value
  // of `field` has an entry, it overrides `sliderMax` (and the hard input max).
  // Used by tilt_mult, whose usable range depends on the chosen cross_section.
  slider_max_by?: Record<string, Record<string, number>>
  step?: number
  // Choice fields carry their options (with a geometry-derived preview SVG). A
  // property with `options` is rendered as a shape dropdown instead of a slider.
  options?: ShapeOption[]
  // Conditional visibility: a map of other-field-name -> required value(s). The
  // field is shown only when the current model value matches every entry — an
  // array entry matches if the value is one of its members (e.g. tilt_mult's
  // `{ "cross_section": ["decagon", "dodecagon"] }`), a scalar by equality (e.g.
  // brim_diameter's `{ "brim": "ears" }`).
  show_if?: Record<string, unknown>
  // This field belongs to the user, not to a preset: applying a preset restores
  // it to its current value instead of the preset's/default one (the chain's link
  // count, layout and center skip). It also splits the form — stable fields are
  // declared first, and the preset rule sits above the first field that isn't one.
  preset_stable?: boolean
  // Changing this field rearranges the whole design rather than tweaking it, so
  // the viewer reframes on the result instead of holding the current camera (the
  // chain's line/spiral layout). Applying a preset recenters too, always.
  recenter?: boolean
  // Fit-and-finish: a parameter that has to be reachable but doesn't belong in
  // the main run of controls (fillets, tolerances). The form collects these into
  // a closed "Advanced" disclosure at the end, in declaration order.
  advanced?: boolean
  [key: string]: unknown
}

/**
 * One choice in a shape/cross-section dropdown or card. `svg` is generated from
 *  the real build123d outline (proto.py's `outline_svg`) so previews match the
 *  geometry.
 */
export interface ShapeOption {
  value: number | string
  label: string
  description?: string
  svg?: { viewBox: string, paths: string[] }
}

/**
 * A curated starting point (a design's `PRESETS`). Applied by replacement: the
 *  form rebuilds from the schema defaults, lays `params` over them, then restores
 *  every `preset_stable` field to what the user had. `params` arrives with the
 *  stable fields already stripped (see `Design.presets_json`).
 */
export interface Preset {
  name: string
  description?: string
  params: Record<string, ParamValue>
}

export interface JsonSchema {
  title?: string
  type?: string
  properties: Record<string, JsonSchemaProperty>
  required?: string[]
  $defs?: Record<string, unknown>
}
