<script setup lang="ts">
import type { Field, SliderField } from '../lib/formFields'
import type { ParamValue } from '../lib/protocol'
import { useMediaQuery } from '@vueuse/core'
import { computed, ref } from 'vue'
import ShapePreview from './ShapePreview.vue'
import ShapeSelect from './ShapeSelect.vue'

// One resolved parameter control. Rendering only: the field has already been
// resolved from the schema by ParamForm, which also owns the model. Split out so
// the main run of controls and the "Advanced" disclosure draw a field the same
// way instead of duplicating this block.
const props = defineProps<{
  field: Field
  modelValue: ParamValue | undefined
  error?: string
}>()

const emit = defineEmits<{ 'update:modelValue': [ParamValue] }>()

// Help. Where there is a mouse and room for the side-by-side layout, the info
// icon opens its popover on hover, the way a tooltip would — and on click/Enter
// too, so it stays keyboard-reachable. Anywhere else (touch, the stacked layout)
// hover means nothing, so it's a plain click/tap popover. Reka picks the popover
// flavour once at setup, hence the `key` on the mode below.
const hoverHelp = useMediaQuery('(min-width: 1024px) and (hover: hover)')
const helpOpen = ref(false)
function toggleHelp() {
  // Click mode's trigger already toggles itself; only hover mode needs this.
  if (hoverHelp.value)
    helpOpen.value = !helpOpen.value
}

// Offer a way back to the schema default once the value has left it.
const changed = computed(() =>
  props.field.default !== undefined && props.modelValue !== props.field.default,
)

// Intl.NumberFormat's `style: 'unit'` only accepts CLDR-sanctioned unit
// identifiers ("millimeter", "degree", …) and throws a RangeError on anything
// else ("mm", "°", "links"). Designs write the everyday symbol, so map the common
// ones onto their CLDR name first…
const UNIT_ALIASES: Record<string, string> = {
  'mm': 'millimeter',
  'cm': 'centimeter',
  'm': 'meter',
  'g': 'gram',
  'deg': 'degree',
  '°': 'degree',
}
function resolveUnit(unit: string): string {
  return UNIT_ALIASES[unit] ?? unit
}

// …and for anything still outside the list ("links", "layers"), fall back to
// plain decimal formatting, so an unsupported unit degrades to a bare number
// instead of crashing the form.
function isSupportedUnit(unit: string): boolean {
  try {
    // eslint-disable-next-line no-new
    new Intl.NumberFormat(undefined, { style: 'unit', unit })
    return true
  }
  catch {
    return false
  }
}

function numberFormatOptions(f: SliderField) {
  const unit = f.unit ? resolveUnit(f.unit) : undefined
  if (unit && isSupportedUnit(unit)) {
    return {
      style: 'unit',
      unit,
      unitDisplay: 'narrow',
    }
  }
  return { style: 'decimal' }
}
</script>

<template>
  <UFormField
    :label="field.label"
    :error="error"
    :size="field.size"
  >
    <!-- Label with an optional info button (the field's description, in a
         popover — see `hoverHelp`) and, once the value differs from the default,
         a revert button. -->
    <template #label>
      <span class="inline-flex items-center gap-1">
        {{ field.label }}
        <UPopover
          v-if="field.description"
          :key="hoverHelp ? 'hover' : 'click'"
          v-model:open="helpOpen"
          :mode="hoverHelp ? 'hover' : 'click'"
          :open-delay="0"
          :close-delay="100"
          :content="{ side: 'top', align: 'start' }"
        >
          <button
            type="button"
            class="inline-flex cursor-help rounded-sm text-neutral-400 hover:text-neutral-600
                   focus-visible:outline-2 focus-visible:outline-primary-500"
            :aria-label="`About ${field.label}`"
            @click="toggleHelp"
          >
            <UIcon name="i-lucide-info" class="size-3.5 shrink-0" />
          </button>
          <template #content>
            <p class="max-w-xs whitespace-pre-line px-2.5 py-1.5 text-xs text-highlighted select-text">
              {{ field.description }}
            </p>
          </template>
        </UPopover>
        <UButton
          v-if="changed"
          size="xs"
          variant="ghost"
          color="neutral"
          icon="i-lucide-rotate-ccw"
          class="-my-1 p-0.5 text-neutral-400"
          :aria-label="`Reset ${field.label} to its default`"
          title="Reset to default"
          @click="emit('update:modelValue', field.default!)"
        />
      </span>
    </template>

    <!-- Big side-by-side tiles for a prominent mode toggle (widget: "cards"). -->
    <div
      v-if="field.kind === 'cards'"
      class="grid gap-2"
      :style="{ gridTemplateColumns: `repeat(${field.options.length}, minmax(0, 1fr))` }"
    >
      <button
        v-for="opt in field.options"
        :key="opt.value"
        type="button"
        class="flex flex-col items-start gap-1 rounded-lg border p-3 text-left transition-colors"
        :class="modelValue === opt.value
          ? 'border-primary-500 bg-primary-50 ring-1 ring-primary-500 dark:bg-primary-950'
          : 'border-neutral-200 hover:border-neutral-300 dark:border-neutral-800 dark:hover:border-neutral-700'"
        @click="emit('update:modelValue', opt.value)"
      >
        <ShapePreview v-if="opt.svg" :svg="opt.svg" :size="44" class="mb-0.5 text-neutral-600 dark:text-neutral-300" />
        <span class="text-sm font-medium">{{ opt.label }}</span>
        <span v-if="opt.description" class="text-xs text-neutral-500">{{ opt.description }}</span>
      </button>
    </div>

    <!-- An on/off switch (widget: "toggle"). -->
    <USwitch
      v-else-if="field.kind === 'toggle'"
      :model-value="modelValue === true"
      :size="field.size"
      @update:model-value="(v: boolean) => emit('update:modelValue', v)"
    />

    <ShapeSelect
      v-else-if="field.kind === 'select'"
      :model-value="modelValue as number | string"
      :options="field.options"
      :size="field.size"
      @update:model-value="(v: number | string) => emit('update:modelValue', v)"
    />

    <div v-else class="flex items-center gap-3">
      <USlider
        class="grow"
        :size="field.size"
        :model-value="(modelValue as number)"
        :min="field.sliderMin"
        :max="field.sliderMax"
        :step="field.step"
        @update:model-value="(v: number | number[]) => emit('update:modelValue', Array.isArray(v) ? v[0] : v)"
      />
      <UInputNumber
        class="w-28 shrink-0 max-sm:w-20"
        :size="field.size"
        :model-value="(modelValue as number)"
        :min="field.inputMin"
        :max="field.inputMax"
        :step="field.step"
        :format-options="numberFormatOptions(field)"
        @update:model-value="(v: number) => emit('update:modelValue', v)"
      />
    </div>
  </UFormField>
</template>
