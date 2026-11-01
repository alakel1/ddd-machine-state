<script setup>
// Un nodo dell'albero di un'azione: l'azione, i suoi effetti e, sotto ciascuno, le azioni del figlio che fa scattare
import { computed, inject } from 'vue'
import { store, entity, ename, sname, originOf } from '../store'
import { pal } from '../colors'
import { effectText, effectTargets } from '../texts'
import StateChip from './StateChip.vue'

defineOptions({ name: 'ActionNode' })
const props = defineProps({ tr: Object, path: { type: Array, default: () => [] } })
// uncovered(tr, i, f): stati del figlio da cui l'effetto non ha una transizione; edit(tr); cover(tr, i, sid)
const cascade = inject('cascade')
const MAX_DEPTH = 8

const owner = computed(() => entity(props.tr.entity_id))
const origin = computed(() => originOf(props.tr))
// la stessa azione più in alto nel ramo: la cascata torna su se stessa, non la si ridisegna
const loop = computed(() => props.path.includes(props.tr.id))
const deep = computed(() => props.path.length >= MAX_DEPTH)
</script>

<template>
  <div>
    <div class="flex items-center gap-2 flex-wrap">
      <span class="size-2 rounded-full" :class="pal(owner?.color).dot" />
      <RouterLink :to="`/d/${store.model.id}/entity/${tr.entity_id}`" class="text-sm text-zinc-500 hover:text-indigo-600">{{ owner?.name }}</RouterLink>
      <StateChip :id="tr.kind === 'create' ? 0 : tr.from_state_id" :entity-id="tr.entity_id" size="xs" />
      <i class="fa-solid fa-arrow-right fa-fw text-[10px] text-zinc-300" />
      <span class="text-sm font-semibold text-zinc-900">{{ tr.action }}</span>
      <i class="fa-solid fa-arrow-right fa-fw text-[10px] text-zinc-300" />
      <StateChip :id="tr.to_state_id" :entity-id="tr.entity_id" size="xs" />
      <span v-if="tr.guards.length" class="tag bg-zinc-100 text-zinc-600 ring-zinc-200"><i class="fa-solid fa-lock fa-fw text-[10px]" />{{ tr.guards.length }}</span>
      <span v-if="origin" class="tag bg-amber-50 text-amber-800 ring-amber-200"><i class="fa-solid fa-wand-magic-sparkles" /> {{ $t('actions.generated') }}</span>
      <span v-else-if="tr.cascade_only" class="tag bg-amber-50 text-amber-800 ring-amber-200"><i class="fa-solid fa-arrows-turn-right" /> {{ $t('entity.cascadeOnlyTag') }}</span>
      <button v-if="!loop" class="btn-ghost !text-xs !py-0.5" @click="cascade.edit(tr)"><i class="fa-solid fa-bolt fa-fw text-[11px]" /> {{ $t('actions.editEffects') }}</button>
    </div>
    <p v-if="loop" class="text-xs text-zinc-400 mt-1 pl-4"><i class="fa-solid fa-rotate-left" /> {{ $t('actions.loop') }}</p>
    <p v-else-if="deep && tr.effects.length" class="text-xs text-zinc-400 mt-1 pl-4">{{ $t('actions.tooDeep') }}</p>
    <ul v-else-if="tr.effects.length" class="mt-2 ml-1 pl-4 border-l-2 border-amber-200 space-y-3">
      <li v-for="(f, i) in tr.effects" :key="f.id ?? i">
        <div class="text-xs text-amber-800 flex items-center gap-1.5 flex-wrap">
          <i class="fa-solid fa-bolt fa-fw text-[11px]" />
          <span class="font-medium">{{ ename(f.entity_id) }}</span> → {{ effectText(f) }}
          <span v-if="Array.isArray(f.sources)" class="tag bg-white text-zinc-500 ring-zinc-200" :title="$t('actions.sourcesHint')">
            <i class="fa-solid fa-wand-magic-sparkles" /> {{ $t('actions.sources', { states: f.sources.map(sname).join(', ') || '—' }) }}
          </span>
        </div>
        <div v-if="cascade.uncovered(tr, i, f).length" class="flex flex-wrap items-center gap-1 mt-1">
          <span class="text-[11px] text-rose-700">{{ $t('actions.uncovered') }}</span>
          <button v-for="s in cascade.uncovered(tr, i, f)" :key="s" type="button" :title="$t('actions.coverHint', { state: sname(s) })"
                  class="tag font-mono bg-rose-50 text-rose-700 ring-rose-200 hover:ring-rose-400 cursor-pointer" @click="cascade.cover(tr, i, s)">
            {{ sname(s) }} <i class="fa-solid fa-plus text-[9px]" />
          </button>
        </div>
        <div class="mt-2 space-y-3">
          <ActionNode v-for="c in effectTargets(f)" :key="c.id" :tr="c" :path="[...path, tr.id]" />
          <p v-if="!effectTargets(f).length" class="text-xs text-rose-600">{{ $t('actions.noTarget') }}</p>
        </div>
      </li>
    </ul>
  </div>
</template>
