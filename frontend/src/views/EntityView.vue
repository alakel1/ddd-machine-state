<script setup>
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { store, api, reloadModel, entity, ename, sname, toast, isCreatable } from '../store'
import { pal, KINDS, COLORS, PALETTE } from '../colors'
import { t } from '../i18n'
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
  if (!confirm(t('entity.confirmDelete', { name: e.value.name }))) return
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
  const all = m.value.entities.flatMap((x) => x.transitions.map((tr) => ({ x, tr })))
  const lost = [
    ...e.value.transitions.filter((tr) => tr.from_state_id === s.id || tr.to_state_id === s.id)
      .map((tr) => t('entity.lost.action', { action: tr.action })),
    ...all.flatMap(({ x, tr }) => tr.effects.filter((f) => f.to_state_id === s.id)
      .map(() => t('entity.lost.effect', { action: tr.action, entity: x.name }))),
    ...all.flatMap(({ x, tr }) => tr.guards.filter((g) => g.state_ids?.includes(s.id))
      .map(() => t('entity.lost.guard', { action: tr.action, entity: x.name }))),
    ...m.value.invariants.filter((i) => i.rules.some((r) => r.state_ids.includes(s.id)))
      .map((i) => t('entity.lost.invariant', { name: i.name })),
  ]
  if (lost.length && !confirm(t('entity.confirmDeleteState', { name: s.name, list: lost.join('\n- ') }))) return
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
const creations = computed(() => e.value.transitions.filter((tr) => tr.kind === 'create'))
const evolutions = computed(() => e.value.transitions.filter((tr) => tr.kind !== 'create'))
const creatable = computed(() => creations.value.length > 0)
const birthStates = computed(() => new Set(creations.value.map((tr) => tr.to_state_id)))
const actionGroups = computed(() => [
  { key: 'create', title: t('entity.birth.title'), hint: t('entity.birth.hint'), list: creations.value },
  { key: 'normal', title: t('entity.evolution.title'), hint: t('entity.evolution.hint'), list: evolutions.value },
])

function openTransition(tr, kind = 'normal') {
  draft.value = tr
    ? JSON.parse(JSON.stringify(tr))
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
const actionsOf = (eid) => [...new Set((entity(eid)?.transitions || []).filter((tr) => tr.kind !== 'create').map((tr) => tr.action))]
const effectText = (f) =>
  (f.kind === 'create' ? t('entity.effect.created') : f.kind === 'action' ? t('entity.effect.runs', { action: f.action }) : sname(f.to_state_id)) +
  (f.skip_final ? ` ${t('entity.effect.activeOnly')}` : '')
async function saveTransition() {
  const d = draft.value
  const body = {
    entity_id: e.value.id, kind: d.kind || 'normal', cascade_only: d.cascade_only ? 1 : 0,
    from_state_id: d.from_state_id, to_state_id: d.to_state_id,
    action: d.action.trim(), actor: d.actor, description: d.description,
  }
  const saved = d.id ? await api.put(`/transitions/${d.id}`, body) : await api.post('/transitions', body)
  const orig = d.id ? e.value.transitions.find((x) => x.id === d.id) : { guards: [], effects: [] }
  const sync = async (table, oldList, newList) => {
    const keep = new Set(newList.filter((x) => x.id).map((x) => x.id))
    await Promise.all([
      ...oldList.filter((x) => !keep.has(x.id)).map((x) => api.del(`/${table}/${x.id}`)),
      ...newList.map((x) => (x.id ? api.put(`/${table}/${x.id}`, x) : api.post(`/${table}`, { ...x, transition_id: saved.id }))),
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
  toast(t('entity.actionSaved'))
}
async function removeTransition(tr) {
  if (!confirm(t('common.confirmDelete', { name: tr.action }))) return
  await api.del(`/transitions/${tr.id}`)
  await reloadModel()
}

// ---------- dipendenze in ingresso ----------
const incoming = computed(() => {
  const out = []
  for (const o of others.value)
    for (const tr of o.transitions) {
      for (const g of tr.guards)
        if (g.kind === 'state' && g.entity_id === e.value.id) out.push({ kind: 'guard', entity: o, t: tr, g })
      for (const f of tr.effects) if (f.entity_id === e.value.id) out.push({ kind: 'effect', entity: o, t: tr, f })
    }
  return out
})

// ---------- matrice stati × azioni ----------
const actionNames = computed(() => [...new Set(e.value.transitions.map((tr) => tr.action))])
const matrixRows = computed(() => (creatable.value ? [{ id: 0 }, ...e.value.states] : e.value.states))
function cell(stateId, action) {
  if (stateId === 0) return creations.value.filter((tr) => tr.action === action)
  const s = e.value.states.find((x) => x.id === stateId)
  return evolutions.value.filter(
    (tr) => tr.action === action && (tr.from_state_id === stateId || (tr.from_state_id == null && !s.is_final && tr.to_state_id !== stateId)),
  )
}

const guardText = (g) =>
  g.kind === 'rule' ? g.description : `${ename(g.entity_id)} ${g.negate ? '∉' : '∈'} {${g.state_ids.map(sname).join(', ')}}`
</script>

<template>
  <div v-if="e">
    <PageHeader :eyebrow="`${$t(`kind.${e.kind}`)} · ${e.context || $t('entity.noContext')}`" :subtitle="e.description">
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
        <button class="btn-danger" @click="removeEntity"><i class="fa-solid fa-trash-can fa-fw text-[14px]" /> {{ $t('common.delete') }}</button>
        <button class="btn-accent" :disabled="!e.states.length" @click="openTransition()"><i class="fa-solid fa-plus fa-fw text-[14px]" /> {{ $t('entity.action') }}</button>
      </template>
    </PageHeader>

    <div class="p-8 grid gap-6 xl:grid-cols-5">
      <div class="xl:col-span-3 space-y-6">
        <!-- STATI -->
        <section class="card">
          <div class="flex items-center justify-between px-6 py-4 border-b border-zinc-100">
            <div>
              <h2 class="font-semibold tracking-tight">{{ $t('entity.states') }}</h2>
              <p class="text-xs text-zinc-500">
                {{ $t('entity.statesHint') }}
                {{ $t(creatable ? 'entity.statesHintCreatable' : 'entity.statesHintInitial') }}
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
              <input :value="s.description" :placeholder="$t('entity.descriptionPlaceholder')" @change="(ev) => patchState(s, { description: ev.target.value })"
                     class="flex-1 text-sm text-zinc-500 bg-transparent rounded px-1.5 py-1 hover:bg-zinc-50 focus:bg-white focus:outline-none focus:ring-2 focus:ring-indigo-500/50 placeholder:text-zinc-300" />
              <span v-if="creatable" class="tag" :class="birthStates.has(s.id) ? 'bg-indigo-50 text-indigo-700 ring-indigo-200' : 'invisible'">
                <i class="fa-solid fa-seedling" /> {{ $t('entity.bornHere') }}
              </span>
              <button v-else class="tag cursor-pointer transition" @click="patchState(s, { is_initial: s.is_initial ? 0 : 1 })"
                      :class="s.is_initial ? 'bg-indigo-600 text-white ring-indigo-600' : 'bg-white text-zinc-400 ring-zinc-200 hover:text-zinc-700'">{{ $t('state.initial') }}</button>
              <button class="tag cursor-pointer transition" @click="patchState(s, { is_final: s.is_final ? 0 : 1 })"
                      :class="s.is_final ? 'bg-zinc-800 text-white ring-zinc-800' : 'bg-white text-zinc-400 ring-zinc-200 hover:text-zinc-700'">{{ $t('state.final') }}</button>
              <button class="icon-btn opacity-0 group-hover:opacity-100 hover:!text-rose-600" @click="removeState(s)"><i class="fa-solid fa-trash-can fa-fw text-[12px]" /></button>
            </div>
          </div>
          <form class="flex items-center gap-2 px-6 py-3 bg-zinc-50/60 rounded-b-2xl border-t border-zinc-100" @submit.prevent="addState">
            <i class="fa-solid fa-plus fa-fw text-[14px] text-zinc-400" />
            <input v-model="newState" :placeholder="$t('entity.newStatePlaceholder')"
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
              <i class="fa-solid fa-plus fa-fw text-[14px]" /> {{ $t(grp.key === 'create' ? 'entity.creation' : 'entity.action') }}
            </button>
          </div>
          <div v-if="!grp.list.length" class="px-6 py-6 text-center text-sm text-zinc-400">
            <template v-if="!e.states.length">{{ $t('entity.statesFirst') }}</template>
            <template v-else-if="grp.key === 'create'">{{ $t('entity.noCreations') }}</template>
            <template v-else>{{ $t('entity.noActions') }}</template>
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
                <span v-if="t.cascade_only" class="tag bg-amber-50 text-amber-800 ring-amber-200" :title="$t('entity.cascadeOnlyHint')">
                  <i class="fa-solid fa-arrows-turn-right" /> {{ $t('entity.cascadeOnlyTag') }}
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
              <h2 class="font-semibold tracking-tight">{{ $t('entity.matrix') }}</h2>
              <p class="text-xs text-zinc-500">{{ $t('entity.matrixHint') }}</p>
            </div>
          </div>
          <div class="overflow-x-auto">
            <table class="w-full text-xs">
              <thead>
                <tr class="bg-zinc-50/80">
                  <th class="text-left font-medium text-zinc-500 px-4 py-2 sticky left-0 bg-zinc-50">{{ $t('entity.matrixCorner') }}</th>
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
            <h2 class="font-semibold tracking-tight">{{ $t('entity.attributes') }}</h2>
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
                <input type="checkbox" :checked="!!a.required" class="accent-indigo-600" @change="(ev) => patchAttr(a, { required: ev.target.checked ? 1 : 0 })" /> {{ $t('entity.required') }}
              </label>
              <input :value="a.description" :placeholder="$t('entity.notesPlaceholder')" @change="(ev) => patchAttr(a, { description: ev.target.value })"
                     class="flex-1 text-sm text-zinc-500 bg-transparent rounded px-1.5 py-1 hover:bg-zinc-50 focus:outline-none focus:ring-2 focus:ring-indigo-500/50 placeholder:text-zinc-300" />
              <button class="icon-btn opacity-0 group-hover:opacity-100 hover:!text-rose-600" @click="removeAttr(a)"><i class="fa-solid fa-trash-can fa-fw text-[12px]" /></button>
            </div>
          </div>
          <form class="flex items-center gap-2 px-6 py-3 bg-zinc-50/60 rounded-b-2xl border-t border-zinc-100" @submit.prevent="addAttr">
            <i class="fa-solid fa-plus fa-fw text-[14px] text-zinc-400" />
            <input v-model="newAttr" :placeholder="$t('entity.newAttributePlaceholder')" class="flex-1 bg-transparent font-mono text-sm focus:outline-none placeholder:font-sans placeholder:text-zinc-400" />
          </form>
        </section>
      </div>

      <!-- Colonna destra -->
      <div class="xl:col-span-2 space-y-6">
        <section class="card overflow-hidden xl:sticky xl:top-28">
          <div class="flex items-center gap-2.5 px-6 py-4 border-b border-zinc-100">
            <i class="fa-solid fa-diagram-project fa-fw text-[14px] text-zinc-400" />
            <h2 class="font-semibold tracking-tight">{{ $t('entity.diagram') }}</h2>
            <span class="ml-auto text-[11px] text-zinc-400"><i class="fa-solid fa-lock" /> {{ $t('entity.legend.guard') }} · <i class="fa-solid fa-bolt" /> {{ $t('entity.legend.effect') }} · <i class="fa-solid fa-arrows-turn-right" /> {{ $t('entity.legend.cascade') }}</span>
          </div>
          <div class="p-4 bg-[radial-gradient(circle_at_1px_1px,#e4e4e7_1px,transparent_0)] [background-size:16px_16px]">
            <Mermaid :code="diagram" />
          </div>

          <div class="border-t border-zinc-100">
            <div class="flex items-center gap-2.5 px-6 py-3">
              <i class="fa-solid fa-link fa-fw text-[14px] text-zinc-400" />
              <h3 class="text-sm font-semibold tracking-tight">{{ $t('entity.dependents', { name: e.name }) }}</h3>
            </div>
            <div v-if="!incoming.length" class="px-6 pb-4 text-xs text-zinc-400">{{ $t('entity.noDependents', { name: e.name }) }}</div>
            <div v-for="(d, i) in incoming" :key="i" class="px-6 py-2 text-xs flex items-center gap-2 border-t border-zinc-50">
              <i class="fa-solid fa-fw text-[12px] shrink-0" :class="d.kind === 'guard' ? 'fa-lock text-zinc-400' : 'fa-bolt text-amber-500'" />
              <RouterLink :to="`/d/${m.id}/entity/${d.entity.id}`" class="font-medium hover:underline" :class="pal(d.entity.color).text">{{ d.entity.name }}</RouterLink>
              <span class="text-zinc-600">{{ $t('common.quoted', { x: d.t.action }) }}</span>
              <span class="text-zinc-400">{{ $t(d.kind === 'guard' ? (d.g.negate ? 'entity.dep.forbiddenIf' : 'entity.dep.requires') : 'entity.dep.leadsTo') }}</span>
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
    <Modal v-if="meta" :title="$t('entity.edit')" @close="meta = null">
      <form class="space-y-4" @submit.prevent="saveMeta">
        <div class="grid grid-cols-2 gap-3">
          <div><label class="label">{{ $t('common.name') }}</label><input v-model="meta.name" class="input" required /></div>
          <div>
            <label class="label">{{ $t('entity.kind') }}</label>
            <select v-model="meta.kind" class="input"><option v-for="k in KINDS" :key="k" :value="k">{{ $t(`kind.${k}`) }}</option></select>
          </div>
        </div>
        <div>
          <label class="label">Bounded context</label>
          <input v-model="meta.context" class="input" list="contexts" :placeholder="$t('entity.contextPlaceholder')" />
          <datalist id="contexts"><option v-for="c in [...new Set(m.entities.map((x) => x.context).filter(Boolean))]" :key="c" :value="c" /></datalist>
        </div>
        <div>
          <label class="label">{{ $t('entity.color') }}</label>
          <div class="flex gap-2">
            <button v-for="c in COLORS" :key="c" type="button" @click="meta.color = c"
                    class="size-7 rounded-full cursor-pointer transition ring-offset-2" :class="[PALETTE[c].dot, meta.color === c ? 'ring-2 ring-zinc-900' : 'hover:scale-110']" />
          </div>
        </div>
        <div><label class="label">{{ $t('common.description') }}</label><textarea v-model="meta.description" rows="3" class="input" /></div>
        <div class="flex justify-end"><button class="btn-accent">{{ $t('common.save') }}</button></div>
      </form>
    </Modal>

    <!-- Modal azione -->
    <Modal v-if="draft" wide :title="draft.id ? $t('entity.editAction', { name: draft.action }) : $t(draft.kind === 'create' ? 'entity.newCreation' : 'entity.newAction')" @close="draft = null">
      <form id="tform" class="space-y-6" @submit.prevent="saveTransition">
        <div class="grid grid-cols-[1fr_auto_1.3fr_auto_1fr] items-end gap-3">
          <div>
            <label class="label">{{ $t('entity.fromState') }}</label>
            <select v-model="fromSel" class="input font-mono">
              <option value="create">{{ $t('entity.fromCreate') }}</option>
              <option :value="null">{{ $t('entity.fromAny') }}</option>
              <option v-for="s in e.states" :key="s.id" :value="s.id">{{ s.name }}</option>
            </select>
          </div>
          <i class="fa-solid fa-arrow-right fa-fw text-[14px] text-zinc-300 mb-2.5" />
          <div><label class="label">{{ $t('entity.action') }}</label><input v-model="draft.action" class="input font-medium" :placeholder="$t('entity.actionPlaceholder')" required /></div>
          <i class="fa-solid fa-arrow-right fa-fw text-[14px] text-zinc-300 mb-2.5" />
          <div>
            <label class="label">{{ $t('entity.toState') }}</label>
            <select v-model="draft.to_state_id" class="input font-mono"><option v-for="s in e.states" :key="s.id" :value="s.id">{{ s.name }}</option></select>
          </div>
        </div>
        <div class="grid grid-cols-3 gap-3">
          <div><label class="label">{{ $t('entity.actor') }}</label><input v-model="draft.actor" class="input" :placeholder="$t('entity.actorPlaceholder')" list="actors" />
            <datalist id="actors"><option v-for="a in [...new Set(m.entities.flatMap((x) => x.transitions.map((t) => t.actor)).filter(Boolean))]" :key="a" :value="a" /></datalist>
          </div>
          <div class="col-span-2"><label class="label">{{ $t('common.description') }}</label><input v-model="draft.description" class="input" /></div>
        </div>
        <label class="flex items-start gap-2.5 rounded-xl ring-1 ring-zinc-200 p-3 cursor-pointer hover:bg-zinc-50 transition">
          <input type="checkbox" class="accent-indigo-600 mt-0.5" :checked="!!draft.cascade_only" @change="(ev) => (draft.cascade_only = ev.target.checked ? 1 : 0)" />
          <span>
            <span class="text-sm font-medium flex items-center gap-1.5"><i class="fa-solid fa-arrows-turn-right text-amber-500" /> {{ $t('entity.cascadeOnly') }}</span>
            <span class="block text-xs text-zinc-500">{{ $t('entity.cascadeOnlyLong') }}</span>
          </span>
        </label>

        <!-- Guardie -->
        <div>
          <div class="flex items-center justify-between mb-2">
            <div>
              <div class="text-sm font-semibold flex items-center gap-1.5"><i class="fa-solid fa-lock fa-fw text-[12px]" /> {{ $t('entity.guards') }}</div>
              <p class="text-xs text-zinc-500">{{ $t('entity.guardsHint') }}</p>
            </div>
            <div class="flex gap-1">
              <button type="button" class="btn-ghost !text-xs" @click="addGuard('state')"><i class="fa-solid fa-plus fa-fw text-[12px]" /> {{ $t('entity.onState') }}</button>
              <button type="button" class="btn-ghost !text-xs" @click="addGuard('rule')"><i class="fa-solid fa-plus fa-fw text-[12px]" /> {{ $t('entity.rule') }}</button>
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
                    <option :value="0">{{ $t('entity.mustBeIn') }}</option>
                    <option :value="1">{{ $t('entity.mustNotBeIn') }}</option>
                  </select>
                  <input v-model="g.description" class="input !py-1.5 flex-1" :placeholder="$t('entity.reasonPlaceholder')" />
                  <button type="button" class="icon-btn hover:!text-rose-600" @click="draft.guards.splice(i, 1)"><i class="fa-solid fa-xmark fa-fw text-[14px]" /></button>
                </div>
                <StatePicker v-model="g.state_ids" :entity-id="g.entity_id" />
              </div>
              <div v-else class="flex items-center gap-2">
                <i class="fa-solid fa-flag fa-fw text-[14px] text-sky-500 shrink-0" />
                <input v-model="g.description" class="input !py-1.5 flex-1" :placeholder="$t('entity.rulePlaceholder')" />
                <button type="button" class="icon-btn hover:!text-rose-600" @click="draft.guards.splice(i, 1)"><i class="fa-solid fa-xmark fa-fw text-[14px]" /></button>
              </div>
            </div>
            <p v-if="!draft.guards.length" class="text-xs text-zinc-400 italic">{{ $t('entity.noGuards') }}</p>
          </div>
        </div>

        <!-- Effetti -->
        <div>
          <div class="flex items-center justify-between mb-2">
            <div>
              <div class="text-sm font-semibold flex items-center gap-1.5"><i class="fa-solid fa-bolt fa-fw text-[12px]" /> {{ $t('entity.effects') }}</div>
              <p class="text-xs text-zinc-500">{{ $t('entity.effectsHint') }}</p>
            </div>
            <button type="button" class="btn-ghost !text-xs" :disabled="!others.length" @click="addEffect"><i class="fa-solid fa-plus fa-fw text-[12px]" /> {{ $t('entity.effect.add') }}</button>
          </div>
          <div class="space-y-2">
            <div v-for="(f, i) in draft.effects" :key="i" class="flex items-center gap-2 rounded-xl ring-1 ring-amber-200 bg-amber-50/40 p-3">
              <select v-model="f.entity_id" class="input !w-44 !py-1.5" @change="resetEffect(f)">
                <option v-for="x in others" :key="x.id" :value="x.id">{{ x.name }}</option>
              </select>
              <select v-model="f.kind" class="input !w-40 !py-1.5">
                <option value="state">{{ $t('entity.effect.toState') }}</option>
                <option value="action">{{ $t('entity.effect.runsAction') }}</option>
                <option v-if="isCreatable(entity(f.entity_id))" value="create">{{ $t('entity.effect.created') }}</option>
              </select>
              <select v-if="f.kind === 'state'" v-model="f.to_state_id" class="input !w-48 !py-1.5 font-mono">
                <option v-for="s in entity(f.entity_id)?.states || []" :key="s.id" :value="s.id">{{ s.name }}</option>
              </select>
              <select v-else-if="f.kind === 'action'" v-model="f.action" class="input !w-48 !py-1.5">
                <option v-for="a in actionsOf(f.entity_id)" :key="a" :value="a">{{ a }}</option>
              </select>
              <span v-else class="w-48 text-xs text-zinc-500 px-1">{{ $t('entity.effect.createHint') }}</span>
              <label v-if="f.kind !== 'create'" class="flex items-center gap-1.5 text-xs text-zinc-600 whitespace-nowrap cursor-pointer"
                     :title="$t('entity.effect.skipFinalHint')">
                <input type="checkbox" :checked="!!f.skip_final" class="accent-indigo-600" @change="(ev) => (f.skip_final = ev.target.checked ? 1 : 0)" />
                {{ $t('entity.effect.skipFinal') }}
              </label>
              <input v-model="f.description" class="input !py-1.5 flex-1" :placeholder="$t('common.notes').toLowerCase()" />
              <button type="button" class="icon-btn hover:!text-rose-600" @click="draft.effects.splice(i, 1)"><i class="fa-solid fa-xmark fa-fw text-[14px]" /></button>
            </div>
          </div>
        </div>
      </form>
      <template #footer>
        <button class="btn-ghost" @click="draft = null">{{ $t('common.cancel') }}</button>
        <button class="btn-accent" form="tform" :disabled="!draft.action.trim() || !draft.to_state_id">{{ $t('entity.saveAction') }}</button>
      </template>
    </Modal>
  </div>
  <div v-else class="p-8 text-zinc-400">{{ $t('entity.notFound') }}</div>
</template>
