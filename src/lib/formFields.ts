import type { ParamValue, ShapeOption } from './protocol'

// The form's field model: a JSON-Schema property resolved into the one thing the
// renderer needs to draw it. `ParamForm.vue` owns the resolution (it is the half
// that sees the whole schema — visibility, dependent bounds, grouping) and
// `ParamField.vue` owns the drawing; this is the type they meet on.
//
// A field is a discriminated union over the schema's `widget` hint (see proto.py's
// field helpers). A new parameter type is a new member here, a branch in
// ParamForm's resolver, and a branch in ParamField's template.

/** Nuxt UI's size scale, shared by a field's form field and its controls. */
export type UiSize = 'xs' | 'sm' | 'md' | 'lg' | 'xl'

export interface SliderField {
  kind: 'slider'
  name: string
  label: string
  description?: string
  /** The schema default, which the field offers to revert to once changed. */
  default?: ParamValue
  size: UiSize
  unit?: string
  // Slider bounds: the comfortable range (json_schema_extra min/max).
  sliderMin: number
  sliderMax: number
  // Number-input bounds: the hard validation limits (pydantic ge/le, i.e.
  // minimum/maximum). These are wider than the slider, so a value can be typed
  // past the slider's range but is still clamped to what the model accepts.
  inputMin: number
  inputMax: number
  step: number
  isInt: boolean
}

export interface SelectField {
  kind: 'select'
  name: string
  label: string
  description?: string
  /** The schema default, which the field offers to revert to once changed. */
  default?: ParamValue
  size: UiSize
  options: ShapeOption[]
}

/**
 * Like a select, but laid out as big inline tiles (a prominent mode toggle)
 * rather than a dropdown. Same option shape; the `widget: "cards"` hint picks it.
 */
export interface CardsField {
  kind: 'cards'
  name: string
  label: string
  description?: string
  /** The schema default, which the field offers to revert to once changed. */
  default?: ParamValue
  size: UiSize
  options: ShapeOption[]
}

/** An on/off switch over a boolean (the `widget: "toggle"` hint). */
export interface ToggleField {
  kind: 'toggle'
  name: string
  label: string
  description?: string
  /** The schema default, which the field offers to revert to once changed. */
  default?: ParamValue
  size: UiSize
}

export type Field = SliderField | SelectField | CardsField | ToggleField
