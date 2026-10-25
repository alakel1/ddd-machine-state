<script setup>
import { ref, watch, onMounted } from 'vue'
import mermaid from 'mermaid'
import { svgToPng, download } from '../svgExport'
import { toast } from '../store'
import { t } from '../i18n'

// natural: dimensione reale con scorrimento, invece di rimpicciolire il diagramma alla larghezza disponibile
// name: nome del file PNG esportato
const props = defineProps({ code: String, clickable: Boolean, natural: Boolean, name: String })
// entity-click: clic su un nodo "e<id>" delle mappe delle entità
const emit = defineEmits(['entity-click'])
const html = ref('')
const error = ref('')
let seq = 0

mermaid.initialize({
  startOnLoad: false,
  securityLevel: 'loose',
  theme: 'base',
  fontFamily: 'Inter Variable, ui-sans-serif, system-ui',
  themeVariables: {
    fontSize: '13px',
    primaryColor: '#ffffff',
    primaryBorderColor: '#d4d4d8',
    primaryTextColor: '#27272a',
    lineColor: '#a1a1aa',
    secondaryColor: '#f4f4f5',
    tertiaryColor: '#fafafa',
    edgeLabelBackground: '#ffffff',
  },
  state: { useMaxWidth: true },
  flowchart: { curve: 'basis', padding: 14, htmlLabels: true },
})

async function render() {
  if (!props.code) { html.value = ''; return }
  try {
    const { svg } = await mermaid.render(`mm-${Date.now()}-${seq++}`, props.code)
    // a dimensione reale: larghezza dal viewBox, senza il max-width che Mermaid imposta
    const w = props.natural && svg.match(/viewBox="[\d.-]+ [\d.-]+ ([\d.]+)/)?.[1]
    html.value = w ? svg.replace(/width="100%"/, `width="${Math.ceil(w)}"`).replace(/max-width:\s*[\d.]+px;?/, '') : svg
    error.value = ''
  } catch (e) {
    error.value = String(e.message || e)
  }
}
function onClick(ev) {
  const node = ev.target.closest?.('.node')
  const m = node?.id.match(/flowchart-e(-?\d+)-/)
  if (m && props.clickable) emit('entity-click', Number(m[1]))
}

const host = ref(null)
const exporting = ref(false)
async function exportPng() {
  exporting.value = true
  try {
    download(await svgToPng(host.value.querySelector('svg')), props.name)
  } catch (e) {
    console.error(e)
    toast(t('mermaid.exportError'), 'error')
  } finally {
    exporting.value = false
  }
}

onMounted(render)
watch(() => props.code, render)
</script>

<template>
  <div class="relative group/mm">
    <button v-if="html" type="button" :disabled="exporting" :title="$t('mermaid.exportPng')" @click="exportPng"
            class="absolute top-0 right-0 z-10 btn-outline !px-2 !py-1 !text-xs opacity-0 group-hover/mm:opacity-100 focus:opacity-100 transition">
      <i class="fa-solid fa-fw text-[11px]" :class="exporting ? 'fa-circle-notch fa-spin' : 'fa-download'" /> PNG
    </button>
    <div v-if="html" ref="host" class="mermaid-host flex" :class="[clickable && 'clickable-nodes', natural ? 'natural overflow-auto' : 'justify-center']" v-html="html" @click="onClick" />
    <p v-else-if="!error" class="text-sm text-zinc-400 text-center py-10">{{ $t('mermaid.empty') }}</p>
    <p v-if="error" class="text-xs text-rose-600 font-mono">{{ error }}</p>
  </div>
</template>
