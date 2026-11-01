<script setup>
// specifica funzionale del dominio, impaginata per la lettura e la stampa (Stampa → Salva come PDF)
import { computed, ref, watch } from 'vue'
import { store, api, ename, sname, entity, plainTitle } from '../store'
import { pal } from '../colors'
import { locale } from '../i18n'
import { stateDiagram, contextMap } from '../diagrams'
import { guardText, effectText, isAlternative } from '../texts'
import Mermaid from '../components/Mermaid.vue'
import StateChip from '../components/StateChip.vue'
import Trace from '../components/Trace.vue'

const m = computed(() => store.model)
const today = computed(() => new Date().toLocaleDateString(locale.value, { dateStyle: 'long' }))
const relationsOf = (e) => m.value.relations.filter((r) => r.source_id === e.id || r.target_id === e.id)

// esito delle aspettative e cause aperte dell'analisi (le accettate restano fuori)
const expectations = ref(null)
const causes = ref(null)
async function load() {
  expectations.value = await api.get(`/domains/${m.value.id}/expectations`)
  const { issues } = await api.get(`/domains/${m.value.id}/analysis`)
  const groups = new Map()
  for (const i of issues) {
    if (i.severity === 'info' || (i.ack && !i.ack.stale)) continue
    const g = groups.get(i.cause.key) || groups.set(i.cause.key, { title: plainTitle(i.cause.title), severity: i.severity, n: 0 }).get(i.cause.key)
    g.n++
    if (i.severity === 'error') g.severity = 'error'
  }
  causes.value = [...groups.values()].sort((a, b) => (a.severity === 'error' ? 0 : 1) - (b.severity === 'error' ? 0 : 1) || b.n - a.n)
}
watch(() => [m.value?.id, locale.value], load, { immediate: true })
const print = () => window.print()
</script>

<template>
  <div class="spec max-w-4xl mx-auto px-10 py-12 print:p-0 print:max-w-none">
    <div class="flex items-start gap-4 print:hidden mb-8">
      <p class="flex-1 text-sm text-zinc-500">{{ $t('spec.hint') }}</p>
      <button class="btn-accent" @click="print"><i class="fa-solid fa-print fa-fw text-[14px]" /> {{ $t('spec.print') }}</button>
    </div>

    <!-- copertina -->
    <header class="border-b border-zinc-200 pb-8">
      <div class="eyebrow">{{ $t('spec.eyebrow') }}</div>
      <h1 class="text-4xl font-semibold tracking-tight mt-2">{{ m.name }}</h1>
      <p v-if="m.description" class="text-zinc-600 mt-3 leading-relaxed">{{ m.description }}</p>
      <p class="text-xs text-zinc-400 mt-4">{{ today }} · {{ $t('spec.counts', { entities: m.entities.length, invariants: m.invariants.length }) }}</p>
    </header>

    <!-- indice -->
    <nav class="mt-8">
      <h2 class="text-lg font-semibold">{{ $t('spec.contents') }}</h2>
      <ol class="mt-2 text-sm text-zinc-600 list-decimal pl-5 space-y-0.5">
        <li><a href="#map" class="hover:underline">{{ $t('spec.map') }}</a></li>
        <li v-for="e in m.entities" :key="e.id"><a :href="`#e${e.id}`" class="hover:underline">{{ e.name }}</a></li>
        <li><a href="#invariants" class="hover:underline">{{ $t('nav.invariants') }}</a></li>
        <li><a href="#expectations" class="hover:underline">{{ $t('nav.expectations') }}</a></li>
        <li><a href="#open" class="hover:underline">{{ $t('spec.open') }}</a></li>
      </ol>
    </nav>

    <section id="map" class="mt-12 break-before-page">
      <h2 class="text-2xl font-semibold tracking-tight">{{ $t('spec.map') }}</h2>
      <div class="mt-4 rounded-xl ring-1 ring-zinc-200 p-4"><Mermaid :code="contextMap(m, { dependencies: true })" :name="`${m.name} context map`" /></div>
    </section>

    <!-- entità -->
    <section v-for="e in m.entities" :id="`e${e.id}`" :key="e.id" class="mt-12 break-before-page">
      <div class="flex items-center gap-3">
        <span class="size-3 rounded-full" :class="pal(e.color).dot" />
        <h2 class="text-2xl font-semibold tracking-tight">{{ e.name }}</h2>
      </div>
      <p class="text-xs text-zinc-500 mt-1">{{ $t(`kind.${e.kind}`) }} · {{ e.context || $t('entity.noContext') }}</p>
      <p v-if="e.description" class="text-zinc-600 mt-2">{{ e.description }}</p>

      <template v-if="e.states.length">
        <h3 class="spec-h3">{{ $t('entity.states') }}</h3>
        <table class="spec-table">
          <thead><tr><th>{{ $t('spec.state') }}</th><th>{{ $t('common.description') }}</th><th>{{ $t('spec.kind') }}</th></tr></thead>
          <tbody>
            <tr v-for="s in e.states" :key="s.id">
              <td class="font-mono font-semibold">{{ s.name }}</td>
              <td>{{ s.description }}</td>
              <td class="whitespace-nowrap">
                {{ [s.is_initial && $t('state.initial'), s.is_final && $t('state.final'), $t(`state.liveness.${s.liveness || 'active'}`)].filter(Boolean).join(' · ') }}
              </td>
            </tr>
          </tbody>
        </table>
        <div class="mt-4 rounded-xl ring-1 ring-zinc-200 p-4 break-inside-avoid"><Mermaid :code="stateDiagram(e)" :name="`${e.name} states`" /></div>
      </template>

      <template v-if="e.transitions.length">
        <h3 class="spec-h3">{{ $t('spec.actions') }}</h3>
        <table class="spec-table">
          <thead><tr><th>{{ $t('spec.from') }}</th><th>{{ $t('spec.action') }}</th><th>{{ $t('spec.to') }}</th><th>{{ $t('entity.guards') }}</th><th>{{ $t('entity.effects') }}</th></tr></thead>
          <tbody>
            <tr v-for="tr in e.transitions" :key="tr.id" class="break-inside-avoid">
              <td class="font-mono text-xs">{{ tr.kind === 'create' ? $t('state.none') : tr.from_state_id ? sname(tr.from_state_id) : $t('state.any') }}</td>
              <td>
                <span class="font-medium">{{ tr.action }}</span>
                <span v-if="tr.actor" class="block text-xs text-zinc-500">{{ tr.actor }}</span>
                <span v-if="tr.cascade_only" class="block text-xs text-amber-700">{{ $t('spec.cascadeOnly') }}</span>
              </td>
              <td class="font-mono text-xs">{{ sname(tr.to_state_id) }}</td>
              <td class="text-xs">
                <div v-for="(g, gi) in tr.guards" :key="g.id"><span v-if="isAlternative(tr.guards, gi)" class="text-zinc-400">{{ $t('entity.or') }} </span>{{ guardText(g) }}</div>
              </td>
              <td class="text-xs"><div v-for="f in tr.effects" :key="f.id">{{ ename(f.entity_id) }} → {{ effectText(f) }}</div></td>
            </tr>
          </tbody>
        </table>
      </template>

      <template v-if="relationsOf(e).length">
        <h3 class="spec-h3">{{ $t('entity.rel.title') }}</h3>
        <ul class="text-sm text-zinc-700 space-y-1">
          <li v-for="r in relationsOf(e)" :key="r.id">
            {{ ename(r.source_id) }} <span class="text-zinc-400">{{ r.label || '→' }}</span> {{ ename(r.target_id) }}
            <span class="text-xs text-zinc-500">({{ r.cardinality }}<template v-if="r.composition">, {{ $t('model.composition').toLowerCase() }}</template>)</span>
          </li>
        </ul>
      </template>
    </section>

    <section id="invariants" class="mt-12 break-before-page">
      <h2 class="text-2xl font-semibold tracking-tight">{{ $t('nav.invariants') }}</h2>
      <p v-if="!m.invariants.length && !m.derived_invariants?.length" class="text-sm text-zinc-500 mt-2">{{ $t('spec.none') }}</p>
      <div v-for="inv in [...m.invariants, ...(m.derived_invariants || [])]" :key="inv.id" class="mt-4 break-inside-avoid">
        <h3 class="font-semibold">{{ inv.name }}</h3>
        <p v-if="inv.description" class="text-sm text-zinc-600">{{ inv.description }}</p>
        <p class="text-sm mt-1">
          <span class="text-xs font-semibold text-rose-600 uppercase">{{ $t('inv.forbiddenWhen') }}</span>
          {{ inv.rules.map((r) => guardText({ kind: 'state', ...r })).join(` ${$t('inv.and')} `) }}
        </p>
      </div>
    </section>

    <section id="expectations" class="mt-12">
      <h2 class="text-2xl font-semibold tracking-tight">{{ $t('nav.expectations') }}</h2>
      <p v-if="expectations && !expectations.length" class="text-sm text-zinc-500 mt-2">{{ $t('spec.none') }}</p>
      <div v-for="x in expectations || []" :key="x.id" class="mt-4 break-inside-avoid">
        <h3 class="font-semibold flex items-center gap-2">
          <i class="fa-solid" :class="x.result.ok ? 'fa-circle-check text-emerald-500' : 'fa-circle-xmark text-rose-500'" />{{ x.name }}
        </h3>
        <p class="text-sm text-zinc-600">
          <template v-if="x.kind === 'goal'">{{ $t('exp.reach') }}: {{ x.rules.map((r) => guardText({ kind: 'state', ...r })).join(` ${$t('inv.and')} `) }}</template>
          <template v-else>{{ $t(`exp.path.${x.expect}`) }}</template>
        </p>
        <div v-if="x.kind === 'path'" class="mt-1"><Trace :steps="x.trace" /></div>
      </div>
    </section>

    <section id="open" class="mt-12">
      <h2 class="text-2xl font-semibold tracking-tight">{{ $t('spec.open') }}</h2>
      <p class="text-sm text-zinc-500 mt-1">{{ $t('spec.openHint') }}</p>
      <p v-if="!causes" class="text-sm text-zinc-400 mt-2"><i class="fa-solid fa-circle-notch fa-spin" /> {{ $t('entity.errors.loading') }}</p>
      <p v-else-if="!causes.length" class="text-sm text-emerald-700 mt-2">{{ $t('spec.noOpen') }}</p>
      <ul v-else class="mt-3 space-y-1.5 text-sm">
        <li v-for="c in causes" :key="c.title" class="flex gap-2">
          <span class="mt-1.5 size-2 rounded-full shrink-0" :class="c.severity === 'error' ? 'bg-rose-500' : 'bg-amber-500'" />
          <span>{{ c.title }} <span v-if="c.n > 1" class="text-zinc-400">×{{ c.n }}</span></span>
        </li>
      </ul>
    </section>
  </div>
</template>

<style scoped>
@reference "../style.css";
.spec-h3 { @apply mt-6 mb-2 text-sm font-semibold uppercase tracking-wider text-zinc-500; }
.spec-table { @apply w-full text-sm border-collapse; }
.spec-table th { @apply text-left text-xs font-semibold text-zinc-500 border-b border-zinc-200 py-1.5 pr-3; }
.spec-table td { @apply align-top border-b border-zinc-100 py-1.5 pr-3; }
</style>
