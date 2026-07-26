/// <reference types="vite/client" />

/** Git hash of the build, injected at build time (see vite.config.ts). */
declare const __GIT_HASH__: string

declare module '*.vue' {
  import type { DefineComponent } from 'vue'

  const component: DefineComponent<Record<string, unknown>, Record<string, unknown>, unknown>
  export default component
}
