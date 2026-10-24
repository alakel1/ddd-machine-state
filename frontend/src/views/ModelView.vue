<script setup>
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { store, api, reloadModel, loadDomains, ename } from '../store'
import { pal, COLORS } from '../colors'
import { t } from '../i18n'
import { contextMap } from '../diagrams'
import PageHeader from '../components/PageHeader.vue'
import Modal from '../components/Modal.vue'
import Mermaid from '../components/Mermaid.vue'
import StateChip from '../components/StateChip.vue'
import RelationModal from '../components/RelationModal.vue'

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
  const e = await api.post('/entities', {
    domain_id: m.value.id, name: t('entity.defaultName'), pos: m.value.entities.length,
    color: COLORS[m.value.entities.length % COLORS.length],
  })
  await reloadModel()
  router.push(`/d/${m.value.id}/entity/${e.id}`)
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

    <!-- Context map -->
    <section class="card overflow-hidden">
      <div class="flex items-center justify-between px-6 py-4 border-b border-zinc-100">
        <div class="flex items-center gap-2.5">
          <i class="fa-solid fa-diagram-project fa-fw text-[14px] text-zinc-400" />
          <h2 class="font-semibold tracking-tight">{{ $t('model.contextMap') }}</h2>
        </div>
        <div class="flex items-center gap-4 text-xs text-zinc-500">
          <span class="flex items-center gap-1.5"><span class="w-5 border-t border-zinc-500" /> {{ $t('model.legend.relation') }}</span>
          <span class="flex items-center gap-1.5"><span class="w-5 border-t border-dashed border-zinc-400" /> {{ $t('model.legend.dependsOn') }}</span>
          <span class="flex items-center gap-1.5"><span class="w-5 border-t-2 border-amber-500" /> {{ $t('model.legend.effect') }}</span>
          <label class="flex items-center gap-2 cursor-pointer pl-3 border-l border-zinc-200">
            <input v-model="showDeps" type="checkbox" class="accent-indigo-600" /> {{ $t('model.legend.stateDeps') }}
          </label>
        </div>
      </div>
      <div class="p-6 bg-[radial-gradient(circle_at_1px_1px,#e4e4e7_1px,transparent_0)] [background-size:18px_18px]">
        <Mermaid :code="diagram" clickable @entity-click="(id) => router.push(`/d/${m.id}/entity/${id}`)" />
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
