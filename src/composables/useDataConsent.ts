import { useLocalStorage, useNetwork } from '@vueuse/core'
import { computed } from 'vue'

/**
 * Consent to spend ~41 MB of the user's data on the CAD kernel.
 *
 * Booting the engine downloads Pyodide, the Python stdlib and the pinned wheels —
 * OCP.wasm alone is 21.6 MB. On a phone plan that deserves an ask, so when the
 * connection looks constrained we hold the worker back until the user agrees
 * (`useDesignWorker().connect()`), and remember the answer forever after.
 *
 * There is no reliable "is this metered?" API: `navigator.connection` is Chromium-only
 * and Safari/Firefox expose nothing at all. So this is a heuristic, and every check is
 * a *positive* signal — when we can't tell, we don't nag.
 *
 * Note this asks a different question from the layout breakpoints, which use media
 * queries: "is the viewport narrow" is about a window, "is this a phone" is about a
 * device. A desktop user with a half-width window gets the stacked layout and no prompt.
 */

// Module-scope so App.vue and Generator.vue read one shared state rather than two
// independent refs that would have to be kept in sync.
const consented = useLocalStorage('lab-data-consent', false)

/**
 * Phones only — a tablet is nearly always on wifi, so gating an iPad costs a tap and
 * buys nothing. Two positive signals, and a `false` from the first must not
 * short-circuit the second:
 *
 * - `userAgentData.mobile` is authoritative on Chromium and already phone-only (Chrome
 *   on an Android tablet reports `mobile: false`). Absent everywhere else.
 * - The UA regex is the only signal Safari and Firefox give us. Android *phones* carry
 *   a "Mobile" token that Android tablets omit, which is what keeps tablets out.
 *   Nothing here matches iPadOS by design: iPadOS 13+ Safari ships a desktop
 *   ("Macintosh") UA and we're happy to let it pass as desktop.
 *
 * `NavigatorUAData` isn't in TypeScript's lib.dom yet, hence the cast.
 */
function isPhone(): boolean {
  const nav = navigator as Navigator & { userAgentData?: { mobile?: boolean } }
  if (nav.userAgentData?.mobile)
    return true
  return /iPhone|Android.*Mobile|Windows Phone/i.test(navigator.userAgent)
}

const { saveData, effectiveType, type } = useNetwork()

const constrained = computed(() =>
  saveData.value === true
  || type.value === 'cellular'
  || ['slow-2g', '2g', '3g'].includes(effectiveType.value ?? '')
  || isPhone(),
)

export function useDataConsent() {
  return {
    /**
     * Whether to ask before booting. The stored flag doubles as the cold-cache check:
     * a warm HTTP cache implies a previous grant on this device, so there's nothing
     * left to warn about.
     */
    needsConsent: computed(() => !consented.value && constrained.value),
    /** Remember the answer, so this is asked at most once per device. */
    grant: () => {
      consented.value = true
    },
  }
}
