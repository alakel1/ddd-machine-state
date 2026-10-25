<script setup>
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { store, api, reloadModel, loadDomains, ename, createEntity, toast } from '../store'
import { pal } from '../colors'
import { t, locale } from '../i18n'
import { contextMap, domainStateMap } from '../diagrams'
import PageHeader from '../components/PageHeader.vue'
import Modal from '../components/Modal.vue'
import Mermaid from '../components/Mermaid.vue'
import StateChip from '../components/StateChip.vue'
import RelationModal from '../components/RelationModal.vue'

const route = useRoute()
const router = useRouter()
const m = computed(() => store.model)
const showDeps = ref(true)
const diagram = computed(() => contextMap(m.value, { dependencies: showDeps.value }))

// ---------- tab del diagramma: context map (default) | stati di tutto il dominio, nell'URL ----------
const setQuery = (patch) => router.replace({ query: { ...route.query, ...patch } })
const view = computed(() => (route.query.view === 'states' ? 'states' : 'map'))
const stateCtx = computed(() => route.query.ctx ?? null) // null = tutti i contesti
const stateOpts = computed(() => ({
  effects: route.query.effects !== '0', guards: route.query.guards === '1', overlay: route.query.overlay !== '0',
}))
const contextNames = computed(() => [...new Set(m.value.entities.filter((e) => e.states.length).map((e) => e.context || ''))])
// le segnalazioni dell'analisi sul diagramma: si ricaricano quando cambia il modello
const analysis = ref(null)
async function loadAnalysis() {
  if (view.value !== 'states' || !stateOpts.value.overlay) return
  const res = await api.get(`/domains/${m.value.id}/analysis`)
  analysis.value = { issues: res.issues, at: new Date() }
}
watch([view, () => stateOpts.value.overlay, m, locale], loadAnalysis, { immediate: true })
const openErrors = computed(() => analysis.value?.issues.filter((i) => i.severity === 'error').length ?? 0)
const stateDiagram = computed(() => domainStateMap(m.value, {
  context: stateCtx.value, effects: stateOpts.value.effects, guards: stateOpts.value.guards,
  issues: stateOpts.value.overlay ? analysis.value?.issues : null,
}))
const editingDomain = ref(null)
const relation = ref(null)

const stats = computed(() => {
  const es = m.value.entities
  const ts = es.flatMap((e) => e.transitions)
  return [
    { label: t('model.stats.entities'), value: es.length },
    { label: t('model.stats.states'), value: es.reduce((n, e) => n + e.states.length, 0) },
    { label: t('model.stats.actions'), value: ts.length },
    { label: t('model.stats.guards'), value: ts.reduce((n, x) => n + x.guards.filter((g) => g.kind === 'state').length, 0) },
    { label: t('model.stats.effects'), value: ts.reduce((n, x) => n + x.effects.length, 0) },
    { label: t('model.stats.invariants'), value: m.value.invariants.length },
  ]
})

const contexts = computed(() => {
  const g = {}
  for (const e of m.value.entities) (g[e.context || t('model.noContext')] ||= []).push(e)
  return g
})

async function saveDomain() {
  await api.put(`/domains/${m.value.id}`, editingDomain.value)
  editingDomain.value = null
  await Promise.all([reloadModel(), loadDomains()])
}

async function addEntity() {
  const { id } = await createEntity(m.value.id)
  router.push(`/d/${m.value.id}/entity/${id}`)
  toast(t('toast.entityCreated'))
}

function openRelation(r) {
  relation.value = r || {}
}

async function removeRelation(r) {
  await api.del(`/relations/${r.id}`)
  await reloadModel()
}
</script>

<template>
  <PageHeader :eyebrow="$t('model.eyebrow')" :subtitle="m.description">
    <template #title>
      <h1 class="text-2xl font-semibold tracking-tight text-zinc-900 flex items-center gap-2">
        {{ m.name }}
        <button class="icon-btn" @click="editingDomain = { name: m.name, description: m.description }"><i class="fa-solid fa-pen fa-fw text-[14px]" /></button>
      </h1>
    </template>
    <template #actions>
      <button class="btn-outline" @click="openRelation()">
        <i class="fa-solid fa-code-branch fa-fw text-[14px]" /> {{ $t('model.relation') }}
      </button>
      <button class="btn-accent" @click="addEntity"><i class="fa-solid fa-plus fa-fw text-[14px]" /> {{ $t('model.entity') }}</button>
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

    <!-- Context map | diagramma degli stati del dominio -->
    <section class="card overflow-hidden">
      <div class="flex flex-wrap items-center justify-between gap-3 px-6 border-b border-zinc-100">
        <nav class="flex gap-5">
          <button v-for="v in ['map', 'states']" :key="v" @click="setQuery({ view: v === 'states' ? 'states' : undefined })"
                  class="relative py-4 text-sm transition cursor-pointer flex items-center gap-2"
                  :class="view === v ? 'text-zinc-900 font-semibold' : 'text-zinc-500 hover:text-zinc-800'">
            <i class="fa-solid fa-fw text-[13px] opacity-70" :class="v === 'map' ? 'fa-diagram-project' : 'fa-circle-nodes'" />
            {{ $t(v === 'map' ? 'model.contextMap' : 'model.states.title') }}
            <span v-if="view === v" class="absolute inset-x-0 -bottom-px h-0.5 rounded-full bg-indigo-600" />
          </button>
        </nav>
        <div v-if="view === 'map'" class="flex items-center gap-4 text-xs text-zinc-500">
          <span class="flex items-center gap-1.5"><span class="w-5 border-t border-zinc-500" /> {{ $t('model.legend.relation') }}</span>
          <span class="flex items-center gap-1.5"><span class="w-5 border-t border-dashed border-zinc-400" /> {{ $t('model.legend.dependsOn') }}</span>
          <span class="flex items-center gap-1.5"><span class="w-5 border-t-2 border-amber-500" /> {{ $t('model.legend.effect') }}</span>
          <label class="flex items-center gap-2 cursor-pointer pl-3 border-l border-zinc-200">
            <input v-model="showDeps" type="checkbox" class="accent-indigo-600" /> {{ $t('model.legend.stateDeps') }}
          </label>
        </div>
        <div v-else class="flex flex-wrap items-center gap-4 text-xs text-zinc-500 py-2">
          <select :value="stateCtx ?? '*'" @change="(ev) => setQuery({ ctx: ev.target.value === '*' ? undefined : ev.target.value })"
                  class="input !w-auto !py-1 !text-xs">
            <option value="*">{{ $t('model.states.allContexts') }}</option>
            <option v-for="c in contextNames" :key="c" :value="c">{{ c || $t('model.noContext') }}</option>
          </select>
          <label class="flex items-center gap-1.5 cursor-pointer">
            <input type="checkbox" class="accent-indigo-600" :checked="stateOpts.effects" @change="(ev) => setQuery({ effects: ev.target.checked ? undefined : '0' })" />
            {{ $t('model.legend.effect') }}
          </label>
          <label class="flex items-center gap-1.5 cursor-pointer">
            <input type="checkbox" class="accent-indigo-600" :checked="stateOpts.guards" @change="(ev) => setQuery({ guards: ev.target.checked ? '1' : undefined })" />
            {{ $t('model.states.guards') }}
          </label>
          <label class="flex items-center gap-1.5 cursor-pointer pl-3 border-l border-zinc-200">
            <input type="checkbox" class="accent-indigo-600" :checked="stateOpts.overlay" @change="(ev) => setQuery({ overlay: ev.target.checked ? undefined : '0' })" />
            {{ $t('model.states.overlay') }}
          </label>
        </div>
      </div>

      <template v-if="view === 'states'">
        <!-- stato di maturità: il diagramma non va preso per una specifica finché l'analisi ha errori aperti -->
        <div v-if="stateOpts.overlay && analysis" class="flex items-center gap-2 px-6 py-2.5 text-xs border-b"
             :class="openErrors ? 'bg-amber-50 text-amber-900 border-amber-100' : 'bg-emerald-50 text-emerald-800 border-emerald-100'">
          <i class="fa-solid fa-fw" :class="openErrors ? 'fa-triangle-exclamation' : 'fa-circle-check'" />
          <span v-if="openErrors">
            <b>{{ $t('model.states.draft') }}</b> · {{ $t('model.states.openErrors', openErrors) }}
            {{ $t('model.states.at', { time: analysis.at.toLocaleTimeString(locale, { hour: '2-digit', minute: '2-digit' }) }) }}
          </span>
          <span v-else>{{ $t('model.states.clean') }}</span>
          <RouterLink :to="`/d/${m.id}/analysis`" class="ml-auto font-medium hover:underline">{{ $t('nav.analysis') }} →</RouterLink>
        </div>
        <div class="flex flex-wrap items-center gap-4 px-6 py-2.5 border-b border-zinc-100 text-xs text-zinc-500">
          <span class="flex items-center gap-1.5"><span class="w-5 border-t border-zinc-500" /> {{ $t('model.states.transition') }}</span>
          <span v-if="stateOpts.effects" class="flex items-center gap-1.5"><span class="w-5 border-t-2 border-dashed border-amber-500" /> {{ $t('model.legend.effect') }}</span>
          <span v-if="stateOpts.guards" class="flex items-center gap-1.5"><span class="w-5 border-t border-dashed border-zinc-400" /> {{ $t('model.states.guards') }}</span>
          <template v-if="stateOpts.overlay">
            <span class="flex items-center gap-1.5"><span class="w-5 border-t-2 border-rose-600" /> {{ $t('model.states.inError') }}</span>
            <span class="flex items-center gap-1.5"><span class="w-5 border-t border-dashed border-zinc-400" /> {{ $t('model.states.never') }}</span>
          </template>
        </div>
        <div class="p-6 bg-[radial-gradient(circle_at_1px_1px,#e4e4e7_1px,transparent_0)] [background-size:18px_18px]">
          <Mermaid :code="stateDiagram" natural :name="`${m.name} ${$t('model.states.title')}${stateCtx ? ` ${stateCtx}` : ''}`" />
        </div>
      </template>
      <div v-else class="p-6 bg-[radial-gradient(circle_at_1px_1px,#e4e4e7_1px,transparent_0)] [background-size:18px_18px]">
        <Mermaid :code="diagram" clickable :name="`${m.name} ${$t('model.contextMap')}`" @entity-click="(id) => router.push(`/d/${m.id}/entity/${id}`)" />
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
              <span class="tag" :class="pal(e.color).soft">{{ $t(`kind.${e.kind}`) }}</span>
            </div>
            <i class="fa-solid fa-arrow-right fa-fw text-[14px] text-zinc-300 group-hover:text-zinc-600 group-hover:translate-x-0.5 transition" />
          </div>
          <p class="text-sm text-zinc-500 mt-1 line-clamp-2 min-h-10">{{ e.description || '—' }}</p>
          <div class="flex flex-wrap gap-1 mt-3">
            <StateChip v-for="s in e.states" :key="s.id" :id="s.id" size="xs" />
            <span v-if="!e.states.length" class="text-xs text-zinc-400 italic">{{ $t('model.noStates') }}</span>
          </div>
          <div class="flex gap-4 mt-4 pt-3 border-t border-zinc-100 text-xs text-zinc-500">
            <span>{{ $t('model.attributeCount', e.attributes.length) }}</span>
            <span>{{ $t('model.actionCount', e.transitions.length) }}</span>
            <span class="flex items-center gap-1"><i class="fa-solid fa-lock fa-fw text-[11px]" />{{ e.transitions.reduce((n, t) => n + t.guards.length, 0) }}</span>
            <span class="flex items-center gap-1"><i class="fa-solid fa-bolt fa-fw text-[11px]" />{{ e.transitions.reduce((n, t) => n + t.effects.length, 0) }}</span>
          </div>
        </RouterLink>
      </div>
    </section>

    <!-- Relazioni -->
    <section class="card">
      <div class="flex items-center justify-between px-6 py-4 border-b border-zinc-100">
        <h2 class="font-semibold tracking-tight">{{ $t('model.relations') }}</h2>
        <button class="btn-ghost" @click="openRelation()">
          <i class="fa-solid fa-plus fa-fw text-[14px]" /> {{ $t('common.add') }}
        </button>
      </div>
      <div v-if="!m.relations.length" class="px-6 py-8 text-sm text-zinc-400 text-center">{{ $t('model.noRelations') }}</div>
      <div v-for="r in m.relations" :key="r.id" class="flex items-center gap-3 px-6 py-3 border-b border-zinc-100 last:border-0 group">
        <span class="font-medium text-sm">{{ ename(r.source_id) }}</span>
        <span class="text-sm text-zinc-500 italic">{{ r.label }}</span>
        <i class="fa-solid fa-arrow-right fa-fw text-[14px] text-zinc-300" />
        <span class="font-medium text-sm">{{ ename(r.target_id) }}</span>
        <span class="tag bg-zinc-50 text-zinc-600 ring-zinc-200 font-mono">{{ r.cardinality }}</span>
        <span v-if="r.composition" class="tag bg-zinc-800 text-white ring-zinc-800" :title="$t('model.compositionTitle', { child: ename(r.target_id), parent: ename(r.source_id) })">
          <i class="fa-solid fa-cubes-stacked" /> {{ $t('model.composition').toLowerCase() }}
        </span>
        <span v-if="r.composition && r.instances > 1" class="tag bg-indigo-50 text-indigo-700 ring-indigo-200" :title="$t('model.instancesTitle')">
          <i class="fa-solid fa-clone" /> {{ $t('model.instanceCount', r.instances) }}
        </span>
        <div class="flex-1" />
        <button class="icon-btn opacity-0 group-hover:opacity-100" @click="openRelation(r)"><i class="fa-solid fa-pen fa-fw text-[12px]" /></button>
        <button class="icon-btn opacity-0 group-hover:opacity-100 hover:!text-rose-600" @click="removeRelation(r)"><i class="fa-solid fa-trash-can fa-fw text-[12px]" /></button>
      </div>
    </section>
  </div>

  <Modal v-if="editingDomain" :title="$t('model.editDomain')" @close="editingDomain = null">
    <form class="space-y-4" @submit.prevent="saveDomain">
      <div><label class="label">{{ $t('common.name') }}</label><input v-model="editingDomain.name" class="input" required /></div>
      <div><label class="label">{{ $t('common.description') }}</label><textarea v-model="editingDomain.description" rows="4" class="input" /></div>
      <div class="flex justify-end"><button class="btn-accent">{{ $t('common.save') }}</button></div>
    </form>
  </Modal>

  <RelationModal v-if="relation" :relation="relation.id ? relation : null" @close="relation = null" />
</template>
