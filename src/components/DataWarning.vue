<script setup lang="ts">
import { branding } from '../branding'

// Shown instead of the boot screen when the connection looks metered (see
// useDataConsent). Nothing has been downloaded at this point — the worker is still
// unconnected — so this is a real choice, not a notice after the fact. Styled to match
// BootProgress, which is what replaces it the moment the user accepts.
defineEmits<{ accept: [], decline: [] }>()
</script>

<template>
  <div class="flex h-full w-full select-text items-center justify-center bg-neutral-50 p-6">
    <div class="mx-auto w-full max-w-md">
      <h1 class="mb-6 text-center text-xl font-semibold text-neutral-800">
        {{ branding.galleryName }}
      </h1>

      <div class="flex flex-col items-center text-center">
        <UIcon name="i-lucide-cloud-download" class="size-8 text-neutral-400" />
        <h2 class="mt-3 text-base font-semibold text-neutral-800">
          This needs a ~41 MB download
        </h2>
        <p class="mt-2 text-sm leading-relaxed text-neutral-500">
          Designs are built in your browser by a real CAD engine, which has to be
          fetched once before anything can be previewed. You look like you're on mobile
          data or a slow connection, so nothing has been downloaded yet.
        </p>
        <p class="mt-2 text-sm leading-relaxed text-neutral-500">
          It's stored by your browser afterwards — you'll only be asked this once.
        </p>

        <div class="mt-6 flex w-full flex-col gap-2">
          <UButton
            color="primary"
            variant="solid"
            icon="i-lucide-download"
            class="justify-center"
            label="Download and continue"
            @click="$emit('accept')"
          />
          <UButton
            color="neutral"
            variant="ghost"
            class="justify-center"
            label="Not now"
            @click="$emit('decline')"
          />
        </div>
      </div>
    </div>
  </div>
</template>
