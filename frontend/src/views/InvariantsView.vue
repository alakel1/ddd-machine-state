<script setup>
import { computed, ref } from 'vue'
import { store, api, reloadModel, ename, entity, toast } from '../store'
import { SEVERITY, pal } from '../colors'
import PageHeader from '../components/PageHeader.vue'
import Modal from '../components/Modal.vue'
import StateChip from '../components/StateChip.vue'
import StatePicker from '../components/StatePicker.vue'

const m = computed(() => store.model)
const draft = ref(null)

function open(inv) {
  draft.value = inv
    ? JSON.parse(JSON.stringify(inv))
    : { name: '', description: '', severity: 'error', rules: [newRule(0), newRule(1)].filter(Boolean) }
}
function newRule(i = 0) {
  const e = m.value.entities.filter((x) => x.states.length)[i]
  return e ? { entity_id: e.id, state_ids: [], negate: 0 } : null
}
async function save() {
  const d = draft.value
  const body = { ...d, domain_id: m.value.id, rules: d.rules.filter((r) => r.state_ids.length) }
  if (d.id) await api.put(`/invariants/${d.id}`, body)
  else await api.post('/invariants', body)
  draft.value = null
  await reloadModel()
  toast('Invariante salvata')
}
async function remove(inv) {
  if (!confirm(`Eliminare «${inv.name}»?`)) return
  await api.del(`/invariants/${inv.id}`)
  await reloadModel()
}
</script>

<template>
  <PageHeader eyebrow="Regole del dominio" title="Invarianti"
              subtitle="Combinazioni di stati che non devono mai esistere contemporaneamente. Il simulatore le segnala in tempo reale, l'analisi cerca se esiste un percorso che le raggiunge.">
    <template #actions>
      <button class="btn-accent" @click="open()"><i class="fa-solid fa-plus fa-fw text-[14px]" /> Invariante</button>
    </template>
  </PageHeader>

  <div class="p-8 space-y-3 max-w-5xl">
    <div v-if="!m.invariants.length" class="card p-12 text-center">
      <i class="fa-solid fa-shield-halved fa-fw text-[28px] text-zinc-300 mx-auto" />
      <p class="mt-3 text-sm text-zinc-500">Nessuna invariante. Esempio: «un sinistro LIQUIDATO non può avere una perizia IN_CORSO».</p>
      <button class="btn-accent mt-4" @click="open()"><i class="fa-solid fa-plus fa-fw text-[14px]" /> Crea la prima</button>
    </div>

    <div v-if="m.derived_invariants?.length" class="pt-4">
      <div class="eyebrow mb-2">Ricavate dalle composizioni (sola lettura)</div>
      <div v-for="inv in m.derived_invariants" :key="inv.id" class="card p-5 mb-3 !bg-zinc-50/60">
        <div class="flex items-center gap-2">
          <i class="fa-solid fa-cubes-stacked text-zinc-500" />
          <h3 class="font-semibold tracking-tight">{{ inv.name }}</h3>
          <span class="tag" :class="SEVERITY.error.cls">{{ SEVERITY.error.label }}</span>
        </div>
        <p class="text-sm text-zinc-500 mt-0.5">{{ inv.description }} Si modifica dalla relazione, nella Panoramica.</p>
        <div class="mt-3 flex flex-wrap items-center gap-2 text-sm">
          <span class="text-xs font-semibold text-rose-600 uppercase tracking-wide">Vietato quando</span>
          <template v-for="(r, i) in inv.rules" :key="i">
            <span v-if="i" class="text-xs font-semibold text-zinc-400">E</span>
            <span class="inline-flex flex-wrap items-center gap-1.5 rounded-lg bg-white ring-1 ring-zinc-200 px-2 py-1">
              <span class="size-1.5 rounded-full" :class="pal(entity(r.entity_id)?.color).dot" />
              <span class="font-medium text-xs">{{ ename(r.entity_id) }}</span>
              <span class="text-xs text-zinc-400 font-mono">∈</span>
              <StateChip v-for="sid in r.state_ids" :key="sid" :id="sid" :entity-id="r.entity_id" size="xs" />
            </span>
          </template>
        </div>
      </div>
    </div>

    <div v-for="inv in m.invariants" :key="inv.id" class="card p-5 group">
      <div class="flex items-start gap-3">
        <span class="mt-1.5 size-2 rounded-full shrink-0" :class="SEVERITY[inv.severity]?.dot" />
        <div class="flex-1 min-w-0">
          <div class="flex items-center gap-2">
            <h3 class="font-semibold tracking-tight">{{ inv.name }}</h3>
            <span class="tag" :class="SEVERITY[inv.severity]?.cls">{{ SEVERITY[inv.severity]?.label }}</span>
          </div>
          <p v-if="inv.description" class="text-sm text-zinc-500 mt-0.5">{{ inv.description }}</p>
          <div class="mt-3 flex flex-wrap items-center gap-2 text-sm">
            <span class="text-xs font-semibold text-rose-600 uppercase tracking-wide">Vietato quando</span>
            <template v-for="(r, i) in inv.rules" :key="i">
              <span v-if="i" class="text-xs font-semibold text-zinc-400">E</span>
              <span class="inline-flex items-center gap-1.5 rounded-lg bg-zinc-50 ring-1 ring-zinc-200 px-2 py-1">
                <span class="size-1.5 rounded-full" :class="pal(entity(r.entity_id)?.color).dot" />
                <span class="font-medium text-xs">{{ ename(r.entity_id) }}</span>
                <span class="text-xs text-zinc-400 font-mono">{{ r.negate ? '∉' : '∈' }}</span>
                <StateChip v-for="sid in r.state_ids" :key="sid" :id="sid" size="xs" />
              </span>
            </template>
          </div>
        </div>
        <div class="flex gap-0.5 opacity-0 group-hover:opacity-100 transition">
          <button class="icon-btn" @click="open(inv)"><i class="fa-solid fa-pen fa-fw text-[14px]" /></button>
          <button class="icon-btn hover:!text-rose-600" @click="remove(inv)"><i class="fa-solid fa-trash-can fa-fw text-[14px]" /></button>
        </div>
      </div>
    </div>
  </div>

  <Modal v-if="draft" wide :title="draft.id ? 'Modifica invariante' : 'Nuova invariante'" @close="draft = null">
    <form id="iform" class="space-y-5" @submit.prevent="save">
      <div class="grid grid-cols-3 gap-3">
        <div class="col-span-2"><label class="label">Nome</label><input v-model="draft.name" class="input" required placeholder="es. Liquidazione con perizia aperta" /></div>
        <div>
          <label class="label">Gravità</label>
          <select v-model="draft.severity" class="input"><option value="error">Errore</option><option value="warning">Attenzione</option></select>
        </div>
      </div>
      <div><label class="label">Descrizione</label><input v-model="draft.description" class="input" placeholder="Perché è vietato" /></div>
      <div>
        <div class="flex items-center justify-between mb-2">
          <div class="text-sm font-semibold">Condizioni <span class="font-normal text-zinc-500">(tutte vere ⇒ violazione)</span></div>
          <button type="button" class="btn-ghost !text-xs" @click="draft.rules.push(newRule())"><i class="fa-solid fa-plus fa-fw text-[12px]" /> condizione</button>
        </div>
        <div class="space-y-2">
          <div v-for="(r, i) in draft.rules" :key="i" class="rounded-xl ring-1 ring-zinc-200 p-3 bg-zinc-50/50 space-y-2.5">
            <div class="flex items-center gap-2">
              <span v-if="i" class="text-xs font-semibold text-zinc-400 w-5">E</span>
              <select v-model="r.entity_id" class="input !w-48 !py-1.5" @change="r.state_ids = []">
                <option v-for="x in m.entities.filter((x) => x.states.length)" :key="x.id" :value="x.id">{{ x.name }}</option>
              </select>
              <select v-model="r.negate" class="input !w-44 !py-1.5">
                <option :value="0">è in uno di</option>
                <option :value="1">non è in nessuno di</option>
              </select>
              <div class="flex-1" />
              <button type="button" class="icon-btn hover:!text-rose-600" @click="draft.rules.splice(i, 1)"><i class="fa-solid fa-xmark fa-fw text-[14px]" /></button>
            </div>
            <StatePicker v-model="r.state_ids" :entity-id="r.entity_id" />
          </div>
        </div>
      </div>
    </form>
    <template #footer>
      <button class="btn-ghost" @click="draft = null">Annulla</button>
      <button class="btn-accent" form="iform" :disabled="!draft.name || !draft.rules.some((r) => r.state_ids.length)">Salva</button>
    </template>
  </Modal>
</template>
