<script setup>
// guida: i concetti del modello con un esempio ciascuno; le voci hanno un'ancora (#topic) per i link "?"
import { nextTick, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import PageHeader from '../components/PageHeader.vue'

const TOPICS = [
  { group: 'model', items: ['entity', 'states', 'liveness', 'creation', 'actions', 'cascade'] },
  { group: 'rules', items: ['guards', 'guardsOr', 'guardsEach', 'effects', 'skipFinal', 'composition', 'instances', 'invariants'] },
  { group: 'analysis', items: ['analysis', 'missing', 'conflicts', 'deadlock', 'fix', 'ack', 'expectations', 'history'] },
]

const route = useRoute()
onMounted(async () => {
  await nextTick()
  if (route.hash) document.querySelector(route.hash)?.scrollIntoView({ block: 'start' })
})
</script>

<template>
  <PageHeader :eyebrow="$t('guide.eyebrow')" :title="$t('guide.title')" :subtitle="$t('guide.subtitle')" />
  <div class="p-8 grid gap-8 lg:grid-cols-[14rem_minmax(0,1fr)] max-w-6xl">
    <nav class="hidden lg:block sticky top-36 self-start text-sm space-y-4">
      <div v-for="g in TOPICS" :key="g.group">
        <div class="eyebrow mb-1">{{ $t(`guide.groups.${g.group}`) }}</div>
        <a v-for="k in g.items" :key="k" :href="`#${k}`" class="block py-0.5 text-zinc-600 hover:text-indigo-600">{{ $t(`guide.${k}.title`) }}</a>
      </div>
    </nav>
    <div class="space-y-10 min-w-0">
      <section v-for="g in TOPICS" :key="g.group" class="space-y-4">
        <h2 class="text-lg font-semibold tracking-tight">{{ $t(`guide.groups.${g.group}`) }}</h2>
        <article v-for="k in g.items" :id="k" :key="k" class="card p-5 scroll-mt-40"
                 :class="route.hash === `#${k}` && '!ring-2 !ring-indigo-400'">
          <h3 class="font-semibold text-zinc-900">{{ $t(`guide.${k}.title`) }}</h3>
          <p class="text-sm text-zinc-600 mt-1 leading-relaxed">{{ $t(`guide.${k}.body`) }}</p>
          <p class="text-sm mt-2 rounded-lg bg-indigo-50/70 text-indigo-900 px-3 py-2">
            <i class="fa-solid fa-lightbulb fa-fw text-[12px] text-indigo-400" /> {{ $t(`guide.${k}.example`) }}
          </p>
        </article>
      </section>
    </div>
  </div>
</template>
