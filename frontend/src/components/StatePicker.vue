<script setup>
// Selettore multiplo di stati di un'entità, a chip
import { computed } from 'vue'
import { entity, statesOf } from '../store'
import { pal } from '../colors'

const props = defineProps({ entityId: Number, modelValue: { type: Array, default: () => [] } })
const emit = defineEmits(['update:modelValue'])
const e = computed(() => entity(props.entityId))

function toggle(id) {
  const v = props.modelValue.includes(id) ? props.modelValue.filter((x) => x !== id) : [...props.modelValue, id]
  emit('update:modelValue', v)
}
</script>

<template>
  <div class="flex flex-wrap gap-1.5">
    <button
      v-for="s in statesOf(e)" :key="s.id" type="button" @click="toggle(s.id)"
      class="rounded-md px-2 py-1 font-mono text-[11.5px] font-medium ring-1 ring-inset transition cursor-pointer"
      :class="[modelValue.includes(s.id) ? pal(e.color).soft + ' !ring-2' : 'bg-white text-zinc-400 ring-zinc-200 hover:text-zinc-700', s.virtual && 'italic']"
    >{{ s.name }}</button>
    <span v-if="!e" class="text-xs text-zinc-400">{{ $t('picker.chooseEntity') }}</span>
  </div>
</template>
