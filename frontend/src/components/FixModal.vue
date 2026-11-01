<script setup>
// "Risolvi": le soluzioni che l'analisi propone per una segnalazione, applicabili con un clic
import { ref } from 'vue'
import { store, api, mutate, toast } from '../store'
import { t } from '../i18n'
import Modal from './Modal.vue'

const props = defineProps({ issue: Object })
const emit = defineEmits(['close', 'applied', 'acked'])
const busy = ref(null)
const note = ref(props.issue.ack?.note || '')
// anteprima: cosa cambierebbe nell'analisi (calcolata dal backend senza toccare il modello)
const previews = ref({})
async function preview(fix, k) {
  previews.value[k] = 'loading'
  try {
    previews.value[k] = await api.post(`/domains/${store.model.id}/fix/preview`, { ops: fix.ops })
  } catch {
    delete previews.value[k]
  }
}
const previewText = (p) => {
  const parts = []
  const n = (key, c) => (c ? t(key, c) : null)
  const solved = [n('fix.preview.errors', p.resolved.error), n('fix.preview.warnings', p.resolved.warning)].filter(Boolean)
  const added = [n('fix.preview.errors', p.introduced.error), n('fix.preview.warnings', p.introduced.warning)].filter(Boolean)
  parts.push(solved.length ? t('fix.preview.solves', { what: solved.join(t('fix.preview.and')) }) : t('fix.preview.solvesNothing'))
  if (added.length) parts.push(t('fix.preview.introduces', { what: added.join(t('fix.preview.and')) }))
  return parts.join(' · ')
}

// "è voluto" (accepted) o "più avanti" (later): la segnalazione esce dai conteggi finché quella parte di modello non cambia
async function acknowledge(status) {
  const base = `/domains/${store.model.id}/acks`
  const i = props.issue
  if (status) await api.put(base, { fingerprint: i.fingerprint, context: i.context, status, note: note.value, title: i.title })
  else await api.del(`${base}/${i.fingerprint}`)
  toast(t(status ? `ack.done.${status}` : 'ack.reopened'))
  emit('acked')
  emit('close')
}

async function apply(fix, k) {
  busy.value = k
  try {
    await mutate(api.post(`/domains/${store.model.id}/fix`, { ops: fix.ops, label: fix.title }))
    toast(t('fix.applied'))
    emit('applied')
    emit('close')
  } finally {
    busy.value = null
  }
}
</script>

<template>
  <Modal :title="$t('fix.title')" wide @close="emit('close')">
    <div class="space-y-4">
      <div>
        <div class="text-sm font-semibold text-zinc-900">{{ issue.title }}</div>
        <p class="text-sm text-zinc-600 mt-0.5">
          {{ issue.conflict ? `${issue.conflict.what}, ${issue.conflict.reason}` : issue.detail }}
        </p>
      </div>

      <template v-if="issue.fixes?.length">
        <div class="eyebrow">{{ $t('fix.options', issue.fixes.length) }}</div>
        <div class="space-y-2">
          <div v-for="(f, k) in issue.fixes" :key="k" class="flex items-start gap-3 rounded-xl ring-1 ring-zinc-200 px-4 py-3 hover:ring-indigo-300 transition">
            <span class="mt-0.5 size-5 shrink-0 rounded-full grid place-items-center text-[11px] font-semibold"
                  :class="k === 0 ? 'bg-indigo-600 text-white' : 'bg-zinc-100 text-zinc-500'">{{ k + 1 }}</span>
            <div class="flex-1 min-w-0">
              <div class="text-sm font-medium text-zinc-900">
                {{ f.title }}
                <span v-if="k === 0 && issue.fixes.length > 1" class="tag bg-indigo-50 text-indigo-700 ring-indigo-200 ml-1">{{ $t('fix.suggested') }}</span>
              </div>
              <p class="text-xs text-zinc-500 mt-0.5">{{ f.detail }}</p>
              <p v-if="previews[k] === 'loading'" class="text-xs text-zinc-400 mt-1.5">
                <i class="fa-solid fa-circle-notch fa-spin fa-fw text-[11px]" /> {{ $t('fix.preview.loading') }}
              </p>
              <div v-else-if="previews[k]" class="text-xs mt-1.5 rounded-lg px-2.5 py-1.5"
                   :class="previews[k].introduced.error ? 'bg-amber-50 text-amber-800' : 'bg-emerald-50 text-emerald-800'">
                {{ previewText(previews[k]) }}
                <ul v-if="previews[k].introduced.titles.length" class="mt-1 list-disc pl-4 text-amber-800/90">
                  <li v-for="x in previews[k].introduced.titles" :key="x">{{ x }}</li>
                </ul>
              </div>
            </div>
            <button v-if="!previews[k]" class="btn-ghost !text-xs shrink-0" :disabled="busy !== null" @click="preview(f, k)">
              <i class="fa-solid fa-eye fa-fw text-[12px]" /> {{ $t('fix.preview.button') }}
            </button>
            <button class="btn-accent !text-xs shrink-0" :disabled="busy !== null" @click="apply(f, k)">
              <i class="fa-solid fa-fw text-[12px]" :class="busy === k ? 'fa-circle-notch fa-spin' : 'fa-wand-magic-sparkles'" /> {{ $t('fix.apply') }}
            </button>
          </div>
        </div>
        <p class="text-xs text-zinc-400">{{ $t('fix.undoHint') }}</p>
      </template>

      <div v-else class="rounded-xl bg-zinc-50 ring-1 ring-zinc-200 px-4 py-3 text-sm text-zinc-600">
        <p v-if="issue.conflict?.fix" class="flex gap-2 text-indigo-800">
          <i class="fa-solid fa-wrench fa-fw text-[11px] mt-[5px] shrink-0" />{{ issue.conflict.fix.text }}
        </p>
        <p v-else>{{ $te(`fix.manual.${issue.category}`) ? $t(`fix.manual.${issue.category}`) : $t('fix.manual.default') }}</p>
      </div>

      <!-- oppure non è un errore: si accetta (o si rimanda) con una nota -->
      <div class="rounded-xl ring-1 ring-zinc-200 px-4 py-3 space-y-2">
        <div class="text-sm font-medium text-zinc-900">{{ $t('ack.title') }}</div>
        <p v-if="issue.ack" class="text-xs" :class="issue.ack.stale ? 'text-amber-700' : 'text-zinc-500'">
          {{ $t(issue.ack.stale ? 'ack.staleHint' : `ack.current.${issue.ack.status}`) }}
        </p>
        <input v-model="note" class="input !py-1.5 !text-sm" :placeholder="$t('ack.notePlaceholder')" />
        <div class="flex flex-wrap gap-2">
          <button class="btn-outline !text-xs" @click="acknowledge('accepted')"><i class="fa-solid fa-check fa-fw text-[12px]" /> {{ $t('ack.accept') }}</button>
          <button class="btn-outline !text-xs" @click="acknowledge('later')"><i class="fa-solid fa-clock fa-fw text-[12px]" /> {{ $t('ack.later') }}</button>
          <button v-if="issue.ack" class="btn-ghost !text-xs" @click="acknowledge(null)"><i class="fa-solid fa-rotate-left fa-fw text-[12px]" /> {{ $t('ack.reopen') }}</button>
        </div>
      </div>
    </div>
    <template #footer>
      <RouterLink v-if="issue.entity_id" :to="`/d/${store.model.id}/entity/${issue.entity_id}`" class="btn-ghost" @click="emit('close')">
        {{ $t('analysis.openEntity') }}
      </RouterLink>
      <button class="btn-ghost" @click="emit('close')">{{ $t('common.close') }}</button>
    </template>
  </Modal>
</template>
