<script setup>
import { computed } from 'vue'
import { stateById, entity } from '../store'
import { pal } from '../colors'

// entityId serve a dare un'entità a NON_ESISTE, che non appartiene a nessuna
const props = defineProps({ id: Number, entityId: Number, entityPrefix: Boolean, size: { type: String, default: 'sm' } })
const s = computed(() => (props.id == null ? null : stateById(props.id)))
const e = computed(() => (props.entityId ? entity(props.entityId) : s.value && !s.value.virtual && entity(s.value.entity_id)))
</script>

<template>
  <span
    class="inline-flex items-center gap-1 rounded-md font-mono font-medium ring-1 ring-inset whitespace-nowrap"
    :class="[
      size === 'xs' ? 'px-1.5 py-px text-[10.5px]' : 'px-2 py-0.5 text-[11.5px]',
      s?.virtual ? 'bg-white text-zinc-400 ring-zinc-300 italic' : s ? pal(e?.color).soft : 'bg-zinc-50 text-zinc-500 ring-zinc-200',
    ]"
  >
    <template v-if="entityPrefix && e"><span class="opacity-60 font-sans">{{ e.name }}·</span></template>
    <i v-if="s?.virtual" class="fa-regular fa-circle text-[8px]" />
    {{ s ? s.name : 'qualsiasi' }}
    <i v-if="s?.is_initial" class="fa-solid fa-play text-[8px] opacity-50" title="iniziale" />
    <i v-if="s?.is_final" class="fa-solid fa-flag-checkered text-[9px] opacity-50" title="finale" />
  </span>
</template>
