<script setup>
// stato di una segnalazione accettata o rimandata; "da rivedere" se il modello è cambiato da allora
defineProps({ ack: Object })
</script>

<template>
  <span v-if="ack" class="tag" :title="ack.note || undefined"
        :class="ack.stale ? 'bg-amber-50 text-amber-700 ring-amber-200' : ack.status === 'later' ? 'bg-sky-50 text-sky-700 ring-sky-200' : 'bg-emerald-50 text-emerald-700 ring-emerald-200'">
    <i class="fa-solid text-[9px]" :class="ack.stale ? 'fa-rotate' : ack.status === 'later' ? 'fa-clock' : 'fa-check'" />
    {{ $t(ack.stale ? 'ack.badge.stale' : `ack.badge.${ack.status}`) }}<template v-if="ack.note"> · {{ ack.note }}</template>
  </span>
</template>
