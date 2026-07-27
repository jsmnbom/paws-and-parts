<script setup lang="ts">
import type { BuildStage } from '../lib/protocol'
import { onBeforeUnmount, ref, watch } from 'vue'

// A hairline progress bar for the rebuild in flight, drawn across the top edge of
// the viewer. What makes it a *determinate* bar rather than a shimmer is that the
// worker reports which phase it's in (see BuildStage): the phases are coarse, but
// they're real, so the bar tracks something instead of miming it.
const props = defineProps<{
  /** The phase the worker is in, or null when no Python call is running. */
  stage: BuildStage | null
  /** A build is queued or debouncing — nothing is running, but something is coming. */
  pending: boolean
}>()

// Where each phase sits. Geometry is by far the longest and has no internal
// signal, so it eases toward its ceiling with elapsed time (below) rather than
// sitting still; tessellation and analysis are short enough to just step.
const STAGE_SPAN: Record<BuildStage, [number, number]> = {
  building: [0.05, 0.5],
  tessellating: [0.5, 0.9],
  analyzing: [0.9, 1],
}

// How fast `building` approaches its ceiling: after this long it's ~63% of the
// way through its span. Loosely the build time we expect, and forgiving in both
// directions — a quick build barely gets going, a slow one never stalls.
const GEOMETRY_TAU_MS = 900

// Nothing is drawn for the first moments of a build, so the common sub-200 ms
// rebuild passes without a flash of chrome.
const APPEAR_AFTER_MS = 200

const visible = ref(false)
const progress = ref(0)

let frame = 0
let runTimer: ReturnType<typeof setTimeout> | null = null
let stageStart = 0
// A *run* spans everything between two idle moments, so a build that supersedes
// another continues the same bar instead of restarting it. Only when the form
// goes quiet does the next run start from the left. `runTimer` belongs to the run
// (the appear delay, then the wind-down) and must outlive the phase changes
// within it — only the animation frame is torn down per phase.
let running = false

function stopFrame() {
  if (frame)
    cancelAnimationFrame(frame)
  frame = 0
}

function stopTimer() {
  if (runTimer)
    clearTimeout(runTimer)
  runTimer = null
}

function tick() {
  const stage = props.stage
  if (!stage)
    return
  const [from, to] = STAGE_SPAN[stage]
  const eased = stage === 'building'
    ? from + (to - from) * (1 - Math.exp(-(performance.now() - stageStart) / GEOMETRY_TAU_MS))
    : to
  // Only ever forward: a build that overtakes another inherits its position
  // rather than snapping backwards, so a drag reads as one continuous run.
  progress.value = Math.max(progress.value, eased)
  frame = requestAnimationFrame(tick)
}

watch(
  () => [props.stage, props.pending] as const,
  ([stage, pending]) => {
    stopFrame()

    if (!stage && !pending) {
      if (!running)
        return
      running = false
      stopTimer()
      // Run the bar out to full and fade it, so a build that was on screen ends
      // by arriving rather than vanishing. One that never appeared just resets.
      if (visible.value) {
        progress.value = 1
        runTimer = setTimeout(() => {
          visible.value = false
          progress.value = 0
        }, 200)
      }
      else {
        progress.value = 0
      }
      return
    }

    if (!running) {
      running = true
      progress.value = 0
      stopTimer() // a wind-down still fading out; this run takes the bar back
      runTimer = setTimeout(() => {
        visible.value = true
      }, APPEAR_AFTER_MS)
    }

    if (stage) {
      stageStart = performance.now()
      tick()
    }
    // Queued but not running: hold at the floor (so there's a bar to see the
    // moment it fades in) until the worker reports a phase.
    else {
      progress.value = Math.max(progress.value, STAGE_SPAN.building[0])
    }
  },
  { immediate: true },
)

onBeforeUnmount(() => {
  stopFrame()
  stopTimer()
})
</script>

<template>
  <div
    class="pointer-events-none absolute inset-x-0 top-0 z-20 h-1 overflow-hidden
           transition-opacity duration-200"
    :class="visible ? 'opacity-70' : 'opacity-0'"
    role="progressbar"
    aria-label="Rebuilding"
  >
    <div
      class="h-full origin-left bg-primary-500 transition-[width] duration-150 ease-out"
      :style="{ width: `${progress * 100}%` }"
    />
  </div>
</template>
