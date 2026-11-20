<script setup>
// Campo per un'espressione sugli attributi (guardie, assegnazioni, invarianti), validata dal backend mentre si scrive
import { ref, watch } from 'vue'
import { store, api } from '../store'
import HelpLink from './HelpLink.vue'

const model = defineModel({ type: String, default: '' })
// entityId: entità di contesto, per gli attributi scritti senza entità
const props = defineProps({ entityId: Number, placeholder: String })
const state = ref(null) // { ok, error, reads }
let timer = null

watch([model, () => props.entityId], () => {
  clearTimeout(timer)
  if (!model.value.trim()) return (state.value = null)
  timer = setTimeout(async () => {
    try {
      state.value = await api.post(`/domains/${store.model.id}/expr/check`, { text: model.value, entity_id: props.entityId })
    } catch {
      state.value = null
    }
  }, 300)
}, { immediate: true })
</script>

<template>
  <div class="flex-1 min-w-0">
    <div class="flex items-center gap-1.5">
      <input v-model="model" class="input !py-1.5 font-mono flex-1" :placeholder="placeholder || $t('expr.placeholder')"
             :class="state && !state.ok && '!ring-rose-300'" />
      <HelpLink topic="attributes" />
    </div>
    <p v-if="state && !state.ok" class="text-[11px] text-rose-600 mt-1">{{ state.error }}</p>
    <p v-else-if="state?.reads?.length" class="text-[11px] text-zinc-500 mt-1">
      <i class="fa-solid fa-check text-emerald-600" /> {{ $t('expr.reads', { list: state.reads.join(', ') }) }}
    </p>
  </div>
</template>
