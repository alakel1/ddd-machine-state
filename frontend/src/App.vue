<script setup>
import { computed, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { store, loadDomains, loadModel, api, toast } from './store'
import { pal, COLORS } from './colors'

const route = useRoute()
const router = useRouter()
const domainId = computed(() => (route.params.domainId ? Number(route.params.domainId) : null))

loadDomains()
watch(domainId, (id) => {
  if (id && store.model?.id !== id) loadModel(id)
  if (!id) store.model = null
}, { immediate: true })

const nav = computed(() => [
  { to: 'model', label: 'Panoramica', icon: 'table-cells-large' },
  { to: 'invariants', label: 'Invarianti', icon: 'shield-halved', count: store.model?.invariants.length },
  { to: 'simulator', label: 'Simulatore', icon: 'play' },
  { to: 'analysis', label: 'Analisi edge case', icon: 'magnifying-glass-chart' },
])

async function addEntity() {
  const e = await api.post('/entities', {
    domain_id: domainId.value, name: 'NuovaEntità', pos: store.model.entities.length,
    color: COLORS[store.model.entities.length % COLORS.length],
  })
  await loadModel(domainId.value)
  router.push(`/d/${domainId.value}/entity/${e.id}`)
  toast('Entità creata')
}
</script>

<template>
  <div class="flex min-h-screen">
    <!-- Sidebar -->
    <aside class="w-64 shrink-0 bg-zinc-950 text-zinc-300 flex flex-col sticky top-0 h-screen">
      <RouterLink to="/" class="flex items-center gap-2.5 px-5 h-16 border-b border-white/5">
        <div class="size-8 rounded-lg bg-gradient-to-br from-indigo-500 to-violet-600 grid place-items-center shadow-lg shadow-indigo-900/50">
          <i class="fa-solid fa-cubes fa-fw text-[16px] text-white" />
        </div>
        <div class="leading-tight">
          <div class="text-sm font-semibold text-white tracking-tight">Machine Status</div>
          <div class="text-[11px] text-zinc-500">Domain-driven edge cases</div>
        </div>
      </RouterLink>

      <div class="px-3 pt-4">
        <label class="px-2 eyebrow !text-zinc-600">Dominio</label>
        <div class="relative mt-1.5">
          <select
            :value="domainId ?? ''"
            @change="(e) => router.push(e.target.value ? `/d/${e.target.value}/model` : '/')"
            class="w-full appearance-none rounded-lg bg-white/5 ring-1 ring-white/10 px-3 py-2 pr-8 text-sm text-white focus:outline-none focus:ring-indigo-500/60 cursor-pointer"
          >
            <option value="" class="text-zinc-900">— Tutti i domini —</option>
            <option v-for="d in store.domains" :key="d.id" :value="d.id" class="text-zinc-900">{{ d.name }}</option>
          </select>
          <i class="fa-solid fa-sort fa-fw text-[14px] absolute right-2.5 top-2.5 text-zinc-500 pointer-events-none" />
        </div>
      </div>

      <nav class="flex-1 overflow-y-auto px-3 py-4 space-y-6">
        <div v-if="!domainId">
          <RouterLink to="/" class="flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-sm bg-white/10 text-white">
            <i class="fa-solid fa-house fa-fw text-[14px]" /> Domini
          </RouterLink>
        </div>
        <template v-else>
          <div class="space-y-0.5">
            <RouterLink
              v-for="n in nav" :key="n.to" :to="`/d/${domainId}/${n.to}`"
              class="flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-sm transition hover:bg-white/5 hover:text-white"
              active-class="!bg-white/10 !text-white"
            >
              <i class="fa-solid fa-fw text-[13px] opacity-80" :class="`fa-${n.icon}`" />
              <span class="flex-1">{{ n.label }}</span>
              <span v-if="n.count" class="text-[11px] text-zinc-500 tabular-nums">{{ n.count }}</span>
            </RouterLink>
          </div>

          <div>
            <div class="flex items-center justify-between px-2.5 mb-1.5">
              <span class="eyebrow !text-zinc-600">Entità</span>
              <button class="icon-btn !size-6 !text-zinc-500 hover:!bg-white/10 hover:!text-white" title="Nuova entità" @click="addEntity">
                <i class="fa-solid fa-plus fa-fw text-[12px]" />
              </button>
            </div>
            <div class="space-y-0.5">
              <RouterLink
                v-for="e in store.model?.entities || []" :key="e.id" :to="`/d/${domainId}/entity/${e.id}`"
                class="flex items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-sm transition hover:bg-white/5 hover:text-white"
                active-class="!bg-white/10 !text-white"
              >
                <span class="size-2 rounded-full" :class="pal(e.color).dot" />
                <span class="flex-1 truncate">{{ e.name }}</span>
                <span class="text-[11px] text-zinc-600 tabular-nums">{{ e.states.length }}</span>
              </RouterLink>
            </div>
          </div>
        </template>
      </nav>

      <div v-if="domainId" class="p-3 border-t border-white/5 grid grid-cols-2 gap-2">
        <a :href="`/api/domains/${domainId}/export`" :download="`dominio-${domainId}.json`"
           class="flex items-center justify-center gap-1.5 rounded-lg py-2 text-xs bg-white/5 hover:bg-white/10 hover:text-white transition">
          <i class="fa-solid fa-file-code fa-fw text-[12px]" /> JSON
        </a>
        <a :href="`/api/domains/${domainId}/export.md`" :download="`specifica-${domainId}.md`"
           class="flex items-center justify-center gap-1.5 rounded-lg py-2 text-xs bg-white/5 hover:bg-white/10 hover:text-white transition">
          <i class="fa-solid fa-file-lines fa-fw text-[12px]" /> Specifica .md
        </a>
      </div>
    </aside>

    <main class="flex-1 min-w-0">
      <RouterView v-slot="{ Component }">
        <component :is="Component" v-if="!domainId || store.model?.id === domainId" :key="route.path" />
      </RouterView>
    </main>

    <!-- Toast -->
    <div class="fixed bottom-5 right-5 z-50 space-y-2">
      <TransitionGroup name="fade">
        <div v-for="t in store.toasts" :key="t.id"
             class="rounded-xl px-4 py-2.5 text-sm shadow-lg ring-1 max-w-sm"
             :class="t.kind === 'error' ? 'bg-rose-600 text-white ring-rose-700' : 'bg-zinc-900 text-white ring-zinc-800'">
          {{ t.message }}
        </div>
      </TransitionGroup>
    </div>
  </div>
</template>
