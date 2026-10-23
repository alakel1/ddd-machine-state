<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { store, api, loadDomains, toast } from '../store'
import PageHeader from '../components/PageHeader.vue'
import Modal from '../components/Modal.vue'

const router = useRouter()
const creating = ref(null)
const fileInput = ref()

async function create() {
  const d = await api.post('/domains', creating.value)
  creating.value = null
  await loadDomains()
  router.push(`/d/${d.id}/model`)
}

async function duplicate(d) {
  await api.post(`/domains/${d.id}/duplicate`)
  await loadDomains()
  toast(`«${d.name}» duplicato`)
}

async function remove(d) {
  if (!confirm(`Eliminare definitivamente «${d.name}» e tutto il suo modello?`)) return
  await api.del(`/domains/${d.id}`)
  await loadDomains()
}

async function importFile(ev) {
  const f = ev.target.files[0]
  if (!f) return
  try {
    const { id } = await api.post('/domains/import', JSON.parse(await f.text()))
    await loadDomains()
    router.push(`/d/${id}/model`)
    toast('Dominio importato')
  } catch (e) {
    if (e instanceof SyntaxError) toast('File JSON non valido', 'error')
  } finally {
    ev.target.value = ''
  }
}
</script>

<template>
  <PageHeader eyebrow="Workspace" title="Domini"
              subtitle="Ogni dominio raccoglie entità, cicli di vita, regole tra entità e invarianti. Da qui parti per modellare e scovare gli edge case.">
    <template #actions>
      <input ref="fileInput" type="file" accept=".json" class="hidden" @change="importFile" />
      <button class="btn-outline" @click="fileInput.click()"><i class="fa-solid fa-upload fa-fw text-[14px]" /> Importa JSON</button>
      <button class="btn-accent" @click="creating = { name: '', description: '' }"><i class="fa-solid fa-plus fa-fw text-[14px]" /> Nuovo dominio</button>
    </template>
  </PageHeader>

  <div class="p-8 grid gap-5 sm:grid-cols-2 xl:grid-cols-3">
    <div v-for="d in store.domains" :key="d.id"
         class="card group p-6 flex flex-col hover:ring-indigo-300 hover:shadow-lg hover:shadow-indigo-500/5 transition cursor-pointer"
         @click="router.push(`/d/${d.id}/model`)">
      <div class="flex items-start justify-between">
        <div class="size-10 rounded-xl bg-gradient-to-br from-indigo-50 to-violet-100 ring-1 ring-indigo-100 grid place-items-center">
          <i class="fa-solid fa-cubes fa-fw text-[18px] text-indigo-600" />
        </div>
        <div class="flex gap-0.5 opacity-0 group-hover:opacity-100 transition" @click.stop>
          <button class="icon-btn" title="Duplica" @click="duplicate(d)"><i class="fa-solid fa-copy fa-fw text-[14px]" /></button>
          <button class="icon-btn hover:!text-rose-600" title="Elimina" @click="remove(d)"><i class="fa-solid fa-trash-can fa-fw text-[14px]" /></button>
        </div>
      </div>
      <h3 class="mt-4 font-semibold tracking-tight text-zinc-900 flex items-center gap-1">
        {{ d.name }} <i class="fa-solid fa-arrow-up-right-from-square fa-fw text-[14px] text-zinc-300 group-hover:text-indigo-500 transition" />
      </h3>
      <p class="mt-1 text-sm text-zinc-500 line-clamp-3 flex-1">{{ d.description || 'Nessuna descrizione' }}</p>
      <div class="mt-5 flex gap-4 text-xs text-zinc-500">
        <span class="flex items-center gap-1.5"><i class="fa-solid fa-cubes fa-fw text-[12px]" /> {{ d.entity_count }} entità</span>
        <span class="flex items-center gap-1.5"><i class="fa-solid fa-shield-halved fa-fw text-[12px]" /> {{ d.invariant_count }} invarianti</span>
      </div>
    </div>

    <button class="rounded-2xl border-2 border-dashed border-zinc-200 p-6 text-zinc-400 hover:border-indigo-300 hover:text-indigo-500 transition min-h-48 flex flex-col items-center justify-center gap-2 cursor-pointer"
            @click="creating = { name: '', description: '' }">
      <i class="fa-solid fa-plus fa-fw text-[22px]" />
      <span class="text-sm font-medium">Nuovo dominio</span>
    </button>
  </div>

  <Modal v-if="creating" title="Nuovo dominio" @close="creating = null">
    <form class="space-y-4" @submit.prevent="create">
      <div>
        <label class="label">Nome</label>
        <input v-model="creating.name" class="input" placeholder="es. Sinistri RC Auto" autofocus required />
      </div>
      <div>
        <label class="label">Descrizione</label>
        <textarea v-model="creating.description" rows="3" class="input" placeholder="Perimetro, attori, obiettivo dell'analisi…" />
      </div>
      <div class="flex justify-end"><button class="btn-accent" :disabled="!creating.name">Crea dominio</button></div>
    </form>
  </Modal>
</template>
