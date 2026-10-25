<script setup>
// "Risolvi": le soluzioni che l'analisi propone per una segnalazione, applicabili con un clic
import { ref } from 'vue'
import { store, api, mutate, toast } from '../store'
import { t } from '../i18n'
import Modal from './Modal.vue'

const props = defineProps({ issue: Object })
const emit = defineEmits(['close', 'applied'])
const busy = ref(null)

async function apply(fix, k) {
  busy.value = k
  try {
    await mutate(api.post(`/domains/${store.model.id}/fix`, { ops: fix.ops }))
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
            </div>
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
    </div>
    <template #footer>
      <RouterLink v-if="issue.entity_id" :to="`/d/${store.model.id}/entity/${issue.entity_id}`" class="btn-ghost" @click="emit('close')">
        {{ $t('analysis.openEntity') }}
      </RouterLink>
      <button class="btn-ghost" @click="emit('close')">{{ $t('common.close') }}</button>
    </template>
  </Modal>
</template>
