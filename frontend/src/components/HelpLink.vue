<script setup>
// "?" accanto a un concetto: porta alla sua voce nella Guida
import { computed } from 'vue'
import { useRoute } from 'vue-router'

const props = defineProps({ topic: String })
const route = useRoute()
const to = computed(() => ({ path: route.params.domainId ? `/d/${route.params.domainId}/guide` : '/guide', hash: `#${props.topic}` }))
</script>

<template>
  <RouterLink :to="to" :title="$t('guide.open')" class="inline-grid place-items-center size-4 rounded-full ring-1 ring-zinc-300 text-[9px] font-bold text-zinc-400 hover:text-indigo-600 hover:ring-indigo-300 align-middle transition" @click.stop>?</RouterLink>
</template>
