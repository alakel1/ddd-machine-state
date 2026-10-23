<script setup>
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { store, api, reloadModel, loadDomains, ename, entity, toast, suggestChildGone } from '../store'
import { pal, KINDS, COLORS } from '../colors'
import { contextMap } from '../diagrams'
import PageHeader from '../components/PageHeader.vue'
import Modal from '../components/Modal.vue'
import Mermaid from '../components/Mermaid.vue'
import StateChip from '../components/StateChip.vue'
import StatePicker from '../components/StatePicker.vue'

const router = useRouter()
const m = computed(() => store.model)
const showDeps = ref(true)
const diagram = computed(() => contextMap(m.value, { dependencies: showDeps.value }))
const editingDomain = ref(null)
const relation = ref(null)

const stats = computed(() => {
  const es = m.value.entities
  const ts = es.flatMap((e) => e.transitions)
  return [
    { label: 'Entità', value: es.length },
    { label: 'Stati', value: es.reduce((n, e) => n + e.states.length, 0) },
    { label: 'Azioni', value: ts.length },
    { label: 'Guardie tra entità', value: ts.reduce((n, t) => n + t.guards.filter((g) => g.kind === 'state').length, 0) },
    { label: 'Effetti a cascata', value: ts.reduce((n, t) => n + t.effects.length, 0) },
    { label: 'Invarianti', value: m.value.invariants.length },
  ]
})

const contexts = computed(() => {
  const g = {}
  for (const e of m.value.entities) (g[e.context || 'Senza contesto'] ||= []).push(e)
  return g
})

async function saveDomain() {
  await api.put(`/domains/${m.value.id}`, editingDomain.value)
  editingDomain.value = null
  await Promise.all([reloadModel(), loadDomains()])
}

async function addEntity() {
  const e = await api.post('/entities', {
    domain_id: m.value.id, name: 'NuovaEntità', pos: m.value.entities.length,
    color: COLORS[m.value.entities.length % COLORS.length],
  })
  await reloadModel()
  router.push(`/d/${m.value.id}/entity/${e.id}`)
}

function newRelation() {
  return {
    source_id: m.value.entities[0]?.id, target_id: m.value.entities[1]?.id, cardinality: '1-N', label: '',
    composition: 0, parent_gone_states: [0], child_gone_states: [], instances: 1,
  }
}
function openRelation(r) {
  relation.value = r ? JSON.parse(JSON.stringify(r)) : newRelation()
  if (!relation.value.child_gone_states?.length) relation.value.child_gone_states = suggestChildGone(entity(relation.value.target_id))
}
function swapRelation() {
  const r = relation.value
  ;[r.source_id, r.target_id] = [r.target_id, r.source_id]
  r.parent_gone_states = [0]
  r.child_gone_states = suggestChildGone(entity(r.target_id))
}

async function saveRelation() {
  const r = relation.value
  if (r.id) await api.put(`/relations/${r.id}`, r)
  else await api.post('/relations', { ...r, domain_id: m.value.id })
  relation.value = null
  await reloadModel()
  toast('Relazione salvata')
}

async function removeRelation(r) {
  await api.del(`/relations/${r.id}`)
  await reloadModel()
}
</script>

<template>
  <PageHeader eyebrow="Panoramica del dominio" :subtitle="m.description">
    <template #title>
      <h1 class="text-2xl font-semibold tracking-tight text-zinc-900 flex items-center gap-2">
        {{ m.name }}
        <button class="icon-btn" @click="editingDomain = { name: m.name, description: m.description }"><i class="fa-solid fa-pen fa-fw text-[14px]" /></button>
      </h1>
    </template>
    <template #actions>
      <button class="btn-outline" @click="openRelation()">
        <i class="fa-solid fa-code-branch fa-fw text-[14px]" /> Relazione
      </button>
      <button class="btn-accent" @click="addEntity"><i class="fa-solid fa-plus fa-fw text-[14px]" /> Entità</button>
    </template>
  </PageHeader>

  <div class="p-8 space-y-8">
    <!-- KPI -->
    <div class="grid grid-cols-3 lg:grid-cols-6 gap-3">
      <div v-for="s in stats" :key="s.label" class="card px-4 py-3.5">
        <div class="text-2xl font-semibold tabular-nums tracking-tight text-zinc-900">{{ s.value }}</div>
        <div class="text-xs text-zinc-500 mt-0.5">{{ s.label }}</div>
      </div>
    </div>

    <!-- Context map -->
    <section class="card overflow-hidden">
      <div class="flex items-center justify-between px-6 py-4 border-b border-zinc-100">
        <div class="flex items-center gap-2.5">
          <i class="fa-solid fa-diagram-project fa-fw text-[14px] text-zinc-400" />
          <h2 class="font-semibold tracking-tight">Context map</h2>
        </div>
        <div class="flex items-center gap-4 text-xs text-zinc-500">
          <span class="flex items-center gap-1.5"><span class="w-5 border-t border-zinc-500" /> relazione</span>
          <span class="flex items-center gap-1.5"><span class="w-5 border-t border-dashed border-zinc-400" /> dipende da (guardia)</span>
          <span class="flex items-center gap-1.5"><span class="w-5 border-t-2 border-amber-500" /> effetto a cascata</span>
          <label class="flex items-center gap-2 cursor-pointer pl-3 border-l border-zinc-200">
            <input v-model="showDeps" type="checkbox" class="accent-indigo-600" /> dipendenze di stato
          </label>
        </div>
      </div>
      <div class="p-6 bg-[radial-gradient(circle_at_1px_1px,#e4e4e7_1px,transparent_0)] [background-size:18px_18px]">
        <Mermaid :code="diagram" />
      </div>
    </section>

    <!-- Entità per bounded context -->
    <section v-for="(ents, ctx) in contexts" :key="ctx">
      <div class="flex items-center gap-3 mb-3">
        <h2 class="eyebrow !text-zinc-500">{{ ctx }}</h2>
        <div class="flex-1 border-t border-zinc-200" />
      </div>
      <div class="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        <RouterLink v-for="e in ents" :key="e.id" :to="`/d/${m.id}/entity/${e.id}`"
                    class="card relative overflow-hidden p-5 hover:shadow-md hover:ring-zinc-300 transition group">
          <div class="absolute inset-x-0 top-0 h-1 bg-gradient-to-r" :class="pal(e.color).bar" />
          <div class="flex items-center justify-between">
            <div class="flex items-center gap-2">
              <span class="font-semibold tracking-tight text-zinc-900">{{ e.name }}</span>
              <span class="tag" :class="pal(e.color).soft">{{ KINDS[e.kind] }}</span>
            </div>
            <i class="fa-solid fa-arrow-right fa-fw text-[14px] text-zinc-300 group-hover:text-zinc-600 group-hover:translate-x-0.5 transition" />
          </div>
          <p class="text-sm text-zinc-500 mt-1 line-clamp-2 min-h-10">{{ e.description || '—' }}</p>
          <div class="flex flex-wrap gap-1 mt-3">
            <StateChip v-for="s in e.states" :key="s.id" :id="s.id" size="xs" />
            <span v-if="!e.states.length" class="text-xs text-zinc-400 italic">nessuno stato definito</span>
          </div>
          <div class="flex gap-4 mt-4 pt-3 border-t border-zinc-100 text-xs text-zinc-500">
            <span>{{ e.attributes.length }} attributi</span>
            <span>{{ e.transitions.length }} azioni</span>
            <span class="flex items-center gap-1"><i class="fa-solid fa-lock fa-fw text-[11px]" />{{ e.transitions.reduce((n, t) => n + t.guards.length, 0) }}</span>
            <span class="flex items-center gap-1"><i class="fa-solid fa-bolt fa-fw text-[11px]" />{{ e.transitions.reduce((n, t) => n + t.effects.length, 0) }}</span>
          </div>
        </RouterLink>
      </div>
    </section>

    <!-- Relazioni -->
    <section class="card">
      <div class="flex items-center justify-between px-6 py-4 border-b border-zinc-100">
        <h2 class="font-semibold tracking-tight">Relazioni</h2>
        <button class="btn-ghost" @click="openRelation()">
          <i class="fa-solid fa-plus fa-fw text-[14px]" /> Aggiungi
        </button>
      </div>
      <div v-if="!m.relations.length" class="px-6 py-8 text-sm text-zinc-400 text-center">Nessuna relazione</div>
      <div v-for="r in m.relations" :key="r.id" class="flex items-center gap-3 px-6 py-3 border-b border-zinc-100 last:border-0 group">
        <span class="font-medium text-sm">{{ ename(r.source_id) }}</span>
        <span class="text-sm text-zinc-500 italic">{{ r.label }}</span>
        <i class="fa-solid fa-arrow-right fa-fw text-[14px] text-zinc-300" />
        <span class="font-medium text-sm">{{ ename(r.target_id) }}</span>
        <span class="tag bg-zinc-50 text-zinc-600 ring-zinc-200 font-mono">{{ r.cardinality }}</span>
        <span v-if="r.composition" class="tag bg-zinc-800 text-white ring-zinc-800" :title="`${ename(r.target_id)} non esiste senza ${ename(r.source_id)}`">
          <i class="fa-solid fa-cubes-stacked" /> composizione
        </span>
        <span v-if="r.composition && r.instances > 1" class="tag bg-indigo-50 text-indigo-700 ring-indigo-200" title="Figli simulati per padre nell'analisi">
          <i class="fa-solid fa-clone" /> {{ r.instances }} istanze
        </span>
        <div class="flex-1" />
        <button class="icon-btn opacity-0 group-hover:opacity-100" @click="openRelation(r)"><i class="fa-solid fa-pen fa-fw text-[12px]" /></button>
        <button class="icon-btn opacity-0 group-hover:opacity-100 hover:!text-rose-600" @click="removeRelation(r)"><i class="fa-solid fa-trash-can fa-fw text-[12px]" /></button>
      </div>
    </section>
  </div>

  <Modal v-if="editingDomain" title="Modifica dominio" @close="editingDomain = null">
    <form class="space-y-4" @submit.prevent="saveDomain">
      <div><label class="label">Nome</label><input v-model="editingDomain.name" class="input" required /></div>
      <div><label class="label">Descrizione</label><textarea v-model="editingDomain.description" rows="4" class="input" /></div>
      <div class="flex justify-end"><button class="btn-accent">Salva</button></div>
    </form>
  </Modal>

  <Modal v-if="relation" wide :title="relation.id ? 'Modifica relazione' : 'Nuova relazione'" @close="relation = null">
    <form class="space-y-4" @submit.prevent="saveRelation">
      <div class="grid grid-cols-[1fr_auto_1fr] items-end gap-3">
        <div>
          <label class="label">Da</label>
          <select v-model="relation.source_id" class="input" @change="relation.parent_gone_states = [0]">
            <option v-for="e in m.entities" :key="e.id" :value="e.id">{{ e.name }}</option>
          </select>
        </div>
        <button type="button" class="icon-btn mb-1.5" title="Inverti" @click="swapRelation"><i class="fa-solid fa-right-left" /></button>
        <div>
          <label class="label">A</label>
          <select v-model="relation.target_id" class="input" @change="relation.child_gone_states = suggestChildGone(entity(relation.target_id))">
            <option v-for="e in m.entities" :key="e.id" :value="e.id">{{ e.name }}</option>
          </select>
        </div>
      </div>
      <div class="grid grid-cols-3 gap-3">
        <div class="col-span-2"><label class="label">Etichetta</label><input v-model="relation.label" class="input" placeholder="es. contiene" /></div>
        <div>
          <label class="label">Cardinalità</label>
          <select v-model="relation.cardinality" class="input"><option v-for="c in ['1-1', '1-N', 'N-1', 'N-N']" :key="c">{{ c }}</option></select>
        </div>
      </div>

      <div class="rounded-xl ring-1 ring-zinc-200 p-4 space-y-4" :class="relation.composition && 'bg-zinc-50/60'">
        <label class="flex items-start gap-2.5 cursor-pointer">
          <input type="checkbox" class="accent-indigo-600 mt-1" :checked="!!relation.composition"
                 @change="(ev) => (relation.composition = ev.target.checked ? 1 : 0)" />
          <span>
            <span class="text-sm font-semibold flex items-center gap-1.5"><i class="fa-solid fa-cubes-stacked" /> Composizione</span>
            <span class="block text-xs text-zinc-500">
              <b>{{ ename(relation.target_id) }}</b> non esiste senza <b>{{ ename(relation.source_id) }}</b>.
              Il tool controlla che la creazione lo richieda, che l'uscita di scena del padre trascini i figli
              e che nessuna combinazione raggiungibile lasci un figlio vivo senza padre.
            </span>
          </span>
        </label>
        <template v-if="relation.composition">
          <div>
            <label class="label">{{ ename(relation.source_id) }} è assente quando è in</label>
            <StatePicker v-model="relation.parent_gone_states" :entity-id="relation.source_id" />
          </div>
          <div>
            <label class="label">{{ ename(relation.target_id) }} non c'è più quando è in <span class="font-normal">(gli altri stati contano come "vivo")</span></label>
            <StatePicker v-model="relation.child_gone_states" :entity-id="relation.target_id" />
          </div>
          <div class="flex items-center gap-3">
            <label class="label !mb-0">Istanze di {{ ename(relation.target_id) }} simulate per ogni {{ ename(relation.source_id) }}</label>
            <select v-model.number="relation.instances" class="input !w-20 !py-1.5">
              <option v-for="n in [1, 2, 3]" :key="n" :value="n">{{ n }}</option>
            </select>
          </div>
          <p v-if="relation.instances > 1" class="text-xs text-amber-700 flex gap-1.5">
            <i class="fa-solid fa-triangle-exclamation mt-0.5" />
            Vale solo per l'analisi (il simulatore resta a un'istanza). Trova casi come "un figlio confermato e l'altro eliminato",
            ma le combinazioni da esplorare crescono molto: se l'analisi risulta troncata, i risultati sono parziali.
          </p>
        </template>
      </div>

      <div class="flex justify-end"><button class="btn-accent">Salva</button></div>
    </form>
  </Modal>
</template>
