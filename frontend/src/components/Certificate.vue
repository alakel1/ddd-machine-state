<script setup>
// Certificato di verifica: cosa dimostra l'analisi, nel modello limitato, e con quali assunzioni
import { ref } from 'vue'
import HelpLink from './HelpLink.vue'

defineProps({ cert: Object })
const open = ref(false)
const VERDICT = {
  complete: { icon: 'fa-circle-check', cls: 'bg-emerald-50 ring-emerald-200 text-emerald-900', dot: 'text-emerald-600' },
  incomplete: { icon: 'fa-circle-half-stroke', cls: 'bg-amber-50 ring-amber-200 text-amber-900', dot: 'text-amber-600' },
  unverifiable: { icon: 'fa-circle-question', cls: 'bg-rose-50 ring-rose-200 text-rose-900', dot: 'text-rose-600' },
}
const STATUS = {
  ok: 'fa-check text-emerald-600',
  limit: 'fa-triangle-exclamation text-amber-600',
  assumption: 'fa-circle-info text-zinc-400',
}
</script>

<template>
  <section class="rounded-2xl ring-1 px-5 py-3.5" :class="VERDICT[cert.verdict].cls">
    <button type="button" class="w-full flex items-center gap-2.5 text-left cursor-pointer" @click="open = !open">
      <i class="fa-solid fa-fw text-[16px]" :class="[VERDICT[cert.verdict].icon, VERDICT[cert.verdict].dot]" />
      <span class="text-sm font-semibold">{{ $t('cert.title') }}</span>
      <span class="text-sm">{{ cert.headline }}</span>
      <span class="ml-auto text-xs opacity-70 whitespace-nowrap">{{ $t(open ? 'cert.hide' : 'cert.show') }}
        <i class="fa-solid fa-chevron-down text-[10px] transition" :class="open && 'rotate-180'" /></span>
    </button>
    <div v-if="open" class="mt-3 grid gap-4 lg:grid-cols-2 text-sm">
      <div>
        <div class="eyebrow mb-1.5 !text-current opacity-70">{{ $t('cert.dimensions') }} <HelpLink topic="certificate" /></div>
        <ul class="space-y-1.5">
          <li v-for="r in cert.rows" :key="r.key" class="flex gap-2">
            <i class="fa-solid fa-fw text-[12px] mt-1 shrink-0" :class="STATUS[r.status]" />
            <span><span class="font-medium">{{ $t(`cert.row.${r.key}`) }}</span> · {{ r.value }}</span>
          </li>
        </ul>
        <ul v-if="cert.rules.length" class="mt-2 pl-6 text-xs opacity-80 list-disc">
          <li v-for="(x, i) in cert.rules" :key="i">«{{ x.action }}» ({{ x.entity }}): {{ x.rule }}</li>
        </ul>
      </div>
      <div>
        <div class="eyebrow mb-1.5 !text-current opacity-70">{{ $t(`cert.mode.${cert.mode}`) }}</div>
        <ul class="space-y-1.5">
          <li v-for="o in cert.observations" :key="o.key" class="flex gap-2">
            <i class="fa-solid fa-check fa-fw text-[12px] mt-1 text-emerald-600 shrink-0" /><span>{{ o.value }}</span>
          </li>
        </ul>
      </div>
    </div>
  </section>
</template>
