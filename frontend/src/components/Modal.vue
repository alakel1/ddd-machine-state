<script setup>
defineProps({ title: String, wide: Boolean })
const emit = defineEmits(['close'])
</script>

<template>
  <Teleport to="body">
    <div class="fixed inset-0 z-40 flex items-start justify-center overflow-y-auto bg-zinc-950/40 backdrop-blur-[2px] p-6 pt-[8vh]"
         @mousedown.self="emit('close')" @keydown.esc="emit('close')">
      <div class="card w-full shadow-2xl" :class="wide ? 'max-w-3xl' : 'max-w-lg'">
        <div class="flex items-center justify-between px-6 h-14 border-b border-zinc-100">
          <h3 class="font-semibold tracking-tight">{{ title }}</h3>
          <button class="icon-btn" @click="emit('close')"><i class="fa-solid fa-xmark fa-fw text-[14px]" /></button>
        </div>
        <div class="p-6"><slot /></div>
        <div v-if="$slots.footer" class="flex justify-end gap-2 px-6 py-4 bg-zinc-50/80 border-t border-zinc-100 rounded-b-2xl">
          <slot name="footer" />
        </div>
      </div>
    </div>
  </Teleport>
</template>
