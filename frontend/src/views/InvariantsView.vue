<script setup>
import { computed, ref } from 'vue'
import { store, api, reloadModel, ename, entity, toast } from '../store'
import { SEVERITY, pal } from '../colors'
import { t } from '../i18n'
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
  toast(t('inv.saved'))
}
async function remove(inv) {
  if (!confirm(t('common.confirmDelete', { name: inv.name }))) return
  await api.del(`/invariants/${inv.id}`)
  await reloadModel()
}
</script>

<template>
  <PageHeader :eyebrow="$t('inv.eyebrow')" :title="$t('inv.title')" :subtitle="$t('inv.subtitle')">
    <template #actions>
      <button class="btn-accent" @click="open()"><i class="fa-solid fa-plus fa-fw text-[14px]" /> {{ $t('inv.add') }}</button>
    </template>
  </PageHeader>

  <div class="p-8 space-y-3 max-w-5xl">
    <div v-if="!m.invariants.length" class="card p-12 text-center">
      <i class="fa-solid fa-shield-halved fa-fw text-[28px] text-zinc-300 mx-auto" />
      <p class="mt-3 text-sm text-zinc-500">{{ $t('inv.empty') }}</p>
      <button class="btn-accent mt-4" @click="open()"><i class="fa-solid fa-plus fa-fw text-[14px]" /> {{ $t('inv.createFirst') }}</button>
    </div>

    <div v-if="m.derived_invariants?.length" class="pt-4">
      <div class="eyebrow mb-2">{{ $t('inv.derived') }}</div>
      <div v-for="inv in m.derived_invariants" :key="inv.id" class="card p-5 mb-3 !bg-zinc-50/60">
        <div class="flex items-center gap-2">
          <i class="fa-solid fa-cubes-stacked text-zinc-500" />
          <h3 class="font-semibold tracking-tight">{{ inv.name }}</h3>
          <span class="tag" :class="SEVERITY.error.cls">{{ $t('severity.error') }}</span>
        </div>
        <p class="text-sm text-zinc-500 mt-0.5">{{ inv.description }} {{ $t('inv.derivedHint') }}</p>
        <div class="mt-3 flex flex-wrap items-center gap-2 text-sm">
          <span class="text-xs font-semibold text-rose-600 uppercase tracking-wide">{{ $t('inv.forbiddenWhen') }}</span>
          <template v-for="(r, i) in inv.rules" :key="i">
            <span v-if="i" class="text-xs font-semibold text-zinc-400">{{ $t('inv.and') }}</span>
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
            <span class="tag" :class="SEVERITY[inv.severity]?.cls">{{ $t(`severity.${inv.severity}`) }}</span>
          </div>
          <p v-if="inv.description" class="text-sm text-zinc-500 mt-0.5">{{ inv.description }}</p>
          <div class="mt-3 flex flex-wrap items-center gap-2 text-sm">
            <span class="text-xs font-semibold text-rose-600 uppercase tracking-wide">{{ $t('inv.forbiddenWhen') }}</span>
            <template v-for="(r, i) in inv.rules" :key="i">
              <span v-if="i" class="text-xs font-semibold text-zinc-400">{{ $t('inv.and') }}</span>
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

  <Modal v-if="draft" wide :title="$t(draft.id ? 'inv.edit' : 'inv.new')" @close="draft = null">
    <form id="iform" class="space-y-5" @submit.prevent="save">
      <div class="grid grid-cols-3 gap-3">
        <div class="col-span-2"><label class="label">{{ $t('common.name') }}</label><input v-model="draft.name" class="input" required :placeholder="$t('inv.namePlaceholder')" /></div>
        <div>
          <label class="label">{{ $t('inv.severity') }}</label>
          <select v-model="draft.severity" class="input"><option value="error">{{ $t('severity.error') }}</option><option value="warning">{{ $t('severity.warning') }}</option></select>
        </div>
      </div>
      <div><label class="label">{{ $t('common.description') }}</label><input v-model="draft.description" class="input" :placeholder="$t('inv.descriptionPlaceholder')" /></div>
      <div>
        <div class="flex items-center justify-between mb-2">
          <div class="text-sm font-semibold">{{ $t('inv.conditions') }} <span class="font-normal text-zinc-500">{{ $t('inv.conditionsHint') }}</span></div>
          <button type="button" class="btn-ghost !text-xs" @click="draft.rules.push(newRule())"><i class="fa-solid fa-plus fa-fw text-[12px]" /> {{ $t('inv.condition') }}</button>
        </div>
        <div class="space-y-2">
          <div v-for="(r, i) in draft.rules" :key="i" class="rounded-xl ring-1 ring-zinc-200 p-3 bg-zinc-50/50 space-y-2.5">
            <div class="flex items-center gap-2">
              <span v-if="i" class="text-xs font-semibold text-zinc-400 w-5">{{ $t('inv.and') }}</span>
              <select v-model="r.entity_id" class="input !w-48 !py-1.5" @change="r.state_ids = []">
                <option v-for="x in m.entities.filter((x) => x.states.length)" :key="x.id" :value="x.id">{{ x.name }}</option>
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
      <button class="btn-accent" form="iform" :disabled="!draft.name || !draft.rules.some((r) => r.state_ids.length)">{{ $t('common.save') }}</button>
    </template>
  </Modal>
</template>
