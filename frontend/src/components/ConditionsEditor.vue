<script setup>
// Condizioni sugli stati in AND (entità in / non in stati), come nelle invarianti e nelle aspettative
import { computed } from 'vue'
import { store } from '../store'
import StatePicker from './StatePicker.vue'

const rules = defineModel({ type: Array, required: true })
const entities = computed(() => store.model.entities.filter((e) => e.states.length))
function add() {
  const e = entities.value[rules.value.length % Math.max(1, entities.value.length)] || entities.value[0]
  if (e) rules.value.push({ entity_id: e.id, state_ids: [], negate: 0 })
}
</script>

<template>
  <div class="space-y-2">
    <div v-for="(r, i) in rules" :key="i" class="rounded-xl ring-1 ring-zinc-200 p-3 bg-zinc-50/50 space-y-2.5">
      <div class="flex items-center gap-2">
        <span v-if="i" class="text-xs font-semibold text-zinc-400 w-5">{{ $t('inv.and') }}</span>
        <select v-model="r.entity_id" class="input !w-48 !py-1.5" @change="r.state_ids = []">
          <option v-for="e in entities" :key="e.id" :value="e.id">{{ e.name }}</option>
        </select>
        <select v-model="r.negate" class="input !w-44 !py-1.5">
          <option :value="0">{{ $t('inv.inOneOf') }}</option>
          <option :value="1">{{ $t('inv.inNoneOf') }}</option>
        </select>
        <div class="flex-1" />
        <button type="button" class="icon-btn hover:!text-rose-600" @click="rules.splice(i, 1)"><i class="fa-solid fa-xmark fa-fw text-[14px]" /></button>
      </div>
      <StatePicker v-model="r.state_ids" :entity-id="r.entity_id" />
    </div>
    <button type="button" class="btn-ghost !text-xs" @click="add"><i class="fa-solid fa-plus fa-fw text-[12px]" /> {{ $t('inv.condition') }}</button>
  </div>
</template>
