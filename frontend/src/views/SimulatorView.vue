<script setup>
import { computed, onMounted, ref } from 'vue'
import { store, api, entity, ename, sname, toast, statesOf } from '../store'
import { pal, STATUS } from '../colors'
import { stateDiagram } from '../diagrams'
import PageHeader from '../components/PageHeader.vue'
import Mermaid from '../components/Mermaid.vue'
import StateChip from '../components/StateChip.vue'
import Modal from '../components/Modal.vue'

const m = computed(() => store.model)
const ents = computed(() => m.value.entities.filter((e) => e.states.length))
const state = ref({})
const evaluation = ref(null)
const history = ref([])
const filter = ref('relevant')
const focus = ref(ents.value[0]?.id)
const scenarios = ref([])
const saving = ref(null)
const busy = ref(false)

const toNum = (s) => Object.fromEntries(Object.entries(s).map(([k, v]) => [Number(k), v]))

async function evaluate() {
  const r = await api.post(`/domains/${m.value.id}/evaluate`, { state: state.value })
  evaluation.value = r
  state.value = toNum(r.state)
}

async function reset() {
  state.value = toNum(await api.get(`/domains/${m.value.id}/initial`))
  history.value = []
  await evaluate()
}

async function setState(eid, sid) {
  const prev = { ...state.value }
  state.value = { ...state.value, [eid]: sid }
  history.value.unshift({ kind: 'manual', label: `${ename(eid)} impostato a ${sname(sid)}`, entity_id: eid, prev })
  await evaluate()
}

async function run(a, force = false) {
  busy.value = true
  try {
    const r = await api.post(`/domains/${m.value.id}/apply`, { state: state.value, transition_id: a.transition_id, force })
    if (!r.ok) {
      toast(`${r.error}${r.reasons ? ': ' + r.reasons[0] : ''}`, 'error')
      return false
    }
    history.value.unshift({
      kind: r.forced ? 'forced' : 'action', label: a.action, entity_id: a.entity_id, actor: a.actor,
      steps: r.steps, conflicts: r.conflicts, violations: r.violations, prev: { ...state.value },
    })
    state.value = toNum(r.state)
    focus.value = a.entity_id
    await evaluate()
    return true
  } finally {
    busy.value = false
  }
}

async function undo(i = 0) {
  const h = history.value[i]
  state.value = h.prev
  history.value = history.value.slice(i + 1)
  await evaluate()
}

// ---------- scenari ----------
async function loadScenarios() {
  scenarios.value = await api.get(`/domains/${m.value.id}/scenarios`)
}
async function loadScenario(id) {
  const sc = scenarios.value.find((s) => s.id === Number(id))
  if (!sc) return
  await reset()
  state.value = { ...state.value, ...toNum(sc.state) }
  history.value = [{ kind: 'manual', label: `Scenario «${sc.name}» caricato`, prev: { ...state.value } }]
  await evaluate()
}
async function saveScenario() {
  await api.post('/scenarios', { ...saving.value, domain_id: m.value.id, state: state.value, history: [] })
  saving.value = null
  await loadScenarios()
  toast('Scenario salvato')
}

// ---------- vista ----------
const actions = computed(() => evaluation.value?.actions || [])
const counts = computed(() => {
  const c = { enabled: 0, conflict: 0, blocked: 0, unavailable: 0 }
  for (const a of actions.value) c[a.status]++
  return c
})
const FILTERS = [
  { key: 'available', label: 'Eseguibili', match: ['enabled', 'conflict'] },
  { key: 'relevant', label: 'Dallo stato attuale', match: ['enabled', 'conflict', 'blocked'] },
  { key: 'all', label: 'Tutte', match: ['enabled', 'conflict', 'blocked', 'unavailable'] },
]
const grouped = computed(() => {
  const f = FILTERS.find((x) => x.key === filter.value).match
  const order = { enabled: 0, conflict: 1, blocked: 2, unavailable: 3 }
  return ents.value
    .map((e) => ({
      e,
      items: actions.value.filter((a) => a.entity_id === e.id && f.includes(a.status)).sort((x, y) => order[x.status] - order[y.status]),
    }))
    .filter((g) => g.items.length)
})
const violations = computed(() => evaluation.value?.violations || [])
const focusEntity = computed(() => entity(focus.value))
const diagram = computed(() =>
  stateDiagram(focusEntity.value, { current: state.value[focus.value] }),
)

onMounted(async () => {
  await loadScenarios()
  await reset()
  // replay di un percorso arrivato dall'analisi
  if (store.replay) {
    const trace = store.replay.trace
    store.replay = null
    for (const step of trace) {
      const a = actions.value.find((x) => x.transition_id === step.transition_id)
      if (!a || !(await run(a))) break
    }
    toast(`Percorso riprodotto: ${trace.length} passi`)
  }
})
</script>

<template>
  <PageHeader eyebrow="Machine status" title="Simulatore"
              subtitle="Imposta lo stato di ogni entità (anche combinazioni insolite) e osserva quali azioni restano disponibili, quali sono bloccate e quali producono conflitti.">
    <template #actions>
      <select class="input !w-60" @change="(ev) => { loadScenario(ev.target.value); ev.target.value = '' }">
        <option value="">Carica scenario…</option>
        <option v-for="s in scenarios" :key="s.id" :value="s.id">{{ s.name }}</option>
      </select>
      <button class="btn-outline" @click="saving = { name: '', description: '' }"><i class="fa-solid fa-floppy-disk fa-fw text-[14px]" /> Salva</button>
      <button class="btn-primary" @click="reset"><i class="fa-solid fa-rotate-left fa-fw text-[14px]" /> Reset</button>
    </template>
  </PageHeader>

  <div class="p-6 grid gap-6 xl:grid-cols-[300px_minmax(0,1fr)_380px]">
    <!-- STATO CORRENTE -->
    <aside class="space-y-3">
      <div class="eyebrow px-1">Stato corrente</div>
      <div v-for="e in ents" :key="e.id" @click="focus = e.id"
           class="card p-4 cursor-pointer transition relative overflow-hidden"
           :class="focus === e.id ? '!ring-2 !ring-indigo-500/70 shadow-md' : 'hover:ring-zinc-300'">
        <div class="absolute left-0 inset-y-0 w-1 bg-gradient-to-b" :class="pal(e.color).bar" />
        <div class="flex items-center justify-between mb-2">
          <span class="font-semibold text-sm tracking-tight">{{ e.name }}</span>
          <span class="text-[11px] text-zinc-400">{{ e.context }}</span>
        </div>
        <select :value="state[e.id]" @click.stop @change="(ev) => setState(e.id, Number(ev.target.value))"
                class="w-full rounded-lg px-2.5 py-1.5 font-mono text-sm font-semibold ring-1 ring-inset focus:outline-none cursor-pointer"
                :class="pal(e.color).soft">
          <option v-for="s in statesOf(e)" :key="s.id" :value="s.id">{{ s.virtual ? '— non esiste —' : s.name }}{{ s.is_final ? '  (finale)' : '' }}</option>
        </select>
        <div class="flex gap-3 mt-2 text-[11px] text-zinc-500">
          <span class="flex items-center gap-1"><span class="size-1.5 rounded-full bg-emerald-500" />{{ actions.filter((a) => a.entity_id === e.id && a.status === 'enabled').length }}</span>
          <span class="flex items-center gap-1"><span class="size-1.5 rounded-full bg-amber-500" />{{ actions.filter((a) => a.entity_id === e.id && a.status === 'conflict').length }}</span>
          <span class="flex items-center gap-1"><span class="size-1.5 rounded-full bg-zinc-400" />{{ actions.filter((a) => a.entity_id === e.id && a.status === 'blocked').length }}</span>
          <span v-if="entity(e.id).states.find((s) => s.id === state[e.id])?.is_final" class="ml-auto text-zinc-400">concluso</span>
        </div>
      </div>
    </aside>

    <!-- AZIONI -->
    <section class="space-y-4 min-w-0">
      <TransitionGroup name="fade">
        <div v-for="v in violations" :key="v.id"
             class="rounded-2xl p-4 flex gap-3 ring-1"
             :class="v.severity === 'error' ? 'bg-rose-50 ring-rose-200 text-rose-900' : 'bg-amber-50 ring-amber-200 text-amber-900'">
          <i class="fa-solid fa-shield-halved fa-fw text-[18px] shrink-0 mt-0.5" />
          <div>
            <div class="font-semibold text-sm">Invariante violata: {{ v.name }}</div>
            <div class="text-sm opacity-80">{{ v.description }}</div>
          </div>
        </div>
      </TransitionGroup>

      <div class="flex items-center justify-between">
        <div class="inline-flex rounded-xl bg-zinc-100 p-1">
          <button v-for="f in FILTERS" :key="f.key" @click="filter = f.key"
                  class="px-3 py-1.5 text-sm rounded-lg transition cursor-pointer"
                  :class="filter === f.key ? 'bg-white shadow-sm text-zinc-900 font-medium' : 'text-zinc-500 hover:text-zinc-800'">
            {{ f.label }}
            <span class="ml-1 text-xs tabular-nums text-zinc-400">{{ f.match.reduce((n, s) => n + counts[s], 0) }}</span>
          </button>
        </div>
        <div class="hidden 2xl:flex gap-3 text-xs text-zinc-500 whitespace-nowrap">
          <span v-for="(s, k) in STATUS" :key="k" class="flex items-center gap-1.5"><span class="size-2 rounded-full" :class="s.dot" />{{ s.label }}</span>
        </div>
      </div>

      <div v-if="!grouped.length" class="card p-10 text-center text-sm text-zinc-500">
        Nessuna azione eseguibile da questa combinazione di stati.
        <span v-if="ents.some((e) => !entity(e.id).states.find((s) => s.id === state[e.id])?.is_final)" class="block mt-1 font-medium text-rose-600">
          <i class="fa-solid fa-triangle-exclamation" /> Possibile deadlock: alcune entità non sono in uno stato finale.
        </span>
      </div>

      <div v-for="g in grouped" :key="g.e.id" class="card overflow-hidden">
        <div class="flex items-center gap-2 px-5 py-2.5 border-b border-zinc-100 bg-zinc-50/60">
          <span class="size-2 rounded-full" :class="pal(g.e.color).dot" />
          <span class="text-sm font-semibold">{{ g.e.name }}</span>
          <StateChip :id="state[g.e.id]" size="xs" />
        </div>
        <div class="divide-y divide-zinc-100">
          <div v-for="a in g.items" :key="a.transition_id" class="px-5 py-3.5"
               :class="a.status === 'unavailable' ? 'opacity-50' : ''">
            <div class="flex items-center gap-2.5 flex-wrap">
              <span class="size-2 rounded-full" :class="STATUS[a.status].dot" />
              <span class="font-semibold text-sm text-zinc-900">{{ a.action }}</span>
              <span v-if="a.create" class="tag bg-indigo-50 text-indigo-700 ring-indigo-200"><i class="fa-solid fa-seedling" /> creazione</span>
              <span v-if="a.actor" class="tag bg-zinc-50 text-zinc-500 ring-zinc-200"><i class="fa-solid fa-user fa-fw text-[11px]" />{{ a.actor }}</span>
              <span class="flex items-center gap-1.5">
                <StateChip :id="a.from" size="xs" /><i class="fa-solid fa-arrow-right fa-fw text-[11px] text-zinc-300" /><StateChip :id="a.to" size="xs" />
              </span>
              <div class="flex-1" />
              <span class="flex items-center gap-2 shrink-0 ml-auto">
              <span class="tag" :class="STATUS[a.status].cls">{{ STATUS[a.status].label }}</span>
              <button v-if="a.status === 'enabled'" class="btn-accent !py-1 !px-2.5 !text-xs" :disabled="busy" @click="run(a)">
                <i class="fa-solid fa-play fa-fw text-[11px]" /> Esegui
              </button>
              <button v-else-if="a.status === 'conflict' || a.status === 'blocked'" class="btn-outline !py-1 !px-2.5 !text-xs" :disabled="busy"
                      title="Esegui ignorando guardie e conflitti, per esplorare l'edge case" @click="run(a, true)">
                <i class="fa-solid fa-hand fa-fw text-[11px]" /> Forza
              </button>
              </span>
            </div>

            <p v-if="a.description" class="text-xs text-zinc-500 mt-1 ml-4.5">{{ a.description }}</p>

            <ul v-if="a.status === 'blocked' && a.reasons.length" class="mt-2 ml-4.5 space-y-1">
              <li v-for="r in a.reasons" :key="r" class="text-xs text-zinc-600 flex gap-1.5"><i class="fa-solid fa-lock fa-fw text-[11px] mt-0.5 shrink-0 text-zinc-400" />{{ r }}</li>
            </ul>
            <ul v-if="a.status === 'conflict'" class="mt-2 ml-4.5 space-y-1">
              <li v-for="r in a.reasons" :key="r" class="text-xs text-amber-800 flex gap-1.5"><i class="fa-solid fa-triangle-exclamation fa-fw text-[11px] mt-0.5 shrink-0" />{{ r }}</li>
            </ul>
            <div v-if="a.steps?.length > 1" class="mt-2 ml-4.5 flex flex-wrap gap-1.5">
              <span v-for="(s, i) in a.steps.slice(1)" :key="i" class="tag"
                    :class="s.forced ? 'bg-amber-50 text-amber-800 ring-amber-200' : 'bg-zinc-50 text-zinc-600 ring-zinc-200'">
                <i class="fa-solid fa-bolt fa-fw text-[11px]" />{{ ename(s.entity_id) }}: {{ sname(s.from) }} → {{ sname(s.to) }}
                <span v-if="!s.forced" class="text-zinc-400">via «{{ s.action }}»</span>
              </span>
            </div>
            <div v-if="a.introduces_violations?.length" class="mt-2 ml-4.5 text-xs text-rose-700 flex gap-1.5">
              <i class="fa-solid fa-shield-halved fa-fw text-[12px] shrink-0" /> Porta a violare: {{ a.introduces_violations.map((v) => v.name).join(', ') }}
            </div>
            <div v-if="a.rules?.length && a.status !== 'unavailable'" class="mt-2 ml-4.5 flex flex-wrap gap-1.5">
              <span v-for="r in a.rules" :key="r" class="tag bg-sky-50 text-sky-700 ring-sky-200"><i class="fa-solid fa-flag fa-fw text-[11px]" />da verificare: {{ r }}</span>
            </div>
          </div>
        </div>
      </div>
    </section>

    <!-- DIAGRAMMA + STORICO -->
    <aside class="space-y-6">
      <section class="card overflow-hidden">
        <div class="flex items-center gap-2 px-5 py-3 border-b border-zinc-100">
          <i class="fa-solid fa-diagram-project fa-fw text-[14px] text-zinc-400" />
          <span class="text-sm font-semibold">{{ focusEntity?.name }}</span>
          <select v-model="focus" class="ml-auto text-xs bg-transparent text-zinc-500 focus:outline-none cursor-pointer">
            <option v-for="e in ents" :key="e.id" :value="e.id">{{ e.name }}</option>
          </select>
        </div>
        <div class="p-3"><Mermaid :code="diagram" /></div>
      </section>

      <section class="card">
        <div class="flex items-center gap-2 px-5 py-3 border-b border-zinc-100">
          <i class="fa-solid fa-clock-rotate-left fa-fw text-[14px] text-zinc-400" />
          <span class="text-sm font-semibold">Storico</span>
          <button class="ml-auto btn-ghost !py-1 !text-xs" :disabled="!history.length" @click="undo(0)"><i class="fa-solid fa-arrow-rotate-left fa-fw text-[12px]" /> Annulla</button>
        </div>
        <div v-if="!history.length" class="px-5 py-6 text-xs text-zinc-400 text-center">Le azioni eseguite appariranno qui.</div>
        <ol class="px-5 py-3 space-y-0">
          <li v-for="(h, i) in history" :key="history.length - i" class="relative pl-6 pb-4 last:pb-1 group">
            <span class="absolute left-[5px] top-2 bottom-0 w-px bg-zinc-200 group-last:hidden" />
            <span class="absolute left-0 top-1 size-[11px] rounded-full ring-2 ring-white"
                  :class="h.kind === 'forced' ? 'bg-amber-500' : h.kind === 'manual' ? 'bg-zinc-300' : pal(entity(h.entity_id)?.color).dot" />
            <div class="flex items-center gap-2">
              <span class="text-sm" :class="h.kind === 'manual' ? 'text-zinc-500 italic' : 'font-medium'">
                <template v-if="h.kind !== 'manual'">{{ ename(h.entity_id) }} · </template>{{ h.label }}
              </span>
              <span v-if="h.kind === 'forced'" class="tag bg-amber-50 text-amber-800 ring-amber-200">forzata</span>
              <button class="ml-auto text-[11px] text-zinc-400 hover:text-indigo-600 opacity-0 group-hover:opacity-100 cursor-pointer" @click="undo(i)">torna qui</button>
            </div>
            <div v-if="h.steps?.length > 1" class="mt-1 space-y-0.5">
              <div v-for="(s, j) in h.steps.slice(1)" :key="j" class="text-[11px] text-zinc-500 flex items-center gap-1">
                <i class="fa-solid fa-bolt fa-fw text-[11px] text-amber-500" />{{ ename(s.entity_id) }} → {{ sname(s.to) }}
              </div>
            </div>
            <div v-for="c in h.conflicts || []" :key="c" class="text-[11px] text-amber-700 mt-0.5"><i class="fa-solid fa-triangle-exclamation fa-fw" /> {{ c }}</div>
            <div v-for="v in h.violations || []" :key="v.id" class="text-[11px] text-rose-700 mt-0.5"><i class="fa-solid fa-ban fa-fw" /> {{ v.name }}</div>
          </li>
        </ol>
      </section>
    </aside>
  </div>

  <Modal v-if="saving" title="Salva scenario" @close="saving = null">
    <form class="space-y-4" @submit.prevent="saveScenario">
      <div><label class="label">Nome</label><input v-model="saving.name" class="input" required placeholder="es. Annullo dopo incasso" /></div>
      <div><label class="label">Note</label><textarea v-model="saving.description" rows="3" class="input" placeholder="Cosa si vuole verificare" /></div>
      <div class="flex flex-wrap gap-1.5">
        <StateChip v-for="e in ents" :key="e.id" :id="state[e.id]" :entity-id="e.id" entity-prefix size="xs" />
      </div>
      <div class="flex justify-end"><button class="btn-accent" :disabled="!saving.name">Salva</button></div>
    </form>
  </Modal>
</template>
