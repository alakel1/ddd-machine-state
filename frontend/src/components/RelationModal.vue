<script setup>
// Crea o modifica una relazione: usato dalla Panoramica e dalla scheda entità
import { computed, ref } from 'vue'
import { store, api, reloadModel, ename, entity, toast, goneStates, offStates } from '../store'
import { t } from '../i18n'
import Modal from './Modal.vue'
import StateChip from './StateChip.vue'

// relation: relazione esistente da modificare, oppure null per una nuova (sourceId = padre proposto)
const props = defineProps({ relation: Object, sourceId: Number })
const emit = defineEmits(['close'])

const m = computed(() => store.model)
const CARDINALITIES = ['1-1', '1-N', 'N-N'] // niente N-1: il lato "1" è sempre il primo
const CARD_HINT = { '1-1': 'oneToOne', '1-N': 'oneToMany', 'N-N': 'manyToMany' }

function draftOf() {
  if (props.relation) return JSON.parse(JSON.stringify(props.relation))
  const source = props.sourceId ?? m.value.entities[0]?.id
  const target = m.value.entities.find((e) => e.id !== source)?.id ?? source
  return { source_id: source, target_id: target, cardinality: '1-N', label: '', composition: 0, instances: 1 }
}
const r = ref(draftOf())

function swap() {
  ;[r.value.source_id, r.value.target_id] = [r.value.target_id, r.value.source_id]
}

async function save() {
  const body = { ...r.value }
  if (body.id) await api.put(`/relations/${body.id}`, body)
  else await api.post('/relations', { ...body, domain_id: m.value.id })
  await reloadModel()
  toast(t('model.relationSaved'))
  emit('close')
}
</script>

<template>
  <Modal wide :title="$t(r.id ? 'model.editRelation' : 'model.newRelation')" @close="emit('close')">
    <form class="space-y-4" @submit.prevent="save">
      <div class="grid grid-cols-[1fr_auto_1fr] items-end gap-3">
        <div>
          <label class="label">{{ $t('model.from') }}</label>
          <select v-model="r.source_id" class="input">
            <option v-for="e in m.entities" :key="e.id" :value="e.id">{{ e.name }}</option>
          </select>
        </div>
        <button type="button" class="icon-btn mb-1.5" :title="$t('common.swap')" @click="swap"><i class="fa-solid fa-right-left" /></button>
        <div>
          <label class="label">{{ $t('model.to') }}</label>
          <select v-model="r.target_id" class="input">
            <option v-for="e in m.entities" :key="e.id" :value="e.id">{{ e.name }}</option>
          </select>
        </div>
      </div>
      <div class="grid grid-cols-3 gap-3">
        <div class="col-span-2"><label class="label">{{ $t('model.label') }}</label><input v-model="r.label" class="input" :placeholder="$t('model.labelPlaceholder')" /></div>
        <div>
          <label class="label">{{ $t('model.cardinality') }}</label>
          <select v-model="r.cardinality" class="input"><option v-for="c in CARDINALITIES" :key="c">{{ c }}</option></select>
        </div>
      </div>
      <p class="text-xs text-zinc-500 -mt-1">
        {{ $t(`model.cardinalityHint.${CARD_HINT[r.cardinality] || 'oneToMany'}`, { a: ename(r.source_id), b: ename(r.target_id) }) }}
      </p>

      <div class="rounded-xl ring-1 ring-zinc-200 p-4 space-y-4" :class="r.composition && 'bg-zinc-50/60'">
        <label class="flex items-start gap-2.5 cursor-pointer">
          <input type="checkbox" class="accent-indigo-600 mt-1" :checked="!!r.composition"
                 @change="(ev) => (r.composition = ev.target.checked ? 1 : 0)" />
          <span>
            <span class="text-sm font-semibold flex items-center gap-1.5"><i class="fa-solid fa-cubes-stacked" /> {{ $t('model.composition') }}</span>
            <i18n-t scope="global" keypath="model.compositionHint" tag="span" class="block text-xs text-zinc-500">
              <template #child><b>{{ ename(r.target_id) }}</b></template>
              <template #parent><b>{{ ename(r.source_id) }}</b></template>
            </i18n-t>
          </span>
        </label>
        <template v-if="r.composition">
          <!-- spento / eliminato si decidono sugli stati di ciascuna entità, qui si vedono soltanto -->
          <div v-for="eid in [r.source_id, r.target_id]" :key="eid" class="text-xs space-y-1">
            <div class="flex items-center gap-2">
              <span class="font-medium text-zinc-700">{{ ename(eid) }}</span>
              <RouterLink :to="`/d/${m.id}/entity/${eid}`" class="text-indigo-600 hover:underline" @click="emit('close')">{{ $t('model.livenessEdit') }}</RouterLink>
            </div>
            <div v-for="[lv, ids] in [['off', offStates(entity(eid))], ['gone', goneStates(entity(eid))]]" :key="lv" class="flex flex-wrap items-center gap-1.5 pl-3">
              <span class="text-zinc-500 w-20">{{ $t(`state.liveness.${lv}`) }}</span>
              <StateChip v-for="sid in ids" :key="sid" :id="sid" :entity-id="eid" size="xs" />
              <span v-if="!ids.length" class="text-zinc-400">—</span>
            </div>
          </div>
          <p class="text-xs text-zinc-500">{{ $t('model.livenessRule', { parent: ename(r.source_id), child: ename(r.target_id) }) }}</p>
          <div class="flex items-center gap-3">
            <label class="label !mb-0">{{ $t('model.instancesLabel', { child: ename(r.target_id), parent: ename(r.source_id) }) }}</label>
            <select v-model.number="r.instances" class="input !w-20 !py-1.5">
              <option v-for="n in [1, 2, 3]" :key="n" :value="n">{{ n }}</option>
            </select>
          </div>
          <p v-if="r.instances > 1" class="text-xs text-amber-700 flex gap-1.5">
            <i class="fa-solid fa-triangle-exclamation mt-0.5" />
            {{ $t('model.instancesWarning') }}
          </p>
        </template>
      </div>

      <div class="flex justify-end"><button class="btn-accent">{{ $t('common.save') }}</button></div>
    </form>
  </Modal>
</template>
