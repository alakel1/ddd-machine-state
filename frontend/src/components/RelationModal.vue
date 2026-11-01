<script setup>
// Crea o modifica una relazione: usato dalla Panoramica e dalla scheda entità
import { computed, ref } from 'vue'
import { store, api, reloadModel, ename, entity, toast } from '../store'
import { t } from '../i18n'
import Modal from './Modal.vue'
import HelpLink from './HelpLink.vue'

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

const LIVENESS = { active: 'bg-emerald-600 text-white', off: 'bg-amber-500 text-white', gone: 'bg-rose-600 text-white' }
async function setLiveness(s, liveness) {
  await api.put(`/states/${s.id}`, { liveness })
  await reloadModel()
}

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
            <span class="text-sm font-semibold flex items-center gap-1.5"><i class="fa-solid fa-cubes-stacked" /> {{ $t('model.composition') }} <HelpLink topic="composition" /></span>
            <i18n-t scope="global" keypath="model.compositionHint" tag="span" class="block text-xs text-zinc-500">
              <template #child><b>{{ ename(r.target_id) }}</b></template>
              <template #parent><b>{{ ename(r.source_id) }}</b></template>
            </i18n-t>
          </span>
        </label>
        <template v-if="r.composition">
          <!-- spento / eliminato si decidono sugli stati di ciascuna entità: si cambiano qui, senza lasciare la relazione -->
          <p class="text-xs text-zinc-500">{{ $t('model.livenessInline') }}</p>
          <div v-for="eid in [...new Set([r.source_id, r.target_id])]" :key="eid" class="text-xs space-y-1">
            <div class="font-medium text-zinc-700">{{ ename(eid) }}</div>
            <div v-for="s in entity(eid)?.states || []" :key="s.id" class="flex items-center gap-2 pl-3">
              <span class="font-mono text-zinc-600 w-40 truncate">{{ s.name }}</span>
              <div class="inline-flex rounded-md bg-white ring-1 ring-inset ring-zinc-200 p-px">
                <button v-for="(cls, lv) in LIVENESS" :key="lv" type="button" @click="setLiveness(s, lv)"
                        class="rounded px-1.5 py-px text-[11px] font-medium whitespace-nowrap transition cursor-pointer"
                        :class="(s.liveness || 'active') === lv ? cls : 'text-zinc-400 hover:text-zinc-700'">{{ $t(`state.liveness.${lv}`) }}</button>
              </div>
            </div>
            <p v-if="!entity(eid)?.states.length" class="pl-3 text-zinc-400">
              {{ $t('model.noStates') }}
              <RouterLink :to="`/d/${m.id}/entity/${eid}`" class="text-indigo-600 hover:underline" @click="emit('close')">{{ $t('model.livenessEdit') }}</RouterLink>
            </p>
          </div>
          <p class="text-xs text-zinc-500">{{ $t('model.livenessRule', { parent: ename(r.source_id), child: ename(r.target_id) }) }}</p>
          <div class="flex items-center gap-3">
            <label class="label !mb-0">{{ $t('model.instancesLabel', { child: ename(r.target_id), parent: ename(r.source_id) }) }} <HelpLink topic="instances" /></label>
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
