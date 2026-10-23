<script setup>
defineProps({ eyebrow: String, title: String, subtitle: String })
</script>

<template>
  <header class="border-b border-zinc-200/70 bg-white/70 backdrop-blur sticky top-0 z-20">
    <div class="px-8 py-5 flex items-end justify-between gap-6">
      <div class="min-w-0">
        <div v-if="eyebrow" class="eyebrow mb-1">{{ eyebrow }}</div>
        <slot name="title"><h1 class="text-2xl font-semibold tracking-tight text-zinc-900">{{ title }}</h1></slot>
        <p v-if="subtitle" class="text-sm text-zinc-500 mt-1 max-w-3xl">{{ subtitle }}</p>
      </div>
      <div class="flex items-center gap-2 shrink-0"><slot name="actions" /></div>
    </div>
  </header>
</template>
