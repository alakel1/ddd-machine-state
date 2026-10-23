<script setup>
// Sequenza di azioni che porta a uno stato (es. a un edge case)
import { ename, entity } from '../store'
import { pal } from '../colors'
defineProps({ steps: Array })
</script>

<template>
  <div class="flex flex-wrap items-center gap-1 text-xs">
    <span class="tag bg-zinc-50 text-zinc-500 ring-zinc-200">inizio</span>
    <template v-for="(s, i) in steps" :key="i">
      <i class="fa-solid fa-arrow-right fa-fw text-[11px] text-zinc-300" />
      <span class="inline-flex items-center gap-1 rounded-md bg-white px-1.5 py-0.5 ring-1 ring-zinc-200">
        <span class="size-1.5 rounded-full" :class="pal(entity(s.entity_id)?.color).dot" />
        <span class="text-zinc-400">{{ ename(s.entity_id) }}</span>
        <span class="font-medium text-zinc-700">{{ s.action }}</span>
      </span>
    </template>
  </div>
</template>
