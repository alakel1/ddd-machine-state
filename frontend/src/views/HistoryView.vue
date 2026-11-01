<script setup>
// storico del modello: ogni modifica con cosa ha cambiato; si confronta con oggi o si ripristina
import { computed, ref, watch } from 'vue'
import { store, api, mutate, toast, undo } from '../store'
import { t, locale } from '../i18n'
import PageHeader from '../components/PageHeader.vue'

const m = computed(() => store.model)
const versions = ref(null)
const open = ref({}) // id versione -> confronto con il modello attuale
const SHOWN = 6

async function load() {
  versions.value = await api.get(`/domains/${m.value.id}/history`)
}
// il modello cambia (modifica, annulla, ripristino): lo storico si aggiorna
watch(() => m.value, load, { immediate: true })
watch(locale, load)

// gli orari arrivano in UTC da SQLite ("2026-11-01 10:20:30")
const when = (s) => new Date(`${s.replace(' ', 'T')}Z`).toLocaleString(locale.value, { dateStyle: 'medium', timeStyle: 'short' })

async function compare(v) {
  if (open.value[v.id]) return delete open.value[v.id]
  open.value[v.id] = (await api.get(`/domains/${m.value.id}/compare?base=${v.id}`)).changes
}
async function restore(v) {
  if (!confirm(t('history.confirmRestore', { when: when(v.created_at) }))) return
  await mutate(api.post(`/domains/${m.value.id}/history/${v.id}/restore`))
  open.value = {}
  toast(t('history.restored'))
}
</script>

<template>
  <PageHeader :eyebrow="m.name" :title="$t('history.title')" :subtitle="$t('history.subtitle')">
    <template #actions>
      <button class="btn-outline" :disabled="!versions?.length" @click="undo(m.id)">
        <i class="fa-solid fa-rotate-left fa-fw text-[14px]" /> {{ $t('history.undo') }}
      </button>
    </template>
  </PageHeader>

  <div class="p-8 max-w-4xl">
    <div v-if="versions && !versions.length" class="card p-10 text-center text-sm text-zinc-500">
      <i class="fa-solid fa-clock-rotate-left text-2xl text-zinc-300" />
      <p class="mt-2">{{ $t('history.empty') }}</p>
    </div>

    <ol v-else-if="versions" class="relative border-l border-zinc-200 ml-3 space-y-4">
      <li v-for="(v, k) in versions" :key="v.id" class="ml-6">
        <span class="absolute -left-[7px] mt-5 size-3.5 rounded-full ring-4 ring-zinc-50"
              :class="k === 0 ? 'bg-indigo-600' : 'bg-zinc-300'" />
        <div class="card p-5">
          <div class="flex items-start gap-3">
            <div class="flex-1 min-w-0">
              <div class="text-xs text-zinc-400">{{ when(v.created_at) }}<span v-if="k === 0"> · {{ $t('history.latest') }}</span></div>
              <div class="font-medium text-zinc-900 mt-0.5">{{ v.label }}</div>
            </div>
            <button class="btn-ghost !text-xs shrink-0" @click="compare(v)">
              <i class="fa-solid fa-code-compare fa-fw text-[12px]" /> {{ $t(open[v.id] ? 'history.hideCompare' : 'history.compare') }}
            </button>
            <button class="btn-outline !text-xs shrink-0" @click="restore(v)">
              <i class="fa-solid fa-clock-rotate-left fa-fw text-[12px]" /> {{ $t('history.restore') }}
            </button>
          </div>
          <ul v-if="v.changes.length" class="mt-3 space-y-1 text-sm text-zinc-600">
            <li v-for="c in (v._all ? v.changes : v.changes.slice(0, SHOWN))" :key="c" class="flex gap-2">
              <i class="fa-solid fa-pen fa-fw text-[10px] text-zinc-300 mt-[5px] shrink-0" />{{ c }}
            </li>
            <li v-if="v.changes.length > SHOWN && !v._all">
              <button class="text-xs text-indigo-600 hover:underline cursor-pointer" @click="v._all = true">{{ $t('history.more', { n: v.changes.length - SHOWN }) }}</button>
            </li>
          </ul>
          <p v-else class="mt-2 text-xs text-zinc-400">{{ $t('history.noVisibleChange') }}</p>
          <!-- confronto con oggi: tutto ciò che si perde (o si ritrova) ripristinando questa versione -->
          <div v-if="open[v.id]" class="mt-3 rounded-xl bg-indigo-50/60 ring-1 ring-indigo-100 px-4 py-3">
            <div class="eyebrow !text-indigo-700 mb-1.5">{{ $t('history.sinceThen', open[v.id].length) }}</div>
            <ul class="space-y-1 text-sm text-zinc-700">
              <li v-for="c in open[v.id]" :key="c" class="flex gap-2"><i class="fa-solid fa-arrow-right fa-fw text-[10px] text-indigo-300 mt-[5px] shrink-0" />{{ c }}</li>
            </ul>
          </div>
        </div>
      </li>
    </ol>
  </div>
</template>
