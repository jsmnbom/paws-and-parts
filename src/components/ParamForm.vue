<script setup lang="ts">
import type { Field, UiSize } from '../lib/formFields'
import type { FieldError, JsonSchema, JsonSchemaProperty, ParamValue } from '../lib/protocol'
import { computed, nextTick, ref } from 'vue'
import ParamField from './ParamField.vue'

const props = defineProps<{
  schema: JsonSchema
  modelValue: Record<string, ParamValue>
  fieldErrors: FieldError[]
  // Titles the fields a preset writes ("Link shape · Default"). Rendered as a
  // rule before the first field that isn't `preset_stable`, so the boundary
  // between "yours" (count, layout) and "the preset's" is visible. Omitted when
  // the design has no presets.
  presetGroupLabel?: string
}>()

const emit = defineEmits<{ 'update:modelValue': [Record<string, ParamValue>] }>()

// The schema's `size` hint carries Nuxt UI's size tokens directly; unset falls
// back to the default.
const DEFAULT_SIZE: UiSize = 'sm'
function resolveSize(size?: string): UiSize {
  return (size as UiSize) ?? DEFAULT_SIZE
}

// A field is a slider, a dropdown, a card row or a switch. The discriminator is
// the schema's `widget` hint (see proto.py's field helpers): "shape"/"select"
// render the dropdown, "cards" the tiles, "toggle" the switch, "slider" the
// slider. A new widget kind is a new branch here and in ParamField.vue. When
// `widget` is absent we fall back structurally (has `options` -> dropdown).
function resolveField(name: string, p: JsonSchemaProperty): Field {
  const label = p.label ?? p.title ?? name
  const description = p.description
  const def = p.default as ParamValue | undefined
  const size = resolveSize(p.size)
  const widget = p.widget ?? (p.options ? 'select' : 'slider')
  if (widget === 'toggle' || p.type === 'boolean') {
    return { kind: 'toggle', name, label, description, default: def, size }
  }
  if (widget === 'cards' && p.options) {
    return { kind: 'cards', name, label, description, default: def, size, options: p.options }
  }
  if (widget !== 'slider' && p.options) {
    return { kind: 'select', name, label, description, default: def, size, options: p.options }
  }
  const isInt = p.type === 'integer'
  const hardMin = p.minimum ?? p.exclusiveMinimum
  const hardMax = p.maximum ?? p.exclusiveMaximum
  const sliderMin = p.sliderMin ?? hardMin ?? 0
  let sliderMax = p.sliderMax ?? hardMax ?? 100
  let inputMax = hardMax ?? sliderMax
  // A `slider_max_by` hint tightens the max to depend on another field's
  // current value (e.g. tilt_mult's max varies with cross_section). It is a
  // real constraint here, so it caps both the slider and the number input.
  for (const [field, byValue] of Object.entries(p.slider_max_by ?? {})) {
    const dyn = byValue[props.modelValue[field] as string]
    if (dyn != null) {
      sliderMax = dyn
      inputMax = dyn
    }
  }
  return {
    kind: 'slider',
    name,
    label,
    description,
    default: def,
    size,
    unit: p.unit,
    sliderMin,
    sliderMax,
    inputMin: hardMin ?? sliderMin,
    inputMax,
    step: p.step ?? (isInt ? 1 : 0.1),
    isInt,
  }
}

// A field with a `show_if` hint is rendered only when the current model value
// matches every entry in it (e.g. brim_diameter shows only when brim === "ears",
// tilt_mult only for a decagon/dodecagon cross_section). An array entry matches
// by membership; a scalar by equality.
// Reading modelValue here makes the computed re-evaluate as values change.
function matchesShowIf(actual: unknown, expected: unknown): boolean {
  return Array.isArray(expected) ? expected.includes(actual) : actual === expected
}
function isVisible(p: JsonSchemaProperty): boolean {
  if (!p.show_if)
    return true
  return Object.entries(p.show_if).every(([k, v]) => matchesShowIf(props.modelValue[k], v))
}

const visible = computed<[string, JsonSchemaProperty][]>(() =>
  Object.entries(props.schema.properties).filter(
    ([, p]: [string, JsonSchemaProperty]) => isVisible(p),
  ),
)

// The main run of controls, and the fit-and-finish ones a design flagged
// `advanced` (fillets, tolerances). The latter keep their declaration order but
// move as a block into the disclosure below, so the form leads with what people
// actually reach for. A design that flags nothing renders exactly as before.
const mainFields = computed<Field[]>(() =>
  visible.value.filter(([, p]) => !p.advanced).map(([name, p]) => resolveField(name, p)),
)
const advancedFields = computed<Field[]>(() =>
  visible.value.filter(([, p]) => p.advanced).map(([name, p]) => resolveField(name, p)),
)

// Closed until asked for — except that a validation error inside it must never
// hide, so an errored advanced field forces the disclosure open.
const advancedOpen = ref(false)
const advancedIsOpen = computed(
  () => advancedOpen.value || advancedFields.value.some(f => errorFor.value[f.name]),
)

// The visible field the preset rule sits above: the first one a preset would
// write. Designs declare their `preset_stable` fields first (see design.py), so
// this is the single split point rather than a per-field marker. Advanced fields
// are already past the boundary by construction, so the main run owns the rule.
const groupStart = computed(() => {
  if (!props.presetGroupLabel)
    return null
  return mainFields.value.find(f => !props.schema.properties[f.name]?.preset_stable)?.name ?? null
})

// Map field name -> first error message. Errors with an empty loc are form-level.
const errorFor = computed<Record<string, string>>(() => {
  const map: Record<string, string> = {}
  for (const e of props.fieldErrors) {
    const key = e.loc[0]
    if (key && !map[key])
      map[key] = e.msg
  }
  return map
})

const formErrors = computed(() => props.fieldErrors.filter(e => e.loc.length === 0))

// Bring the failure into view, for the "Build failed" banner over the viewer: the
// form-level alert, or else the first field showing an error (opening "Advanced"
// if that's where it is — `advancedIsOpen` already forces that).
const root = ref<HTMLElement | null>(null)
const formErrorsRef = ref<{ $el: HTMLElement } | null>(null)
async function revealErrors() {
  await nextTick()
  const target = formErrorsRef.value?.$el ?? root.value?.querySelector('[data-slot="error"]')
  target?.scrollIntoView({ behavior: 'smooth', block: 'center' })
}
defineExpose({ revealErrors })

function setValue(name: string, value: ParamValue) {
  // UInputNumber re-emits update:model-value on blur, so clicking away from a
  // field (e.g. into the viewer to orbit) fires this with an unchanged value.
  // Bail on no-ops so we don't push an identical params object and trigger a
  // pointless rebuild — which would re-render the scene and reset the camera.
  if (props.modelValue[name] === value)
    return
  emit('update:modelValue', { ...props.modelValue, [name]: value })
}
</script>

<template>
  <div ref="root" class="flex flex-col gap-5">
    <!-- A build error that belongs to no one field (a geometry failure, a
         cross-field rule) leads the form, where it can't be scrolled past. -->
    <UAlert
      v-if="formErrors.length"
      ref="formErrorsRef"
      color="error"
      variant="soft"
      icon="i-lucide-triangle-alert"
      title="Build failed"
      :description="formErrors.map((e) => e.msg).join('; ')"
    />

    <template v-for="f in mainFields" :key="f.name">
      <!-- Everything from here down is what the selected preset set. -->
      <div
        v-if="f.name === groupStart"
        class="flex items-center gap-2 text-[10px] font-medium uppercase tracking-wider text-neutral-400"
      >
        <span>{{ presetGroupLabel }}</span>
        <span class="h-px grow bg-neutral-200" />
      </div>

      <ParamField
        :field="f"
        :model-value="modelValue[f.name]"
        :error="errorFor[f.name]"
        @update:model-value="(v: ParamValue) => setValue(f.name, v)"
      />
    </template>

    <!-- Fit and finish: reachable, but out of the way until asked for. -->
    <UCollapsible
      v-if="advancedFields.length"
      :open="advancedIsOpen"
      @update:open="advancedOpen = $event"
    >
      <UButton
        color="neutral"
        variant="ghost"
        size="sm"
        class="w-full justify-between px-0 hover:bg-transparent"
        label="Advanced"
        :trailing-icon="advancedIsOpen ? 'i-lucide-chevron-up' : 'i-lucide-chevron-down'"
      />
      <template #content>
        <div class="flex flex-col gap-5 pt-3">
          <ParamField
            v-for="f in advancedFields"
            :key="f.name"
            :field="f"
            :model-value="modelValue[f.name]"
            :error="errorFor[f.name]"
            @update:model-value="(v: ParamValue) => setValue(f.name, v)"
          />
        </div>
      </template>
    </UCollapsible>
  </div>
</template>
