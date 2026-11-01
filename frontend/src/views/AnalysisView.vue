<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { store, api, entity, ename, NONE, plainTitle } from '../store'
import { SEVERITY, pal } from '../colors'
import { locale } from '../i18n'
import PageHeader from '../components/PageHeader.vue'
import StateChip from '../components/StateChip.vue'
import Trace from '../components/Trace.vue'
import FixModal from '../components/FixModal.vue'
import AckBadge from '../components/AckBadge.vue'
import HelpLink from '../components/HelpLink.vue'

const route = useRoute()
const router = useRouter()
const m = computed(() => store.model)
const ents = computed(() => m.value.entities.filter((e) => e.states.length))
const res = ref(null)
const loading = ref(false)
// filtri iniziali dall'URL (es. dalla scheda di un'entità: ?entity=12&sev=error)
const sev = ref(route.query.sev || 'all')
const cat = ref(route.query.cat || 'all')
const focus = computed(() => Number(route.query.entity) || null)
const clearFocus = () => router.replace({ query: { ...route.query, entity: undefined } })
const fixing = ref(null) // segnalazione aperta in "Risolvi"

async function run() {
  loading.value = true
  try {
    res.value = await api.get(`/domains/${m.value.id}/analysis`)
    // nomi delle istanze simulate (es. "Riga #2"), usati da percorsi e chip
    store.instanceLabels = Object.fromEntries(Object.entries(res.value.labels || {}).map(([k, v]) => [Number(k), v]))
  } finally {
    loading.value = false
  }
}

// accettate o rimandate (e non da rivedere): fuori dai conteggi, visibili a richiesta
const isClosed = (i) => !!i.ack && !i.ack.stale
const showClosed = ref(false)
const inFocus = computed(() => (res.value?.issues || []).filter((i) => !focus.value || i.entity_ids.includes(focus.value)))
const closedCount = computed(() => inFocus.value.filter(isClosed).length)
const scoped = computed(() => inFocus.value.filter((i) => isClosed(i) === showClosed.value))
const categories = computed(() => [...new Set(scoped.value.map((i) => i.category))])
const causeKey = ref(null) // elenco filtrato sulle segnalazioni di una causa
const issues = computed(() =>
  scoped.value.filter((i) => (sev.value === 'all' || i.severity === sev.value) && (cat.value === 'all' || i.category === cat.value)
    && (!causeKey.value || i.cause.key === causeKey.value)),
)

// ---------- per causa: segnalazioni con la stessa causa si risolvono insieme ----------
const mode = ref(route.query.view === 'list' ? 'list' : 'causes')
const SEV_ORDER = { error: 0, warning: 1, info: 2 }
// per "Risolvi" sul gruppo: prima chi ha soluzioni pronte, poi la segnalazione più vicina alla causa
const CAT_ORDER = ['missing_transition', 'effect_conflict', 'composition', 'invariants']
const representative = (list) => [...list].sort((a, b) =>
  (b.fixes.length > 0) - (a.fixes.length > 0) || ((CAT_ORDER.indexOf(a.category) + 9) % 9) - ((CAT_ORDER.indexOf(b.category) + 9) % 9))[0]
const causes = computed(() => {
  const groups = new Map()
  for (const i of issues.value) {
    const g = groups.get(i.cause.key) || groups.set(i.cause.key, { key: i.cause.key, title: i.cause.title, issues: [] }).get(i.cause.key)
    g.issues.push(i)
  }
  return [...groups.values()]
    .map((g) => {
      const cats = {}
      for (const i of g.issues) cats[i.category] = (cats[i.category] || 0) + 1
      const severity = g.issues.reduce((s, i) => (SEV_ORDER[i.severity] < SEV_ORDER[s] ? i.severity : s), 'info')
      const symptoms = [...new Set(g.issues.map((i) => plainTitle(i.title)))]
      return { ...g, cats, severity, symptoms, fixIssue: representative(g.issues) }
    })
    // prima le cause risolvibili con un clic: i deadlock senza soluzione di solito ne sono una conseguenza
    .sort((a, b) => SEV_ORDER[a.severity] - SEV_ORDER[b.severity]
      || (b.fixIssue.fixes.length > 0) - (a.fixIssue.fixes.length > 0) || b.issues.length - a.issues.length)
})
function showCause(g) {
  causeKey.value = g.key
  mode.value = 'list'
}
// i contatori contano sempre le segnalazioni aperte
const bySev = (s) => inFocus.value.filter((i) => i.severity === s && !isClosed(i)).length
watch(closedCount, (n) => { if (!n) showClosed.value = false })

onUnmounted(() => (store.instanceLabels = {}))

// il simulatore ha un'istanza per entità: il backend indica se il percorso passa da "#2" o "#3"
const replayable = (i) => i.replayable
const multiInstances = computed(() =>
  Object.entries(res.value?.stats.instances || {}).map(([id, n]) => `${m.value.entities.find((e) => e.id === Number(id))?.name} ×${n}`),
)

// stato globale: le entità che non esistono sono la maggioranza e nascondono quelle che contano
const visibleState = (i) => Object.entries(i.state).filter(([, sid]) => i._showAll || sid !== NONE)
const missingCount = (i) => Object.values(i.state).filter((sid) => sid === NONE).length

function replay(i) {
  store.replay = { trace: i.trace }
  router.push(`/d/${m.value.id}/simulator`)
}

// ---------- matrice ----------
const a = ref(ents.value[0]?.id)
const b = ref(ents.value[1]?.id ?? ents.value[0]?.id)
const matrix = ref(null)
async function loadMatrix() {
  if (!a.value || !b.value) return
  matrix.value = await api.get(`/domains/${m.value.id}/matrix?a=${a.value}&b=${b.value}`)
}
watch([a, b], loadMatrix)
const cellAt = (sa, sb) => matrix.value?.cells.find((c) => c.a === sa && c.b === sb)
function cellClass(c) {
  if (!c) return ''
  if (c.violating) return 'bg-rose-500 text-white'
  if (c.reachable && c.forbidden) return 'bg-rose-500 text-white'
  if (c.reachable) return 'bg-emerald-100 text-emerald-800 hover:bg-emerald-200'
  if (c.forbidden) return 'striped text-rose-400'
  return 'bg-zinc-50 text-zinc-300'
}
const swap = () => ([a.value, b.value] = [b.value, a.value])

onMounted(() => { run(); loadMatrix() })
// titoli e dettagli arrivano tradotti dal backend
watch(locale, () => { run(); loadMatrix() })
</script>

<template>
  <PageHeader eyebrow="Model checking" :title="$t('analysis.title')" :subtitle="$t('analysis.subtitle')">
    <template #actions>
      <button class="btn-accent" :disabled="loading" @click="run(); loadMatrix()">
        <i class="fa-solid fa-arrows-rotate fa-fw text-[14px]" :class="loading && 'animate-spin'" /> {{ $t('analysis.rerun') }}
      </button>
    </template>
  </PageHeader>

  <div class="p-8 space-y-8">
    <!-- KPI -->
    <div v-if="loading && !res" class="card p-12 text-center">
      <i class="fa-solid fa-circle-notch fa-spin text-2xl text-indigo-500" />
      <p class="mt-3 text-sm font-medium text-zinc-700">{{ $t('analysis.loading') }}</p>
      <p class="text-xs text-zinc-500 mt-1">{{ $t('analysis.loadingHint') }}</p>
    </div>
    <div v-if="res" class="grid grid-cols-2 lg:grid-cols-5 gap-3">
      <div class="card px-5 py-4 lg:col-span-2 relative overflow-hidden">
        <i class="fa-solid fa-magnifying-glass-chart fa-fw absolute -right-4 -bottom-4 text-[100px] text-indigo-50" />
        <div class="relative">
          <div class="text-xs text-zinc-500">{{ $t('analysis.reachable') }}</div>
          <div class="mt-1 text-3xl font-semibold tabular-nums tracking-tight">
            {{ res.stats.reachable.toLocaleString(locale) }}
            <span class="text-base font-normal text-zinc-400">{{ $t('analysis.theoretical', { n: res.stats.theoretical.toLocaleString(locale) }) }}</span>
          </div>
          <div class="mt-2 h-1.5 rounded-full bg-zinc-100 overflow-hidden">
            <div class="h-full bg-gradient-to-r from-indigo-500 to-violet-500 rounded-full"
                 :style="{ width: Math.max(1, (100 * res.stats.reachable) / Math.max(1, res.stats.theoretical)) + '%' }" />
          </div>
          <div v-if="multiInstances.length" class="text-xs text-indigo-700 mt-1.5">
            <i class="fa-solid fa-clone" /> {{ $t('analysis.multiInstances', { list: multiInstances.join(', ') }) }}
          </div>
          <div v-if="res.stats.truncated" class="text-xs text-amber-700 mt-1.5 flex gap-1.5">
            <i class="fa-solid fa-triangle-exclamation mt-0.5" />
            <i18n-t scope="global" keypath="analysis.truncated" tag="span"><template #partial><b>{{ $t('analysis.partial') }}</b></template></i18n-t>
          </div>
        </div>
      </div>
      <button v-for="s in ['error', 'warning', 'info']" :key="s" @click="sev = sev === s ? 'all' : s"
              class="card px-5 py-4 text-left transition cursor-pointer"
              :class="sev === s ? '!ring-2 !ring-zinc-900' : 'hover:ring-zinc-300'">
        <div class="flex items-center gap-2 text-xs text-zinc-500"><span class="size-2 rounded-full" :class="SEVERITY[s].dot" />{{ $t(`severity.${s}`) }}</div>
        <div class="mt-1 text-3xl font-semibold tabular-nums tracking-tight">{{ bySev(s) }}</div>
      </button>
    </div>

    <div class="grid gap-8 2xl:grid-cols-[minmax(0,1fr)_minmax(0,0.9fr)]">
      <!-- ISSUE -->
      <section class="space-y-3 min-w-0">
        <div class="flex items-center gap-3 flex-wrap">
          <div class="inline-flex rounded-lg bg-zinc-100 p-0.5">
            <button v-for="v in ['causes', 'list']" :key="v" @click="mode = v; causeKey = null"
                    class="px-3 py-1 rounded-md text-xs transition cursor-pointer"
                    :class="mode === v ? 'bg-white shadow-sm text-zinc-900 font-medium' : 'text-zinc-500 hover:text-zinc-800'">
              <i class="fa-solid fa-fw text-[11px] mr-1" :class="v === 'causes' ? 'fa-sitemap' : 'fa-list'" />{{ $t(`analysis.view.${v}`) }}
            </button>
          </div>
          <span class="text-xs text-zinc-500">{{ $t('analysis.view.summary', { issues: issues.length, causes: causes.length }) }}</span>
          <HelpLink topic="analysis" />
          <button v-if="causeKey" @click="causeKey = null"
                  class="inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-medium ring-1 bg-indigo-50 text-indigo-700 ring-indigo-200 cursor-pointer">
            {{ causes.find((g) => g.key === causeKey)?.title }} <i class="fa-solid fa-xmark text-[10px] opacity-60" />
          </button>
        </div>
        <div class="flex items-center gap-2 flex-wrap">
          <button v-if="focus" @click="clearFocus"
                  class="inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-medium ring-1 bg-indigo-50 text-indigo-700 ring-indigo-200 hover:ring-indigo-300 transition cursor-pointer">
            <span class="size-1.5 rounded-full" :class="pal(entity(focus)?.color).dot" />{{ ename(focus) }}
            <i class="fa-solid fa-xmark text-[10px] opacity-60" />
          </button>
          <button v-for="c in ['all', ...categories]" :key="c" @click="cat = c"
                  class="rounded-full px-3 py-1 text-xs font-medium ring-1 transition cursor-pointer"
                  :class="cat === c ? 'bg-zinc-900 text-white ring-zinc-900' : 'bg-white text-zinc-600 ring-zinc-200 hover:ring-zinc-300'">
            {{ $t(c === 'all' ? 'analysis.allCategories' : `analysis.category.${c}`) }}
          </button>
          <button v-if="closedCount || showClosed" @click="showClosed = !showClosed; cat = 'all'"
                  class="ml-auto inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-medium ring-1 transition cursor-pointer"
                  :class="showClosed ? 'bg-emerald-600 text-white ring-emerald-600' : 'bg-white text-zinc-600 ring-zinc-200 hover:ring-zinc-300'">
            <i class="fa-solid fa-check-double text-[10px]" /> {{ showClosed ? $t('ack.hide') : $t('ack.show', { n: closedCount }) }}
          </button>
        </div>

        <div v-if="res && !issues.length" class="card p-10 text-center">
          <i class="fa-solid fa-circle-check fa-fw text-[28px] text-emerald-500 mx-auto" />
          <p class="mt-2 text-sm text-zinc-600">{{ $t('analysis.noIssues') }}</p>
        </div>

        <!-- per causa: una card per causa, con i sintomi e un solo "Risolvi" -->
        <template v-if="mode === 'causes'">
          <article v-for="g in causes" :key="g.key" class="card p-5 relative overflow-hidden">
            <div class="absolute left-0 inset-y-0 w-1" :class="SEVERITY[g.severity].dot" />
            <div class="flex items-start gap-3">
              <div class="flex-1 min-w-0">
                <div class="flex items-center gap-2 flex-wrap">
                  <span class="tag" :class="SEVERITY[g.severity].cls">{{ $t(`severity.${g.severity}`) }}</span>
                  <span v-for="(n, c) in g.cats" :key="c" class="tag bg-zinc-50 text-zinc-500 ring-zinc-200">
                    {{ $t(`analysis.category.${c}`) }}<template v-if="n > 1"> ×{{ n }}</template>
                  </span>
                </div>
                <h3 class="mt-2 font-semibold tracking-tight text-zinc-900">{{ g.title }}</h3>
                <ul v-if="g.symptoms.length > 1 || g.symptoms[0] !== g.title" class="mt-2 space-y-0.5 text-sm text-zinc-600">
                  <li v-for="s in g.symptoms.slice(0, 4)" :key="s" class="flex gap-2">
                    <i class="fa-solid fa-angle-right fa-fw text-[10px] text-zinc-300 mt-[6px] shrink-0" />{{ s }}
                  </li>
                </ul>
                <button class="mt-2 text-xs text-indigo-600 hover:underline cursor-pointer" @click="showCause(g)">
                  {{ $t('analysis.view.showIssues', g.issues.length) }}
                </button>
              </div>
              <button class="shrink-0 !text-xs" :class="g.fixIssue.fixes.length ? 'btn-accent' : 'btn-outline'" @click="fixing = g.fixIssue">
                <i class="fa-solid fa-wand-magic-sparkles fa-fw text-[12px]" /> {{ $t('fix.button') }}
              </button>
            </div>
          </article>
        </template>

        <template v-else>
        <article v-for="(i, k) in issues" :key="k" class="card p-5 relative overflow-hidden">
          <div class="absolute left-0 inset-y-0 w-1" :class="SEVERITY[i.severity].dot" />
          <div class="flex items-start gap-3">
            <div class="flex-1 min-w-0">
              <div class="flex items-center gap-2 flex-wrap">
                <span class="tag" :class="SEVERITY[i.severity].cls">{{ $t(`severity.${i.severity}`) }}</span>
                <span class="tag bg-zinc-50 text-zinc-500 ring-zinc-200">{{ $t(`analysis.category.${i.category}`) }}</span>
                <AckBadge :ack="i.ack" />
              </div>
              <h3 class="mt-2 font-semibold tracking-tight text-zinc-900">{{ i.title }}</h3>
              <p v-if="i.detail && !i.conflict" class="text-sm text-zinc-600 mt-0.5">{{ i.detail }}</p>
            </div>
            <RouterLink v-if="i.entity_id" :to="`/d/${m.id}/entity/${i.entity_id}`" class="btn-ghost !text-xs shrink-0">{{ $t('analysis.openEntity') }}</RouterLink>
            <button class="shrink-0 !text-xs" :class="i.fixes?.length ? 'btn-accent' : 'btn-outline'" @click="fixing = i">
              <i class="fa-solid fa-wand-magic-sparkles fa-fw text-[12px]" /> {{ $t('fix.button') }}
            </button>
          </div>
          <!-- conflitto: azione → effetti a cascata → dove si blocca e perché -->
          <div v-if="i.conflict" class="mt-3 rounded-xl bg-zinc-50/80 ring-1 ring-zinc-200 px-4 py-3">
            <div class="eyebrow mb-2">{{ $t('analysis.whatHappens') }}</div>
            <div class="space-y-1.5 text-sm">
              <div v-for="(s, j) in i.cascade" :key="j" class="flex items-center gap-2 flex-wrap" :style="{ paddingLeft: `${s.depth * 20}px` }">
                <span v-if="s.depth" class="font-mono text-zinc-300">└</span>
                <span class="size-1.5 rounded-full" :class="pal(entity(s.entity_id)?.color).dot" />
                <span class="text-zinc-500">{{ ename(s.entity_id) }}</span>
                <span class="font-medium text-zinc-800">{{ s.action }}</span>
                <StateChip :id="s.from" :entity-id="s.entity_id" size="xs" />
                <i class="fa-solid fa-arrow-right fa-fw text-[10px] text-zinc-300" />
                <StateChip :id="s.to" :entity-id="s.entity_id" size="xs" />
              </div>
              <div :style="{ paddingLeft: `${i.conflict.depth * 20}px` }">
                <div class="flex items-center gap-2 text-rose-700">
                  <span class="font-mono text-zinc-300">└</span>
                  <i class="fa-solid fa-circle-xmark fa-fw text-[13px]" />
                  <span class="font-medium">{{ i.conflict.what }}</span>
                </div>
                <div class="pl-[42px] mt-0.5 text-zinc-700">{{ i.conflict.reason }}</div>
                <ul v-if="i.conflict.details.length" class="pl-[42px] mt-1 space-y-0.5">
                  <li v-for="d in i.conflict.details" :key="d" class="flex gap-1.5 text-xs text-zinc-600">
                    <i class="fa-solid fa-lock fa-fw text-[10px] text-zinc-400 mt-[3px] shrink-0" />{{ d }}
                  </li>
                </ul>
              </div>
            </div>
            <!-- come si risolve, e su quale entità -->
            <div v-if="i.conflict.fix" class="mt-3 pt-2.5 border-t border-zinc-200/70 flex items-start gap-2 text-sm text-indigo-800">
              <i class="fa-solid fa-wrench fa-fw text-[11px] mt-[5px] shrink-0" />
              <span class="flex-1">{{ i.conflict.fix.text }}</span>
              <RouterLink v-if="i.conflict.fix.entity_id !== i.entity_id" :to="`/d/${m.id}/entity/${i.conflict.fix.entity_id}`"
                          class="btn-ghost !text-xs !py-0.5 shrink-0">{{ $t('analysis.openEntity') }}</RouterLink>
            </div>
          </div>
          <!-- deadlock: per ogni entità ferma, le azioni che escono dal suo stato e cosa le blocca -->
          <div v-if="i.deadlock" class="mt-3 rounded-xl bg-zinc-50/80 ring-1 ring-zinc-200 px-4 py-3">
            <div class="eyebrow mb-2">{{ $t('analysis.whyStuck') }}</div>
            <div class="space-y-2 text-sm">
              <div v-for="s in i.deadlock.stuck" :key="s.entity_id">
                <div class="flex items-center gap-2 flex-wrap">
                  <span class="size-1.5 rounded-full" :class="pal(entity(s.entity_id)?.color).dot" />
                  <span class="text-zinc-500">{{ ename(s.entity_id) }}</span>
                  <StateChip :id="s.state" :entity-id="s.entity_id" size="xs" />
                  <span v-if="!s.exits.length" class="text-xs text-zinc-500">{{ $t('analysis.noExit') }}</span>
                </div>
                <div v-for="x in s.exits" :key="x.transition_id" class="pl-5 mt-1">
                  <div class="flex items-center gap-2 text-rose-700">
                    <span class="font-mono text-zinc-300">└</span>
                    <i class="fa-solid fa-circle-xmark fa-fw text-[13px]" />
                    <span class="font-medium">{{ x.action }}</span>
                    <i class="fa-solid fa-arrow-right fa-fw text-[10px] text-zinc-300" />
                    <StateChip :id="x.to" :entity-id="s.entity_id" size="xs" />
                  </div>
                  <ul class="pl-[42px] mt-0.5 space-y-0.5">
                    <li v-for="r in x.reasons" :key="r" class="flex gap-1.5 text-xs text-zinc-600">
                      <i class="fa-solid fa-lock fa-fw text-[10px] text-zinc-400 mt-[3px] shrink-0" />{{ r }}
                    </li>
                  </ul>
                </div>
              </div>
            </div>
            <div v-if="i.deadlock.fix" class="mt-3 pt-2.5 border-t border-zinc-200/70 flex items-start gap-2 text-sm text-indigo-800">
              <i class="fa-solid fa-wrench fa-fw text-[11px] mt-[5px] shrink-0" />
              <span class="flex-1">{{ i.deadlock.fix.text }}</span>
              <RouterLink :to="`/d/${m.id}/entity/${i.deadlock.fix.entity_id}`" class="btn-ghost !text-xs !py-0.5 shrink-0">{{ $t('analysis.openEntity') }}</RouterLink>
            </div>
          </div>
          <div v-if="i.state" class="mt-3">
            <div class="eyebrow mb-1.5">{{ $t('analysis.reachedState') }}</div>
            <div class="flex flex-wrap items-center gap-1.5">
              <StateChip v-for="[eid, sid] in visibleState(i)" :key="eid" :id="sid" :entity-id="Number(eid)" entity-prefix size="xs" />
              <button v-if="missingCount(i)" class="text-[11px] text-zinc-400 hover:text-indigo-600 cursor-pointer" @click="i._showAll = !i._showAll">
                {{ i._showAll ? $t('analysis.hideMissing') : $t('analysis.showMissing', missingCount(i)) }}
              </button>
            </div>
          </div>
          <div v-if="i.trace" class="mt-3 pt-3 border-t border-zinc-100 flex items-center gap-3">
            <div class="flex-1 min-w-0">
              <div class="eyebrow mb-1.5">{{ $t('analysis.shortestPath', i.trace.length) }}</div>
              <Trace :steps="i.trace" />
            </div>
            <button class="btn-outline !text-xs shrink-0" :disabled="!replayable(i)" @click="replay(i)"
                    :title="$t(replayable(i) ? 'analysis.replayTitle' : 'analysis.notReplayable')">
              <i class="fa-solid fa-circle-play fa-fw text-[14px]" /> {{ $t('analysis.replay') }}
            </button>
          </div>
        </article>
        </template>
      </section>

      <!-- MATRICE -->
      <section class="card self-start 2xl:sticky 2xl:top-28 overflow-hidden">
        <div class="px-6 py-4 border-b border-zinc-100">
          <div class="flex items-center gap-2.5">
            <i class="fa-solid fa-table-cells fa-fw text-[14px] text-zinc-400" />
            <h2 class="font-semibold tracking-tight">{{ $t('analysis.matrix') }}</h2>
          </div>
          <p class="text-xs text-zinc-500 mt-1">{{ $t('analysis.matrixHint') }}</p>
          <div class="flex items-center gap-2 mt-3">
            <select v-model="a" class="input !py-1.5"><option v-for="e in ents" :key="e.id" :value="e.id">{{ e.name }}</option></select>
            <button class="icon-btn shrink-0" :title="$t('common.swap')" @click="swap"><i class="fa-solid fa-right-left fa-fw text-[14px]" /></button>
            <select v-model="b" class="input !py-1.5"><option v-for="e in ents" :key="e.id" :value="e.id">{{ e.name }}</option></select>
          </div>
        </div>
        <div v-if="matrix && entity(a) && entity(b)" class="p-5 overflow-x-auto">
          <table class="border-separate border-spacing-1 text-xs">
            <thead>
              <tr>
                <th class="text-right pr-2 align-bottom">
                  <span class="font-semibold" :class="pal(entity(a).color).text">{{ entity(a).name }}</span>
                  <span class="text-zinc-400"> ↓ / </span>
                  <span class="font-semibold" :class="pal(entity(b).color).text">{{ entity(b).name }} →</span>
                </th>
                <th v-for="sb in matrix.b_states" :key="sb.id" class="h-28 align-bottom pb-1">
                  <div class="font-mono font-medium text-zinc-600 [writing-mode:vertical-rl] rotate-180 mx-auto text-[11px]">{{ sb.name }}</div>
                </th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="sa in matrix.a_states" :key="sa.id">
                <td class="text-right pr-2 font-mono font-medium text-zinc-600 text-[11px] whitespace-nowrap">{{ sa.name }}</td>
                <td v-for="sb in matrix.b_states" :key="sb.id"
                    class="size-10 min-w-10 rounded-md text-center font-semibold tabular-nums transition"
                    :class="cellClass(cellAt(sa.id, sb.id))"
                    :title="[
                      `${sa.name} × ${sb.name}`,
                      cellAt(sa.id, sb.id)?.reachable ? $t('analysis.cellReachable', cellAt(sa.id, sb.id).reachable) : $t('analysis.neverReached'),
                      ...(cellAt(sa.id, sb.id)?.invariants || []).map((x) => $t('analysis.cellForbidden', { name: x })),
                    ].join('\n')">
                  {{ cellAt(sa.id, sb.id)?.reachable || (cellAt(sa.id, sb.id)?.forbidden ? '×' : '') }}
                </td>
              </tr>
            </tbody>
          </table>
          <div class="flex flex-wrap gap-4 mt-4 text-[11px] text-zinc-500">
            <span class="flex items-center gap-1.5"><span class="size-3 rounded bg-emerald-100" /> {{ $t('analysis.legend.reachable') }}</span>
            <span class="flex items-center gap-1.5"><span class="size-3 rounded bg-rose-500" /> {{ $t('analysis.legend.violating') }}</span>
            <span class="flex items-center gap-1.5"><span class="size-3 rounded striped ring-1 ring-rose-100" /> {{ $t('analysis.legend.forbidden') }} <i class="fa-solid fa-check text-emerald-600" /></span>
            <span class="flex items-center gap-1.5"><span class="size-3 rounded bg-zinc-50 ring-1 ring-zinc-200" /> {{ $t('analysis.neverReached') }}</span>
          </div>
        </div>
      </section>
    </div>
  </div>
  <FixModal v-if="fixing" :issue="fixing" @close="fixing = null" @applied="run(); loadMatrix()" @acked="run()" />
</template>
