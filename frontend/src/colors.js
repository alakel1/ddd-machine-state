// Classi statiche (Tailwind non vede classi composte a runtime)
export const PALETTE = {
  indigo: { dot: 'bg-indigo-500', soft: 'bg-indigo-50 text-indigo-700 ring-indigo-200', text: 'text-indigo-600', bar: 'from-indigo-500 to-violet-500', hex: '#6366f1', bg: '#eef2ff' },
  emerald: { dot: 'bg-emerald-500', soft: 'bg-emerald-50 text-emerald-700 ring-emerald-200', text: 'text-emerald-600', bar: 'from-emerald-500 to-teal-500', hex: '#10b981', bg: '#ecfdf5' },
  amber: { dot: 'bg-amber-500', soft: 'bg-amber-50 text-amber-800 ring-amber-200', text: 'text-amber-600', bar: 'from-amber-400 to-orange-500', hex: '#f59e0b', bg: '#fffbeb' },
  rose: { dot: 'bg-rose-500', soft: 'bg-rose-50 text-rose-700 ring-rose-200', text: 'text-rose-600', bar: 'from-rose-500 to-pink-500', hex: '#f43f5e', bg: '#fff1f2' },
  sky: { dot: 'bg-sky-500', soft: 'bg-sky-50 text-sky-700 ring-sky-200', text: 'text-sky-600', bar: 'from-sky-400 to-blue-500', hex: '#0ea5e9', bg: '#f0f9ff' },
  violet: { dot: 'bg-violet-500', soft: 'bg-violet-50 text-violet-700 ring-violet-200', text: 'text-violet-600', bar: 'from-violet-500 to-fuchsia-500', hex: '#8b5cf6', bg: '#f5f3ff' },
  teal: { dot: 'bg-teal-500', soft: 'bg-teal-50 text-teal-700 ring-teal-200', text: 'text-teal-600', bar: 'from-teal-400 to-cyan-500', hex: '#14b8a6', bg: '#f0fdfa' },
  orange: { dot: 'bg-orange-500', soft: 'bg-orange-50 text-orange-700 ring-orange-200', text: 'text-orange-600', bar: 'from-orange-400 to-red-500', hex: '#f97316', bg: '#fff7ed' },
  fuchsia: { dot: 'bg-fuchsia-500', soft: 'bg-fuchsia-50 text-fuchsia-700 ring-fuchsia-200', text: 'text-fuchsia-600', bar: 'from-fuchsia-500 to-pink-500', hex: '#d946ef', bg: '#fdf4ff' },
  slate: { dot: 'bg-slate-500', soft: 'bg-slate-100 text-slate-700 ring-slate-200', text: 'text-slate-600', bar: 'from-slate-400 to-slate-600', hex: '#64748b', bg: '#f1f5f9' },
}
export const COLORS = Object.keys(PALETTE)
export const pal = (c) => PALETTE[c] || PALETTE.indigo

export const KINDS = { aggregate: 'Aggregato', entity: 'Entità', value_object: 'Value object' }

export const SEVERITY = {
  error: { label: 'Errore', cls: 'bg-rose-50 text-rose-700 ring-rose-200', dot: 'bg-rose-500' },
  warning: { label: 'Attenzione', cls: 'bg-amber-50 text-amber-800 ring-amber-200', dot: 'bg-amber-500' },
  info: { label: 'Info', cls: 'bg-sky-50 text-sky-700 ring-sky-200', dot: 'bg-sky-500' },
}

export const STATUS = {
  enabled: { label: 'Eseguibile', cls: 'bg-emerald-50 text-emerald-700 ring-emerald-200', dot: 'bg-emerald-500' },
  conflict: { label: 'In conflitto', cls: 'bg-amber-50 text-amber-800 ring-amber-200', dot: 'bg-amber-500' },
  blocked: { label: 'Bloccata', cls: 'bg-zinc-100 text-zinc-600 ring-zinc-200', dot: 'bg-zinc-400' },
  unavailable: { label: 'Non dallo stato', cls: 'bg-white text-zinc-400 ring-zinc-200', dot: 'bg-zinc-200' },
}
