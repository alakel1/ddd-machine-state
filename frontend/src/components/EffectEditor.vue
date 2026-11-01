<script setup>
// Un effetto di un'azione (modificato sul posto, è una bozza). Per "porta in stato" si scelgono anche gli stati del
// figlio da cui l'effetto deve funzionare: le transizioni "solo in cascata" che mancano le crea e le mantiene il
// backend (editing.sync_generated), così la cascata si dichiara in un punto solo
import { computed, onMounted, ref } from 'vue'
import { store, api, entity, isCreatable } from '../store'
import { t } from '../i18n'
import HelpLink from './HelpLink.vue'

// owner: entità dell'azione; from: suo stato di partenza (0 = creazione, null = qualsiasi); entities: bersagli possibili
const props = defineProps({ f: Object, owner: Number, from: { type: Number, default: null }, entities: Array })
const emit = defineEmits(['remove'])
const f = props.f

const child = computed(() => entity(f.entity_id))
// nomi delle azioni (non di creazione) che l'effetto può far eseguire
const actions = computed(() => [...new Set((child.value?.transitions || []).filter((x) => x.kind !== 'create').map((x) => x.action))])
const candidates = computed(() => (child.value?.states || []).filter((s) => !s.is_final && s.id !== f.to_state_id))
// transizioni scritte a mano verso lo stato richiesto: da quegli stati non serve generare niente
const manual = computed(() => new Map((child.value?.transitions || [])
  .filter((x) => !x.origin_effect_id && x.kind !== 'create' && x.to_state_id === f.to_state_id)
  .map((x) => [x.from_state_id, x])))
const covered = (sid) => manual.value.get(sid) || manual.value.get(null)
const generating = computed(() => Array.isArray(f.sources))
const loading = ref(false)

// stati in cui il figlio si trova davvero quando l'azione può partire (dall'esplorazione); senza, tutti i candidati
async function suggest() {
  loading.value = true
  try {
    const [ids] = await api.post(`/domains/${store.model.id}/effect-sources`,
      { queries: [{ owner_id: props.owner, from: props.from, entity_id: f.entity_id }] })
    f.sources = ids.filter((id) => id !== f.to_state_id)
  } catch {
    f.sources = candidates.value.map((s) => s.id)
  } finally {
    loading.value = false
  }
}
function setGenerating(on) {
  if (on) suggest()
  else f.sources = null
}
function toggle(sid) {
  f.sources = f.sources.includes(sid) ? f.sources.filter((x) => x !== sid) : [...f.sources, sid]
}
function reset() {
  const was = generating.value
  f.kind = 'state'
  f.to_state_id = child.value?.states[0]?.id
  f.action = ''
  f.sources = null
  if (was) suggest()
}
function chipTitle(s) {
  if (!f.sources.includes(s.id)) return t('entity.effect.notFrom', { state: s.name })
  const c = covered(s.id)
  return c ? t('entity.effect.covered', { action: c.action }) : t('entity.effect.willGenerate', { state: s.name })
}
onMounted(() => {
  if (f._suggest) {
    delete f._suggest
    suggest()
  }
})
</script>

<template>
  <div class="rounded-xl ring-1 ring-amber-200 bg-amber-50/40 p-3 space-y-2">
    <div class="flex items-center gap-2">
      <select v-model="f.entity_id" class="input !w-44 !py-1.5" @change="reset">
        <option v-for="x in entities" :key="x.id" :value="x.id">{{ x.name }}</option>
      </select>
      <select v-model="f.kind" class="input !w-40 !py-1.5">
        <option value="state">{{ $t('entity.effect.toState') }}</option>
        <option value="action">{{ $t('entity.effect.runsAction') }}</option>
        <option v-if="isCreatable(child)" value="create">{{ $t('entity.effect.created') }}</option>
      </select>
      <select v-if="f.kind === 'state'" v-model="f.to_state_id" class="input !w-48 !py-1.5 font-mono"
              @change="generating && (f.sources = f.sources.filter((x) => x !== f.to_state_id))">
        <option v-for="s in child?.states || []" :key="s.id" :value="s.id">{{ s.name }}</option>
      </select>
      <select v-else-if="f.kind === 'action'" v-model="f.action" class="input !w-48 !py-1.5">
        <option v-for="a in actions" :key="a" :value="a">{{ a }}</option>
      </select>
      <span v-else class="w-48 text-xs text-zinc-500 px-1">{{ $t('entity.effect.createHint') }}</span>
      <label v-if="f.kind !== 'create'" class="flex items-center gap-1.5 text-xs text-zinc-600 whitespace-nowrap cursor-pointer"
             :title="$t('entity.effect.skipFinalHint')">
        <input type="checkbox" :checked="!!f.skip_final" class="accent-indigo-600" @change="(ev) => (f.skip_final = ev.target.checked ? 1 : 0)" />
        {{ $t('entity.effect.skipFinal') }} <HelpLink topic="skipFinal" />
      </label>
      <div class="flex-1" />
      <button type="button" class="icon-btn hover:!text-rose-600" @click="emit('remove')"><i class="fa-solid fa-xmark fa-fw text-[14px]" /></button>
    </div>
    <input v-model="f.description" class="input !py-1 !text-xs" :placeholder="$t('common.notes').toLowerCase()" />
    <!-- da quali stati del figlio: le transizioni "solo in cascata" che mancano le crea il tool -->
    <div v-if="f.kind === 'state'" class="flex flex-wrap items-center gap-1.5 text-xs">
      <label class="flex items-center gap-1.5 text-zinc-600 cursor-pointer mr-1">
        <input type="checkbox" class="accent-indigo-600" :checked="generating" @change="(ev) => setGenerating(ev.target.checked)" />
        {{ $t('entity.effect.generate') }} <HelpLink topic="effectSources" />
      </label>
      <template v-if="generating">
        <i v-if="loading" class="fa-solid fa-spinner fa-spin text-zinc-400" />
        <template v-else>
          <button v-for="s in candidates" :key="s.id" type="button" class="tag font-mono cursor-pointer transition" :title="chipTitle(s)"
                  :class="!f.sources.includes(s.id) ? 'bg-white text-zinc-400 ring-zinc-200 line-through'
                    : covered(s.id) ? 'bg-zinc-100 text-zinc-600 ring-zinc-300' : 'bg-indigo-600 text-white ring-indigo-600'"
                  @click="toggle(s.id)">
            {{ s.name }}<i v-if="f.sources.includes(s.id) && covered(s.id)" class="fa-solid fa-check text-[10px]" />
          </button>
          <span v-if="!candidates.length" class="text-zinc-400">{{ $t('entity.effect.noSources') }}</span>
          <button type="button" class="text-indigo-600 hover:underline ml-1" @click="suggest">{{ $t('entity.effect.suggest') }}</button>
        </template>
      </template>
      <span v-else class="text-zinc-400">{{ $t('entity.effect.manualHint') }}</span>
    </div>
  </div>
</template>
