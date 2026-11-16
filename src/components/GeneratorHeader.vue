<script setup lang="ts">
import { useClipboard } from '@vueuse/core'
import { computed } from 'vue'
import { branding } from '../branding'

// The generator's title bar. It has to live in two places — inside the controls pane
// on desktop, spanning the top of the screen above the viewer on mobile — so it's a
// component rather than markup duplicated per breakpoint. `compact` picks the variant;
// AppContent mounts exactly one of them.
const props = defineProps<{
  designName: string
  /** The design's `__experimental__` flag — badged beside the title (see manifest.ts). */
  experimental?: boolean
  /** Whether a render exists — a screenshot needs something on screen to capture. */
  hasShapes: boolean
  /** Collapse the actions into an overflow menu (the mobile layout). */
  compact?: boolean
  /** Some parameter differs from its default, so "Reset to defaults" has work to do. */
  canReset?: boolean
}>()

const emit = defineEmits<{ screenshot: [], openCode: [], reset: [] }>()

// Copying the current URL needs nothing from the parent, so it lives here.
const { copy, copied } = useClipboard()
function copyLink() {
  copy(window.location.href)
}

// Compact only: the same actions the wide layout spreads across the header, the form
// footer and the preset strip. "View code" is in here because its footer button is
// hidden on mobile; "Reset to defaults" because the menu is always in reach.
const menuItems = computed(() => [[
  {
    label: copied.value ? 'Copied!' : 'Copy link',
    icon: copied.value ? 'i-lucide-check' : 'i-lucide-link',
    onSelect: copyLink,
  },
  {
    label: 'Screenshot',
    icon: 'i-lucide-camera',
    disabled: !props.hasShapes,
    onSelect: () => emit('screenshot'),
  },
  {
    label: 'View code',
    icon: 'i-lucide-code',
    onSelect: () => emit('openCode'),
  },
], [
  {
    label: 'Reset to defaults',
    icon: 'i-lucide-rotate-ccw',
    disabled: !props.canReset,
    onSelect: () => emit('reset'),
  },
]])
</script>

<template>
  <header class="flex flex-col border-b border-primary-200 bg-primary-50">
    <div
      class="flex items-start justify-between gap-2 px-5 py-3"
      :class="compact ? 'items-center py-2' : ''"
    >
      <div class="flex min-w-0 items-start gap-2" :class="compact ? 'items-center' : ''">
        <UButton
          size="xs"
          variant="ghost"
          color="neutral"
          icon="i-lucide-arrow-left"
          :to="{ name: 'gallery' }"
          title="Back to gallery"
          class="shrink-0"
          :class="compact ? '' : 'mt-0.5'"
        />
        <div class="min-w-0">
          <div class="flex min-w-0 items-center gap-2">
            <h1 class="truncate text-lg font-semibold text-neutral-800">
              {{ designName }}
            </h1>
            <!-- The gallery's `__experimental__` badge follows the design in here, where
                 it sits right above the export button. -->
            <UBadge
              v-if="experimental"
              size="sm"
              variant="subtle"
              color="warning"
              icon="i-lucide-flask-conical"
              class="shrink-0"
              title="Still being worked out — geometry and parameters may change"
            >
              Experimental
            </UBadge>
          </div>
          <p v-if="!compact" class="text-sm text-neutral-500">
            {{ branding.galleryName }}
          </p>
        </div>
      </div>

      <!-- Wide: the actions are always visible, stacked beside the title. -->
      <div v-if="!compact" class="flex shrink-0 flex-col items-end">
        <UButton
          size="xs"
          variant="ghost"
          color="neutral"
          :icon="copied ? 'i-lucide-check' : 'i-lucide-link'"
          :label="copied ? 'Copied!' : 'Copy link'"
          @click="copyLink"
        />
        <UButton
          size="xs"
          variant="ghost"
          color="neutral"
          icon="i-lucide-camera"
          label="Screenshot"
          title="Save a PNG of the current view"
          :disabled="!hasShapes"
          @click="emit('screenshot')"
        />
      </div>

      <!-- Compact: one target, so the title keeps the width on a narrow screen. -->
      <UDropdownMenu v-else :items="menuItems" :content="{ align: 'end' }">
        <UButton
          size="sm"
          variant="ghost"
          color="neutral"
          icon="i-lucide-ellipsis-vertical"
          title="Actions"
          class="shrink-0"
        />
      </UDropdownMenu>
    </div>
  </header>
</template>
