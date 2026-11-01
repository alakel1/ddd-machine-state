<script setup>
// Azioni: per ogni azione, l'albero delle cascate (effetti → azioni dei figli → ...) in un posto solo. Si aggiungono e
// tolgono effetti, e si coprono gli stati da cui un effetto non ha ancora una transizione
import { computed, provide, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { store, api, mutate, entity, sname, toast } from '../store'
import { pal } from '../colors'
import { t } from '../i18n'
import { effectTargets, fromOf } from '../texts'
import PageHeader from '../components/PageHeader.vue'
import Modal from '../components/Modal.vue'
import ActionNode from '../components/ActionNode.vue'
import EffectEditor from '../components/EffectEditor.vue'
import HelpLink from '../components/HelpLink.vue'

const route = useRoute()
const router = useRouter()
const m = computed(() => store.model)
const query = ref('')
const showCascade = ref(false)

// azioni eseguibili dall'utente (le altre partono solo in cascata, e si vedono dentro gli alberi)
const groups = computed(() => m.value.entities.map((e) => ({
  e,
  list: e.transitions.filter((x) => (showCascade.value || (!x.cascade_only && !x.origin_effect_id))
    && `${e.name} ${x.action}`.toLowerCase().includes(query.value.trim().toLowerCase())),
})).filter((g) => g.list.length))
const allTransitions = computed(() => m.value.entities.flatMap((e) => e.transitions))
const selected = computed(() => allTransitions.value.find((x) => x.id === Number(route.query.t)) || groups.value[0]?.list[0])
const select = (x) => router.replace({ query: { ...route.query, t: x.id } })

// stati in cui il figlio si trova quando l'azione può partire, per ogni effetto "porta in stato" dell'albero
const suggestions = ref({})
function walk(tr, path, out) {
  if (path.includes(tr.id) || path.length >= 8) return
  tr.effects.forEach((f, i) => {
    if (f.kind === 'state') out.push({ key: `${tr.id}:${i}`, owner_id: tr.entity_id, from: fromOf(tr), entity_id: f.entity_id })
    for (const c of effectTargets(f)) walk(c, [...path, tr.id], out)
  })
  return out
}
watch([selected, m], async ([root]) => {
  if (!root) return
  const qs = walk(root, [], [])
  if (!qs.length) return (suggestions.value = {})
  try {
    const res = await api.post(`/domains/${m.value.id}/effect-sources`, { queries: qs })
    suggestions.value = Object.fromEntries(qs.map((q, i) => [q.key, res[i]]))
  } catch {
    suggestions.value = {}
  }
}, { immediate: true })

// da dove l'effetto ha già una transizione (scritta a mano o generata); null = da qualsiasi stato
const coveredFrom = (f) => effectTargets(f).map((x) => x.from_state_id)
function uncovered(tr, i, f) {
  const ids = f.kind === 'state' ? suggestions.value[`${tr.id}:${i}`] : null
  const from = coveredFrom(f)
  if (!ids || from.includes(null)) return []
  return ids.filter((s) => s !== f.to_state_id && !from.includes(s))
}
const body = (tr) => JSON.parse(JSON.stringify(tr))
async function cover(tr, i, sid) {
  const b = body(tr)
  const f = b.effects[i]
  // un effetto scritto "a mano" passa a generare: si tengono gli stati che aveva già coperti
  f.sources = [...new Set([...(Array.isArray(f.sources) ? f.sources : coveredFrom(f).filter((x) => x != null)), sid])]
  await mutate(api.put(`/transitions/${tr.id}`, b))
  toast(t('actions.covered'))
}

// modifica degli effetti di un'azione dell'albero
const editing = ref(null)
let key = 0
function edit(tr) {
  editing.value = body(tr)
}
function addEffect() {
  const target = m.value.entities.find((x) => x.id !== editing.value.entity_id)
  editing.value.effects.push({ entity_id: target?.id, kind: 'state', to_state_id: target?.states[0]?.id, action: '', skip_final: 0,
    description: '', sources: [], _suggest: true, _key: `n${++key}` })
}
async function save() {
  await mutate(api.put(`/transitions/${editing.value.id}`, editing.value))
  editing.value = null
  toast(t('entity.actionSaved'))
}
provide('cascade', { uncovered, cover, edit })
</script>

<template>
  <PageHeader :eyebrow="$t('actions.eyebrow')" :title="$t('actions.title')" :subtitle="$t('actions.subtitle')">
    <template #actions><HelpLink topic="actionTree" /></template>
  </PageHeader>
  <div class="p-8 grid gap-6 lg:grid-cols-[18rem_minmax(0,1fr)] items-start">
    <aside class="card p-3 space-y-3 lg:sticky lg:top-32 max-h-[calc(100vh-10rem)] overflow-y-auto">
      <input v-model="query" class="input !py-1.5" :placeholder="$t('actions.search')" />
      <label class="flex items-center gap-2 text-xs text-zinc-600 px-1 cursor-pointer">
        <input v-model="showCascade" type="checkbox" class="accent-indigo-600" /> {{ $t('actions.showCascade') }}
      </label>
      <div v-for="g in groups" :key="g.e.id">
        <div class="flex items-center gap-1.5 px-1 text-xs font-medium text-zinc-500 mb-1">
          <span class="size-1.5 rounded-full" :class="pal(g.e.color).dot" />{{ g.e.name }}
        </div>
        <button v-for="x in g.list" :key="x.id" type="button" class="w-full text-left text-sm rounded-lg px-2 py-1 transition"
                :class="selected?.id === x.id ? 'bg-indigo-50 text-indigo-700 font-medium' : 'text-zinc-700 hover:bg-zinc-50'" @click="select(x)">
          {{ x.action }}
          <span class="text-[11px] font-mono text-zinc-400 ml-1">{{ x.kind === 'create' ? '∅' : sname(x.from_state_id) }}</span>
          <span v-if="x.effects.length" class="text-[11px] text-amber-600 ml-1"><i class="fa-solid fa-bolt" /> {{ x.effects.length }}</span>
        </button>
      </div>
      <p v-if="!groups.length" class="text-sm text-zinc-400 px-1">{{ $t('actions.none') }}</p>
    </aside>
    <section v-if="selected" class="card p-6 min-w-0">
      <ActionNode :tr="selected" />
    </section>
  </div>

  <Modal v-if="editing" wide :title="$t('actions.effectsOf', { action: editing.action, entity: entity(editing.entity_id)?.name })" @close="editing = null">
    <div class="space-y-2">
      <p class="text-xs text-zinc-500">{{ $t('entity.effectsHint') }}</p>
      <EffectEditor v-for="(f, i) in editing.effects" :key="f.id ?? f._key" :f="f" :owner="editing.entity_id" :from="fromOf(editing)"
                    :entities="m.entities.filter((x) => x.id !== editing.entity_id)" @remove="editing.effects.splice(i, 1)" />
      <p v-if="!editing.effects.length" class="text-xs text-zinc-400 italic">{{ $t('actions.noEffects') }}</p>
      <button type="button" class="btn-ghost !text-xs" @click="addEffect"><i class="fa-solid fa-plus fa-fw text-[12px]" /> {{ $t('entity.effect.add') }}</button>
    </div>
    <template #footer>
      <button class="btn-ghost" @click="editing = null">{{ $t('common.cancel') }}</button>
      <button class="btn-accent" @click="save">{{ $t('common.save') }}</button>
    </template>
  </Modal>
</template>
