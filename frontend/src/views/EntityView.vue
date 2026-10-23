<script setup>
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { store, api, reloadModel, entity, ename, sname, toast, isCreatable } from '../store'
import { pal, KINDS, COLORS, PALETTE } from '../colors'
import { stateDiagram } from '../diagrams'
import PageHeader from '../components/PageHeader.vue'
import Modal from '../components/Modal.vue'
import Mermaid from '../components/Mermaid.vue'
import StateChip from '../components/StateChip.vue'
import StatePicker from '../components/StatePicker.vue'

const route = useRoute()
const router = useRouter()
const e = computed(() => entity(Number(route.params.entityId)))
const m = computed(() => store.model)
const others = computed(() => m.value.entities.filter((x) => x.id !== e.value?.id))
const diagram = computed(() => stateDiagram(e.value))

const meta = ref(null)
const newState = ref('')
const newAttr = ref('')
const draft = ref(null)

// ---------- entità ----------
async function saveMeta() {
  await api.put(`/entities/${e.value.id}`, meta.value)
  meta.value = null
  await reloadModel()
}
async function removeEntity() {
  if (!confirm(`Eliminare l'entità «${e.value.name}» con stati, azioni e regole collegate?`)) return
  const id = m.value.id
  await api.del(`/entities/${e.value.id}`)
  await reloadModel()
  router.push(`/d/${id}/model`)
}

// ---------- stati ----------
async function addState() {
  const name = newState.value.trim().toUpperCase().replace(/\s+/g, '_')
  if (!name) return
  await api.post('/states', {
    entity_id: e.value.id, name, pos: e.value.states.length, is_initial: e.value.states.length ? 0 : 1,
  })
  newState.value = ''
  await reloadModel()
}
async function patchState(s, patch) {
  await api.put(`/states/${s.id}`, patch)
  await reloadModel()
}
async function removeState(s) {
  // tutto ciò che verrebbe eliminato o resterebbe orfano, anche nelle altre entità
  const all = m.value.entities.flatMap((x) => x.transitions.map((t) => ({ x, t })))
  const lost = [
    ...e.value.transitions.filter((t) => t.from_state_id === s.id || t.to_state_id === s.id).map((t) => `azione «${t.action}»`),
    ...all.flatMap(({ x, t }) => t.effects.filter((f) => f.to_state_id === s.id).map(() => `effetto di «${t.action}» (${x.name})`)),
    ...all.flatMap(({ x, t }) => t.guards.filter((g) => g.state_ids?.includes(s.id)).map(() => `guardia di «${t.action}» (${x.name})`)),
    ...m.value.invariants.filter((i) => i.rules.some((r) => r.state_ids.includes(s.id))).map((i) => `invariante «${i.name}»`),
  ]
  if (lost.length && !confirm(`Eliminando «${s.name}» si perdono o vanno riviste:\n\n- ${lost.join('\n- ')}\n\nProcedere?`)) return
  await api.del(`/states/${s.id}`)
  await reloadModel()
}
async function moveState(s, dir) {
  const list = [...e.value.states]
  const i = list.indexOf(s)
  const j = i + dir
  if (j < 0 || j >= list.length) return
  ;[list[i], list[j]] = [list[j], list[i]]
  await Promise.all(list.map((x, k) => api.put(`/states/${x.id}`, { pos: k })))
  await reloadModel()
}

// ---------- attributi ----------
const TYPES = ['string', 'text', 'int', 'decimal', 'bool', 'date', 'datetime', 'enum', 'ref']
async function addAttr() {
  if (!newAttr.value.trim()) return
  await api.post('/attributes', { entity_id: e.value.id, name: newAttr.value.trim() })
  newAttr.value = ''
  await reloadModel()
}
async function patchAttr(a, patch) {
  await api.put(`/attributes/${a.id}`, patch)
  await reloadModel()
}
async function removeAttr(a) {
  await api.del(`/attributes/${a.id}`)
  await reloadModel()
}

// ---------- azioni (transizioni) ----------
const creations = computed(() => e.value.transitions.filter((t) => t.kind === 'create'))
const evolutions = computed(() => e.value.transitions.filter((t) => t.kind !== 'create'))
const creatable = computed(() => creations.value.length > 0)
const birthStates = computed(() => new Set(creations.value.map((t) => t.to_state_id)))
const actionGroups = computed(() => [
  { key: 'create', title: 'Come nasce', hint: "Azioni di creazione: ogni modo in cui l'entità viene al mondo, con i suoi controlli.", list: creations.value },
  { key: 'normal', title: 'Come evolve', hint: 'Transizioni di stato con guardie (pre-condizioni su altre entità) ed effetti a cascata.', list: evolutions.value },
])

function openTransition(t, kind = 'normal') {
  draft.value = t
    ? JSON.parse(JSON.stringify(t))
    : {
        kind,
        from_state_id: kind === 'create' ? null : e.value.states[0]?.id ?? null,
        to_state_id: kind === 'create' ? e.value.states[0]?.id : e.value.states[1]?.id ?? e.value.states[0]?.id,
        action: '', actor: '', description: '', cascade_only: 0, guards: [], effects: [],
      }
}
// selettore "Da stato": 'create' = nasce da "non esiste", null = qualsiasi, altrimenti id stato
const fromSel = computed({
  get: () => (draft.value.kind === 'create' ? 'create' : draft.value.from_state_id),
  set: (v) => {
    draft.value.kind = v === 'create' ? 'create' : 'normal'
    draft.value.from_state_id = v === 'create' ? null : v
  },
})
function addGuard(kind) {
  draft.value.guards.push(kind === 'rule'
    ? { kind: 'rule', description: '', state_ids: [], negate: 0, entity_id: null }
    : { kind: 'state', entity_id: others.value[0]?.id ?? e.value.id, state_ids: [], negate: 0, description: '' })
}
function addEffect() {
  const target = others.value[0]
  draft.value.effects.push({ entity_id: target?.id, kind: 'state', to_state_id: target?.states[0]?.id, action: '', skip_final: 0, description: '' })
}
function resetEffect(f) {
  f.kind = 'state'
  f.to_state_id = entity(f.entity_id)?.states[0]?.id
  f.action = ''
}
// nomi delle azioni (non di creazione) che un effetto può far eseguire all'entità
const actionsOf = (eid) => [...new Set((entity(eid)?.transitions || []).filter((t) => t.kind !== 'create').map((t) => t.action))]
const effectText = (f) =>
  (f.kind === 'create' ? 'nasce' : f.kind === 'action' ? `esegue «${f.action}»` : sname(f.to_state_id)) +
  (f.skip_final ? ' (solo istanze attive)' : '')
async function saveTransition() {
  const d = draft.value
  const body = {
    entity_id: e.value.id, kind: d.kind || 'normal', cascade_only: d.cascade_only ? 1 : 0,
    from_state_id: d.from_state_id, to_state_id: d.to_state_id,
    action: d.action.trim(), actor: d.actor, description: d.description,
  }
  const t = d.id ? await api.put(`/transitions/${d.id}`, body) : await api.post('/transitions', body)
  const orig = d.id ? e.value.transitions.find((x) => x.id === d.id) : { guards: [], effects: [] }
  const sync = async (table, oldList, newList) => {
    const keep = new Set(newList.filter((x) => x.id).map((x) => x.id))
    await Promise.all([
      ...oldList.filter((x) => !keep.has(x.id)).map((x) => api.del(`/${table}/${x.id}`)),
      ...newList.map((x) => (x.id ? api.put(`/${table}/${x.id}`, x) : api.post(`/${table}`, { ...x, transition_id: t.id }))),
    ])
  }
  await sync('guards', orig.guards, d.guards)
  for (const f of d.effects) {
    if (f.kind !== 'state') f.to_state_id = null
    if (f.kind !== 'action') f.action = ''
  }
  await sync('effects', orig.effects, d.effects)
  draft.value = null
  await reloadModel()
  toast('Azione salvata')
}
async function removeTransition(t) {
  if (!confirm(`Eliminare l'azione «${t.action}»?`)) return
  await api.del(`/transitions/${t.id}`)
  await reloadModel()
}

// ---------- dipendenze in ingresso ----------
const incoming = computed(() => {
  const out = []
  for (const o of others.value)
    for (const t of o.transitions) {
      for (const g of t.guards)
        if (g.kind === 'state' && g.entity_id === e.value.id) out.push({ kind: 'guard', entity: o, t, g })
      for (const f of t.effects) if (f.entity_id === e.value.id) out.push({ kind: 'effect', entity: o, t, f })
    }
  return out
})

// ---------- matrice stati × azioni ----------
const actionNames = computed(() => [...new Set(e.value.transitions.map((t) => t.action))])
const matrixRows = computed(() => (creatable.value ? [{ id: 0 }, ...e.value.states] : e.value.states))
function cell(stateId, action) {
  if (stateId === 0) return creations.value.filter((t) => t.action === action)
  const s = e.value.states.find((x) => x.id === stateId)
  return evolutions.value.filter(
    (t) => t.action === action && (t.from_state_id === stateId || (t.from_state_id == null && !s.is_final && t.to_state_id !== stateId)),
  )
}

const guardText = (g) =>
  g.kind === 'rule' ? g.description : `${ename(g.entity_id)} ${g.negate ? '∉' : '∈'} {${g.state_ids.map(sname).join(', ')}}`
</script>

<template>
  <div v-if="e">
    <PageHeader :eyebrow="`${KINDS[e.kind]} · ${e.context || 'senza contesto'}`" :subtitle="e.description">
      <template #title>
        <h1 class="text-2xl font-semibold tracking-tight text-zinc-900 flex items-center gap-3">
          <span class="size-3 rounded-full" :class="pal(e.color).dot" />
          {{ e.name }}
          <button class="icon-btn" @click="meta = { name: e.name, kind: e.kind, context: e.context, color: e.color, description: e.description }">
            <i class="fa-solid fa-pen fa-fw text-[14px]" />
          </button>
        </h1>
      </template>
      <template #actions>
        <button class="btn-danger" @click="removeEntity"><i class="fa-solid fa-trash-can fa-fw text-[14px]" /> Elimina</button>
        <button class="btn-accent" :disabled="!e.states.length" @click="openTransition()"><i class="fa-solid fa-plus fa-fw text-[14px]" /> Azione</button>
      </template>
    </PageHeader>

    <div class="p-8 grid gap-6 xl:grid-cols-5">
      <div class="xl:col-span-3 space-y-6">
        <!-- STATI -->
        <section class="card">
          <div class="flex items-center justify-between px-6 py-4 border-b border-zinc-100">
            <div>
              <h2 class="font-semibold tracking-tight">Stati</h2>
              <p class="text-xs text-zinc-500">
                Ciclo di vita dell'entità. I finali chiudono il ciclo.
                <template v-if="creatable">Gli stati di nascita sono quelli raggiunti dalle azioni di creazione.</template>
                <template v-else>Uno stato iniziale, oppure più modi di nascere con le azioni di creazione.</template>
              </p>
            </div>
          </div>
          <div class="divide-y divide-zinc-100">
            <div v-for="(s, i) in e.states" :key="s.id" class="flex items-center gap-3 px-6 py-2.5 group">
              <div class="flex flex-col -my-1 opacity-0 group-hover:opacity-100 transition">
                <button class="text-zinc-300 hover:text-zinc-700 text-[10px] leading-none cursor-pointer" :disabled="i === 0" @click="moveState(s, -1)"><i class="fa-solid fa-chevron-up" /></button>
                <button class="text-zinc-300 hover:text-zinc-700 text-[10px] leading-none cursor-pointer" :disabled="i === e.states.length - 1" @click="moveState(s, 1)"><i class="fa-solid fa-chevron-down" /></button>
              </div>
              <input :value="s.name" @change="(ev) => patchState(s, { name: ev.target.value.trim() })"
                     class="font-mono text-sm font-semibold w-44 bg-transparent rounded px-1.5 py-1 -mx-1.5 hover:bg-zinc-50 focus:bg-white focus:outline-none focus:ring-2 focus:ring-indigo-500/50" />
              <input :value="s.description" placeholder="descrizione…" @change="(ev) => patchState(s, { description: ev.target.value })"
                     class="flex-1 text-sm text-zinc-500 bg-transparent rounded px-1.5 py-1 hover:bg-zinc-50 focus:bg-white focus:outline-none focus:ring-2 focus:ring-indigo-500/50 placeholder:text-zinc-300" />
              <span v-if="creatable" class="tag" :class="birthStates.has(s.id) ? 'bg-indigo-50 text-indigo-700 ring-indigo-200' : 'invisible'">
                <i class="fa-solid fa-seedling" /> nasce qui
              </span>
              <button v-else class="tag cursor-pointer transition" @click="patchState(s, { is_initial: s.is_initial ? 0 : 1 })"
                      :class="s.is_initial ? 'bg-indigo-600 text-white ring-indigo-600' : 'bg-white text-zinc-400 ring-zinc-200 hover:text-zinc-700'">iniziale</button>
              <button class="tag cursor-pointer transition" @click="patchState(s, { is_final: s.is_final ? 0 : 1 })"
                      :class="s.is_final ? 'bg-zinc-800 text-white ring-zinc-800' : 'bg-white text-zinc-400 ring-zinc-200 hover:text-zinc-700'">finale</button>
              <button class="icon-btn opacity-0 group-hover:opacity-100 hover:!text-rose-600" @click="removeState(s)"><i class="fa-solid fa-trash-can fa-fw text-[12px]" /></button>
            </div>
          </div>
          <form class="flex items-center gap-2 px-6 py-3 bg-zinc-50/60 rounded-b-2xl border-t border-zinc-100" @submit.prevent="addState">
            <i class="fa-solid fa-plus fa-fw text-[14px] text-zinc-400" />
            <input v-model="newState" placeholder="Nuovo stato (es. IN_LAVORAZIONE) e invio"
                   class="flex-1 bg-transparent font-mono text-sm focus:outline-none placeholder:font-sans placeholder:text-zinc-400" />
            <i class="fa-solid fa-arrow-turn-down fa-fw text-[12px] text-zinc-300" />
          </form>
        </section>

        <!-- AZIONI -->
        <section v-for="grp in actionGroups" :key="grp.key" class="card">
          <div class="flex items-center justify-between px-6 py-4 border-b border-zinc-100">
            <div>
              <h2 class="font-semibold tracking-tight flex items-center gap-2">
                <i class="fa-solid fa-fw text-[13px] text-zinc-400" :class="grp.key === 'create' ? 'fa-seedling' : 'fa-shuffle'" />{{ grp.title }}
              </h2>
              <p class="text-xs text-zinc-500">{{ grp.hint }}</p>
            </div>
            <button class="btn-ghost" :disabled="!e.states.length" @click="openTransition(null, grp.key)">
              <i class="fa-solid fa-plus fa-fw text-[14px]" /> {{ grp.key === 'create' ? 'Creazione' : 'Azione' }}
            </button>
          </div>
          <div v-if="!grp.list.length" class="px-6 py-6 text-center text-sm text-zinc-400">
            <template v-if="!e.states.length">Prima definisci gli stati.</template>
            <template v-else-if="grp.key === 'create'">
              Nessuna azione di creazione: l'entità parte dallo stato iniziale. Aggiungine una o più se può nascere in modi diversi.
            </template>
            <template v-else>Nessuna azione: definisci come l'entità cambia stato.</template>
          </div>
          <div class="divide-y divide-zinc-100">
            <div v-for="t in grp.list" :key="t.id" class="px-6 py-3.5 group hover:bg-zinc-50/50 transition">
              <div class="flex items-center gap-2.5 flex-wrap">
                <StateChip :id="t.kind === 'create' ? 0 : t.from_state_id" />
                <i class="fa-solid fa-arrow-right fa-fw text-[12px] text-zinc-300" />
                <span class="font-semibold text-sm text-zinc-900">{{ t.action }}</span>
                <i class="fa-solid fa-arrow-right fa-fw text-[12px] text-zinc-300" />
                <StateChip :id="t.to_state_id" />
                <span v-if="t.actor" class="tag bg-zinc-50 text-zinc-500 ring-zinc-200"><i class="fa-solid fa-user fa-fw text-[11px]" />{{ t.actor }}</span>
                <span v-if="t.cascade_only" class="tag bg-amber-50 text-amber-800 ring-amber-200" title="Non si esegue direttamente: avviene solo come effetto di un'altra azione">
                  <i class="fa-solid fa-arrows-turn-right" /> solo in cascata
                </span>
                <div class="flex-1" />
                <button class="icon-btn opacity-0 group-hover:opacity-100" @click="openTransition(t)"><i class="fa-solid fa-pen fa-fw text-[12px]" /></button>
                <button class="icon-btn opacity-0 group-hover:opacity-100 hover:!text-rose-600" @click="removeTransition(t)"><i class="fa-solid fa-trash-can fa-fw text-[12px]" /></button>
              </div>
              <p v-if="t.description" class="text-xs text-zinc-500 mt-1.5">{{ t.description }}</p>
              <div v-if="t.guards.length || t.effects.length" class="mt-2 flex flex-wrap gap-1.5">
                <span v-for="g in t.guards" :key="'g' + g.id" class="tag"
                      :class="g.kind === 'rule' ? 'bg-sky-50 text-sky-700 ring-sky-200' : 'bg-zinc-100 text-zinc-700 ring-zinc-200'">
                  <i class="fa-solid fa-fw text-[10px]" :class="g.kind === 'rule' ? 'fa-flag' : 'fa-lock'" />{{ guardText(g) }}
                </span>
                <span v-for="f in t.effects" :key="'f' + f.id" class="tag bg-amber-50 text-amber-800 ring-amber-200">
                  <i class="fa-solid fa-bolt fa-fw text-[11px]" />{{ ename(f.entity_id) }} → {{ effectText(f) }}
                </span>
              </div>
            </div>
          </div>
        </section>

        <!-- MATRICE STATI × AZIONI -->
        <section v-if="actionNames.length" class="card overflow-hidden">
          <div class="flex items-center gap-2.5 px-6 py-4 border-b border-zinc-100">
            <i class="fa-solid fa-table fa-fw text-[14px] text-zinc-400" />
            <div>
              <h2 class="font-semibold tracking-tight">Tabella stati × azioni</h2>
              <p class="text-xs text-zinc-500">Le celle vuote sono le combinazioni non gestite: verifica che sia voluto.</p>
            </div>
          </div>
          <div class="overflow-x-auto">
            <table class="w-full text-xs">
              <thead>
                <tr class="bg-zinc-50/80">
                  <th class="text-left font-medium text-zinc-500 px-4 py-2 sticky left-0 bg-zinc-50">stato \ azione</th>
                  <th v-for="a in actionNames" :key="a" class="font-medium text-zinc-700 px-3 py-2 whitespace-nowrap">{{ a }}</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="s in matrixRows" :key="s.id" class="border-t border-zinc-100">
                  <td class="px-4 py-2 sticky left-0 bg-white"><StateChip :id="s.id" size="xs" /></td>
                  <td v-for="a in actionNames" :key="a" class="px-3 py-2 text-center whitespace-nowrap">
                    <template v-for="t in cell(s.id, a)" :key="t.id">
                      <span class="inline-flex items-center gap-0.5 font-mono text-[10.5px] text-zinc-700">
                        → {{ sname(t.to_state_id) }}<i v-if="t.guards.some((g) => g.kind === 'state')" class="fa-solid fa-lock text-[9px] text-zinc-400 ml-0.5" />
                      </span>
                    </template>
                    <span v-if="!cell(s.id, a).length" class="text-zinc-200">·</span>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>

        <!-- ATTRIBUTI -->
        <section class="card">
          <div class="px-6 py-4 border-b border-zinc-100">
            <h2 class="font-semibold tracking-tight">Attributi</h2>
          </div>
          <div class="divide-y divide-zinc-100">
            <div v-for="a in e.attributes" :key="a.id" class="flex items-center gap-3 px-6 py-2 group">
              <input :value="a.name" @change="(ev) => patchAttr(a, { name: ev.target.value })"
                     class="font-mono text-sm w-44 bg-transparent rounded px-1.5 py-1 -mx-1.5 hover:bg-zinc-50 focus:outline-none focus:ring-2 focus:ring-indigo-500/50" />
              <select :value="a.type" @change="(ev) => patchAttr(a, { type: ev.target.value })"
                      class="text-xs font-mono text-violet-700 bg-violet-50 rounded-md px-2 py-1 ring-1 ring-violet-100 focus:outline-none cursor-pointer">
                <option v-for="t in TYPES" :key="t">{{ t }}</option>
              </select>
              <label class="flex items-center gap-1.5 text-xs text-zinc-500 cursor-pointer">
                <input type="checkbox" :checked="!!a.required" class="accent-indigo-600" @change="(ev) => patchAttr(a, { required: ev.target.checked ? 1 : 0 })" /> obbligatorio
              </label>
              <input :value="a.description" placeholder="note…" @change="(ev) => patchAttr(a, { description: ev.target.value })"
                     class="flex-1 text-sm text-zinc-500 bg-transparent rounded px-1.5 py-1 hover:bg-zinc-50 focus:outline-none focus:ring-2 focus:ring-indigo-500/50 placeholder:text-zinc-300" />
              <button class="icon-btn opacity-0 group-hover:opacity-100 hover:!text-rose-600" @click="removeAttr(a)"><i class="fa-solid fa-trash-can fa-fw text-[12px]" /></button>
            </div>
          </div>
          <form class="flex items-center gap-2 px-6 py-3 bg-zinc-50/60 rounded-b-2xl border-t border-zinc-100" @submit.prevent="addAttr">
            <i class="fa-solid fa-plus fa-fw text-[14px] text-zinc-400" />
            <input v-model="newAttr" placeholder="Nuovo attributo e invio" class="flex-1 bg-transparent font-mono text-sm focus:outline-none placeholder:font-sans placeholder:text-zinc-400" />
          </form>
        </section>
      </div>

      <!-- Colonna destra -->
      <div class="xl:col-span-2 space-y-6">
        <section class="card overflow-hidden xl:sticky xl:top-28">
          <div class="flex items-center gap-2.5 px-6 py-4 border-b border-zinc-100">
            <i class="fa-solid fa-diagram-project fa-fw text-[14px] text-zinc-400" />
            <h2 class="font-semibold tracking-tight">Diagramma degli stati</h2>
            <span class="ml-auto text-[11px] text-zinc-400"><i class="fa-solid fa-lock" /> guardia · <i class="fa-solid fa-bolt" /> effetto · <i class="fa-solid fa-arrows-turn-right" /> in cascata</span>
          </div>
          <div class="p-4 bg-[radial-gradient(circle_at_1px_1px,#e4e4e7_1px,transparent_0)] [background-size:16px_16px]">
            <Mermaid :code="diagram" />
          </div>

          <div class="border-t border-zinc-100">
            <div class="flex items-center gap-2.5 px-6 py-3">
              <i class="fa-solid fa-link fa-fw text-[14px] text-zinc-400" />
              <h3 class="text-sm font-semibold tracking-tight">Chi dipende da {{ e.name }}</h3>
            </div>
            <div v-if="!incoming.length" class="px-6 pb-4 text-xs text-zinc-400">Nessuna altra entità guarda lo stato di {{ e.name }}.</div>
            <div v-for="(d, i) in incoming" :key="i" class="px-6 py-2 text-xs flex items-center gap-2 border-t border-zinc-50">
              <i class="fa-solid fa-fw text-[12px] shrink-0" :class="d.kind === 'guard' ? 'fa-lock text-zinc-400' : 'fa-bolt text-amber-500'" />
              <RouterLink :to="`/d/${m.id}/entity/${d.entity.id}`" class="font-medium hover:underline" :class="pal(d.entity.color).text">{{ d.entity.name }}</RouterLink>
              <span class="text-zinc-600">«{{ d.t.action }}»</span>
              <span class="text-zinc-400">{{ d.kind === 'guard' ? (d.g.negate ? 'vietata se' : 'richiede') : 'porta a' }}</span>
              <span class="flex flex-wrap gap-1">
                <template v-if="d.kind === 'guard'"><StateChip v-for="sid in d.g.state_ids" :key="sid" :id="sid" size="xs" /></template>
                <span v-else-if="d.f.kind !== 'state'" class="tag bg-indigo-50 text-indigo-700 ring-indigo-200">{{ effectText(d.f) }}</span>
                <StateChip v-else :id="d.f.to_state_id" size="xs" />
              </span>
            </div>
          </div>
        </section>
      </div>
    </div>

    <!-- Modal metadati -->
    <Modal v-if="meta" title="Modifica entità" @close="meta = null">
      <form class="space-y-4" @submit.prevent="saveMeta">
        <div class="grid grid-cols-2 gap-3">
          <div><label class="label">Nome</label><input v-model="meta.name" class="input" required /></div>
          <div>
            <label class="label">Tipo</label>
            <select v-model="meta.kind" class="input"><option v-for="(l, k) in KINDS" :key="k" :value="k">{{ l }}</option></select>
          </div>
        </div>
        <div>
          <label class="label">Bounded context</label>
          <input v-model="meta.context" class="input" list="contexts" placeholder="es. Vendite" />
          <datalist id="contexts"><option v-for="c in [...new Set(m.entities.map((x) => x.context).filter(Boolean))]" :key="c" :value="c" /></datalist>
        </div>
        <div>
          <label class="label">Colore</label>
          <div class="flex gap-2">
            <button v-for="c in COLORS" :key="c" type="button" @click="meta.color = c"
                    class="size-7 rounded-full cursor-pointer transition ring-offset-2" :class="[PALETTE[c].dot, meta.color === c ? 'ring-2 ring-zinc-900' : 'hover:scale-110']" />
          </div>
        </div>
        <div><label class="label">Descrizione</label><textarea v-model="meta.description" rows="3" class="input" /></div>
        <div class="flex justify-end"><button class="btn-accent">Salva</button></div>
      </form>
    </Modal>

    <!-- Modal azione -->
    <Modal v-if="draft" wide :title="draft.id ? `Modifica azione «${draft.action}»` : draft.kind === 'create' ? 'Nuova azione di creazione' : 'Nuova azione'" @close="draft = null">
      <form id="tform" class="space-y-6" @submit.prevent="saveTransition">
        <div class="grid grid-cols-[1fr_auto_1.3fr_auto_1fr] items-end gap-3">
          <div>
            <label class="label">Da stato</label>
            <select v-model="fromSel" class="input font-mono">
              <option value="create">nasce (creazione)</option>
              <option :value="null">∗ qualsiasi non finale</option>
              <option v-for="s in e.states" :key="s.id" :value="s.id">{{ s.name }}</option>
            </select>
          </div>
          <i class="fa-solid fa-arrow-right fa-fw text-[14px] text-zinc-300 mb-2.5" />
          <div><label class="label">Azione</label><input v-model="draft.action" class="input font-medium" placeholder="es. approva" required /></div>
          <i class="fa-solid fa-arrow-right fa-fw text-[14px] text-zinc-300 mb-2.5" />
          <div>
            <label class="label">A stato</label>
            <select v-model="draft.to_state_id" class="input font-mono"><option v-for="s in e.states" :key="s.id" :value="s.id">{{ s.name }}</option></select>
          </div>
        </div>
        <div class="grid grid-cols-3 gap-3">
          <div><label class="label">Attore</label><input v-model="draft.actor" class="input" placeholder="es. Operatore" list="actors" />
            <datalist id="actors"><option v-for="a in [...new Set(m.entities.flatMap((x) => x.transitions.map((t) => t.actor)).filter(Boolean))]" :key="a" :value="a" /></datalist>
          </div>
          <div class="col-span-2"><label class="label">Descrizione</label><input v-model="draft.description" class="input" /></div>
        </div>
        <label class="flex items-start gap-2.5 rounded-xl ring-1 ring-zinc-200 p-3 cursor-pointer hover:bg-zinc-50 transition">
          <input type="checkbox" class="accent-indigo-600 mt-0.5" :checked="!!draft.cascade_only" @change="(ev) => (draft.cascade_only = ev.target.checked ? 1 : 0)" />
          <span>
            <span class="text-sm font-medium flex items-center gap-1.5"><i class="fa-solid fa-arrows-turn-right text-amber-500" /> Solo in cascata</span>
            <span class="block text-xs text-zinc-500">Non si esegue direttamente: avviene solo come effetto di un'altra azione (es. la fase di gara nasce solo creando la procedura).</span>
          </span>
        </label>

        <!-- Guardie -->
        <div>
          <div class="flex items-center justify-between mb-2">
            <div>
              <div class="text-sm font-semibold flex items-center gap-1.5"><i class="fa-solid fa-lock fa-fw text-[12px]" /> Guardie</div>
              <p class="text-xs text-zinc-500">Pre-condizioni sugli stati di altre entità (verificate) o regole di business (da verificare a mano).</p>
            </div>
            <div class="flex gap-1">
              <button type="button" class="btn-ghost !text-xs" @click="addGuard('state')"><i class="fa-solid fa-plus fa-fw text-[12px]" /> su stato</button>
              <button type="button" class="btn-ghost !text-xs" @click="addGuard('rule')"><i class="fa-solid fa-plus fa-fw text-[12px]" /> regola</button>
            </div>
          </div>
          <div class="space-y-2">
            <div v-for="(g, i) in draft.guards" :key="i" class="rounded-xl ring-1 ring-zinc-200 p-3 bg-zinc-50/50">
              <div v-if="g.kind === 'state'" class="space-y-2.5">
                <div class="flex items-center gap-2">
                  <select v-model="g.entity_id" class="input !w-44 !py-1.5" @change="g.state_ids = []">
                    <option v-for="x in m.entities" :key="x.id" :value="x.id">{{ x.name }}</option>
                  </select>
                  <select v-model="g.negate" class="input !w-40 !py-1.5">
                    <option :value="0">deve essere in</option>
                    <option :value="1">non deve essere in</option>
                  </select>
                  <input v-model="g.description" class="input !py-1.5 flex-1" placeholder="motivazione (opzionale)" />
                  <button type="button" class="icon-btn hover:!text-rose-600" @click="draft.guards.splice(i, 1)"><i class="fa-solid fa-xmark fa-fw text-[14px]" /></button>
                </div>
                <StatePicker v-model="g.state_ids" :entity-id="g.entity_id" />
              </div>
              <div v-else class="flex items-center gap-2">
                <i class="fa-solid fa-flag fa-fw text-[14px] text-sky-500 shrink-0" />
                <input v-model="g.description" class="input !py-1.5 flex-1" placeholder="es. importo ≤ massimale di polizza" />
                <button type="button" class="icon-btn hover:!text-rose-600" @click="draft.guards.splice(i, 1)"><i class="fa-solid fa-xmark fa-fw text-[14px]" /></button>
              </div>
            </div>
            <p v-if="!draft.guards.length" class="text-xs text-zinc-400 italic">Nessuna guardia: l'azione è sempre eseguibile dallo stato di partenza.</p>
          </div>
        </div>

        <!-- Effetti -->
        <div>
          <div class="flex items-center justify-between mb-2">
            <div>
              <div class="text-sm font-semibold flex items-center gap-1.5"><i class="fa-solid fa-bolt fa-fw text-[12px]" /> Effetti a cascata</div>
              <p class="text-xs text-zinc-500">Quando l'azione avviene, un'altra entità deve cambiare stato, eseguire un'azione o nascere. Se non può, è un conflitto.</p>
            </div>
            <button type="button" class="btn-ghost !text-xs" :disabled="!others.length" @click="addEffect"><i class="fa-solid fa-plus fa-fw text-[12px]" /> effetto</button>
          </div>
          <div class="space-y-2">
            <div v-for="(f, i) in draft.effects" :key="i" class="flex items-center gap-2 rounded-xl ring-1 ring-amber-200 bg-amber-50/40 p-3">
              <select v-model="f.entity_id" class="input !w-44 !py-1.5" @change="resetEffect(f)">
                <option v-for="x in others" :key="x.id" :value="x.id">{{ x.name }}</option>
              </select>
              <select v-model="f.kind" class="input !w-40 !py-1.5">
                <option value="state">passa allo stato</option>
                <option value="action">esegue l'azione</option>
                <option v-if="isCreatable(entity(f.entity_id))" value="create">nasce</option>
              </select>
              <select v-if="f.kind === 'state'" v-model="f.to_state_id" class="input !w-48 !py-1.5 font-mono">
                <option v-for="s in entity(f.entity_id)?.states || []" :key="s.id" :value="s.id">{{ s.name }}</option>
              </select>
              <select v-else-if="f.kind === 'action'" v-model="f.action" class="input !w-48 !py-1.5">
                <option v-for="a in actionsOf(f.entity_id)" :key="a" :value="a">{{ a }}</option>
              </select>
              <span v-else class="w-48 text-xs text-zinc-500 px-1">lo stato lo decidono le guardie di creazione</span>
              <label v-if="f.kind !== 'create'" class="flex items-center gap-1.5 text-xs text-zinc-600 whitespace-nowrap cursor-pointer"
                     title="Per relazioni 1-N: l'effetto vale per tutte le istanze ancora attive e ignora quelle in stato finale (es. nel cestino)">
                <input type="checkbox" :checked="!!f.skip_final" class="accent-indigo-600" @change="(ev) => (f.skip_final = ev.target.checked ? 1 : 0)" />
                salta concluse
              </label>
              <input v-model="f.description" class="input !py-1.5 flex-1" placeholder="note" />
              <button type="button" class="icon-btn hover:!text-rose-600" @click="draft.effects.splice(i, 1)"><i class="fa-solid fa-xmark fa-fw text-[14px]" /></button>
            </div>
          </div>
        </div>
      </form>
      <template #footer>
        <button class="btn-ghost" @click="draft = null">Annulla</button>
        <button class="btn-accent" form="tform" :disabled="!draft.action.trim() || !draft.to_state_id">Salva azione</button>
      </template>
    </Modal>
  </div>
  <div v-else class="p-8 text-zinc-400">Entità non trovata.</div>
</template>
