<script setup>
// aspettative: obiettivi che devono restare raggiungibili e percorsi che devono funzionare (o restare bloccati);
// si verificano a ogni analisi, come test di regressione del dominio
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { store, api, ename, entity, toast } from '../store'
import { pal } from '../colors'
import { t, locale } from '../i18n'
import PageHeader from '../components/PageHeader.vue'
import Modal from '../components/Modal.vue'
import StateChip from '../components/StateChip.vue'
import StatePicker from '../components/StatePicker.vue'
import Trace from '../components/Trace.vue'

const router = useRouter()
const m = computed(() => store.model)
const list = ref(null)
const loading = ref(false)
const draft = ref(null)

async function load() {
  loading.value = true
  try {
    list.value = await api.get(`/domains/${m.value.id}/expectations`)
  } finally {
    loading.value = false
  }
}
watch(() => m.value, load, { immediate: true })
watch(locale, load)

const passed = computed(() => (list.value || []).filter((x) => x.result.ok).length)

function newRule(i = 0) {
  const e = m.value.entities.filter((x) => x.states.length)[i]
  return e ? { entity_id: e.id, state_ids: [], negate: 0 } : null
}
function open(x) {
  draft.value = x ? JSON.parse(JSON.stringify(x)) : { kind: 'goal', name: '', description: '', expect: 'possible', rules: [newRule(0)].filter(Boolean), trace: [] }
}
async function save() {
  const d = draft.value
  const body = { domain_id: m.value.id, name: d.name, description: d.description, kind: d.kind, expect: d.expect,
    rules: (d.rules || []).filter((r) => r.state_ids.length), trace: d.trace || [] }
  if (d.id) await api.put(`/expectations/${d.id}`, body)
  else await api.post('/expectations', body)
  draft.value = null
  toast(t('exp.saved'))
  await load()
}
async function remove(x) {
  if (!confirm(t('common.confirmDelete', { name: x.name }))) return
  await api.del(`/expectations/${x.id}`)
  await load()
}
function replay(trace) {
  store.replay = { trace }
  router.push(`/d/${m.value.id}/simulator`)
}
</script>

<template>
  <PageHeader :eyebrow="$t('exp.eyebrow')" :title="$t('exp.title')" :subtitle="$t('exp.subtitle')">
    <template #actions>
      <button class="btn-outline" :disabled="loading" @click="load">
        <i class="fa-solid fa-arrows-rotate fa-fw text-[14px]" :class="loading && 'animate-spin'" /> {{ $t('exp.check') }}
      </button>
      <button class="btn-accent" @click="open()"><i class="fa-solid fa-plus fa-fw text-[14px]" /> {{ $t('exp.addGoal') }}</button>
    </template>
  </PageHeader>

  <div class="p-8 space-y-3 max-w-5xl">
    <div v-if="loading && !list" class="card p-10 text-center text-sm text-zinc-500">
      <i class="fa-solid fa-circle-notch fa-spin text-indigo-500" /> {{ $t('exp.checking') }}
    </div>
    <div v-else-if="list && !list.length" class="card p-12 text-center">
      <i class="fa-solid fa-bullseye fa-fw text-[28px] text-zinc-300 mx-auto" />
      <p class="mt-3 text-sm text-zinc-500 max-w-xl mx-auto">{{ $t('exp.empty') }}</p>
      <button class="btn-accent mt-4" @click="open()"><i class="fa-solid fa-plus fa-fw text-[14px]" /> {{ $t('exp.addGoal') }}</button>
    </div>
    <template v-else-if="list">
      <div class="text-sm text-zinc-500">
        <span class="font-semibold" :class="passed === list.length ? 'text-emerald-600' : 'text-rose-600'">{{ passed }}/{{ list.length }}</span>
        {{ $t('exp.summary') }}
      </div>

      <div v-for="x in list" :key="x.id" class="card p-5 group relative overflow-hidden">
        <div class="absolute left-0 inset-y-0 w-1" :class="x.result.ok ? 'bg-emerald-500' : 'bg-rose-500'" />
        <div class="flex items-start gap-3">
          <i class="fa-solid fa-fw text-[16px] mt-0.5" :class="x.result.ok ? 'fa-circle-check text-emerald-500' : 'fa-circle-xmark text-rose-500'" />
          <div class="flex-1 min-w-0">
            <div class="flex items-center gap-2 flex-wrap">
              <h3 class="font-semibold tracking-tight">{{ x.name }}</h3>
              <span class="tag bg-zinc-50 text-zinc-500 ring-zinc-200">
                <i class="fa-solid" :class="x.kind === 'goal' ? 'fa-bullseye' : 'fa-route'" />
                {{ $t(x.kind === 'goal' ? 'exp.goal' : `exp.path.${x.expect}`) }}
              </span>
            </div>
            <p v-if="x.description" class="text-sm text-zinc-500 mt-0.5">{{ x.description }}</p>

            <!-- obiettivo: la combinazione da raggiungere -->
            <div v-if="x.kind === 'goal'" class="mt-3 flex flex-wrap items-center gap-2 text-sm">
              <span class="text-xs font-semibold text-indigo-600 uppercase tracking-wide">{{ $t('exp.reach') }}</span>
              <template v-for="(r, i) in x.rules" :key="i">
                <span v-if="i" class="text-xs font-semibold text-zinc-400">{{ $t('inv.and') }}</span>
                <span class="inline-flex items-center gap-1.5 rounded-lg bg-zinc-50 ring-1 ring-zinc-200 px-2 py-1">
                  <span class="size-1.5 rounded-full" :class="pal(entity(r.entity_id)?.color).dot" />
                  <span class="font-medium text-xs">{{ ename(r.entity_id) }}</span>
                  <span class="text-xs text-zinc-400 font-mono">{{ r.negate ? '∉' : '∈' }}</span>
                  <StateChip v-for="sid in r.state_ids" :key="sid" :id="sid" size="xs" />
                </span>
              </template>
            </div>
            <!-- percorso: la sequenza di azioni -->
            <div v-else class="mt-3"><Trace :steps="x.trace" /></div>

            <!-- esito -->
            <div class="mt-3 text-sm">
              <template v-if="x.kind === 'goal'">
                <div v-if="x.result.ok" class="flex items-center gap-3">
                  <div class="flex-1 min-w-0">
                    <div class="text-emerald-700 text-xs mb-1.5">{{ $t('exp.reachedIn', x.result.trace.length) }}</div>
                    <Trace :steps="x.result.trace" />
                  </div>
                  <button class="btn-outline !text-xs shrink-0" @click="replay(x.result.trace)">
                    <i class="fa-solid fa-circle-play fa-fw text-[14px]" /> {{ $t('analysis.replay') }}
                  </button>
                </div>
                <p v-else class="text-rose-700">{{ $t(x.result.empty ? 'exp.noConditions' : 'exp.unreachable') }}</p>
              </template>
              <template v-else>
                <p v-if="x.result.ok" class="text-emerald-700">{{ $t(x.expect === 'possible' ? 'exp.pathOk' : 'exp.pathBlockedOk', { n: x.result.done + 1, action: x.result.failed }) }}</p>
                <p v-else-if="x.expect === 'possible'" class="text-rose-700">
                  {{ $t('exp.pathFails', { n: x.result.done + 1, action: x.result.failed }) }} <span class="text-zinc-500">{{ x.result.reason }}</span>
                </p>
                <p v-else class="text-rose-700">{{ $t('exp.pathShouldBlock') }}</p>
                <button v-if="x.expect === 'possible'" class="mt-2 btn-outline !text-xs" @click="replay(x.trace)">
                  <i class="fa-solid fa-circle-play fa-fw text-[14px]" /> {{ $t('analysis.replay') }}
                </button>
              </template>
            </div>
          </div>
          <div class="flex gap-0.5 opacity-0 group-hover:opacity-100 transition">
            <button class="icon-btn" @click="open(x)"><i class="fa-solid fa-pen fa-fw text-[14px]" /></button>
            <button class="icon-btn hover:!text-rose-600" @click="remove(x)"><i class="fa-solid fa-trash-can fa-fw text-[14px]" /></button>
          </div>
        </div>
      </div>
      <p class="text-xs text-zinc-400 pt-2"><i class="fa-solid fa-lightbulb" /> {{ $t('exp.pathHint') }}</p>
    </template>
  </div>

  <Modal v-if="draft" wide :title="$t(draft.id ? 'exp.edit' : 'exp.new')" @close="draft = null">
    <form id="xform" class="space-y-5" @submit.prevent="save">
      <div><label class="label">{{ $t('common.name') }}</label><input v-model="draft.name" class="input" required :placeholder="$t('exp.namePlaceholder')" /></div>
      <div><label class="label">{{ $t('common.description') }}</label><input v-model="draft.description" class="input" /></div>
      <div v-if="draft.kind === 'path'">
        <label class="label">{{ $t('exp.expect') }}</label>
        <select v-model="draft.expect" class="input">
          <option value="possible">{{ $t('exp.path.possible') }}</option>
          <option value="blocked">{{ $t('exp.path.blocked') }}</option>
        </select>
      </div>
      <div v-else>
        <div class="flex items-center justify-between mb-2">
          <div class="text-sm font-semibold">{{ $t('exp.conditions') }} <span class="font-normal text-zinc-500">{{ $t('exp.conditionsHint') }}</span></div>
          <button type="button" class="btn-ghost !text-xs" @click="draft.rules.push(newRule())"><i class="fa-solid fa-plus fa-fw text-[12px]" /> {{ $t('inv.condition') }}</button>
        </div>
        <div class="space-y-2">
          <div v-for="(r, i) in draft.rules" :key="i" class="rounded-xl ring-1 ring-zinc-200 p-3 bg-zinc-50/50 space-y-2.5">
            <div class="flex items-center gap-2">
              <span v-if="i" class="text-xs font-semibold text-zinc-400 w-5">{{ $t('inv.and') }}</span>
              <select v-model="r.entity_id" class="input !w-48 !py-1.5" @change="r.state_ids = []">
                <option v-for="e in m.entities.filter((e) => e.states.length)" :key="e.id" :value="e.id">{{ e.name }}</option>
              </select>
              <select v-model="r.negate" class="input !w-44 !py-1.5">
                <option :value="0">{{ $t('inv.inOneOf') }}</option>
                <option :value="1">{{ $t('inv.inNoneOf') }}</option>
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
      <button class="btn-ghost" @click="draft = null">{{ $t('common.cancel') }}</button>
      <button class="btn-accent" form="xform">{{ $t('common.save') }}</button>
    </template>
  </Modal>
</template>
