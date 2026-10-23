<script setup>
import { ref, watch, onMounted } from 'vue'
import mermaid from 'mermaid'

const props = defineProps({ code: String })
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
    html.value = svg
    error.value = ''
  } catch (e) {
    error.value = String(e.message || e)
  }
}
onMounted(render)
watch(() => props.code, render)
</script>

<template>
  <div>
    <div v-if="html" class="mermaid-host flex justify-center" v-html="html" />
    <p v-else-if="!error" class="text-sm text-zinc-400 text-center py-10">{{ $t('mermaid.empty') }}</p>
    <p v-if="error" class="text-xs text-rose-600 font-mono">{{ error }}</p>
  </div>
</template>
