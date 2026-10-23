import { reactive } from 'vue'

export const store = reactive({
  domains: [],
  model: null,
  toasts: [],
  replay: null, // { trace } passato dall'analisi al simulatore
  instanceLabels: {}, // id entità -> { name, base } per le istanze multiple dell'analisi
})

let toastId = 0
export function toast(message, kind = 'info') {
  const id = ++toastId
  store.toasts.push({ id, message, kind })
  setTimeout(() => (store.toasts = store.toasts.filter((t) => t.id !== id)), 4000)
}

async function request(method, url, body) {
  const res = await fetch(url, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!res.ok) {
    let msg = res.statusText
    try { msg = (await res.json()).detail || msg } catch {}
    toast(msg, 'error')
    throw new Error(msg)
  }
  const type = res.headers.get('content-type') || ''
  return type.includes('json') ? res.json() : res.text()
}

export const api = {
  get: (u) => request('GET', `/api${u}`),
  post: (u, b = {}) => request('POST', `/api${u}`, b),
  put: (u, b) => request('PUT', `/api${u}`, b),
  del: (u) => request('DELETE', `/api${u}`),
}

export async function loadDomains() {
  store.domains = await api.get('/domains')
}

export async function loadModel(id) {
  store.model = await api.get(`/domains/${id}/model`)
  return store.model
}

export const reloadModel = () => store.model && loadModel(store.model.id)

// --- lookup sul modello corrente ---
export function entity(id) {
  const real = store.model?.entities.find((e) => e.id === id)
  const label = store.instanceLabels[id]
  if (!label) return real
  // istanza simulata dall'analisi (es. "Lotto #2"): stessa entità di base, altro nome
  const base = real || store.model?.entities.find((e) => e.id === label.base)
  return base && { ...base, id, name: label.name }
}
// stato implicito "non esiste" delle entità con azioni di creazione
export const NONE = 0
export const NONE_STATE = { id: NONE, name: 'NON_ESISTE', virtual: true }
export const isCreatable = (e) => !!e?.transitions.some((t) => t.kind === 'create')
/** Stati selezionabili dell'entità: NON_ESISTE in testa se l'entità è creabile. */
export const statesOf = (e) => (e ? (isCreatable(e) ? [{ ...NONE_STATE, entity_id: e.id }, ...e.states] : e.states) : [])

export function stateById(id) {
  if (id === NONE) return NONE_STATE
  for (const e of store.model?.entities || []) {
    const s = e.states.find((s) => s.id === id)
    if (s) return s
  }
}
export const sname = (id) => (id == null ? '∗' : stateById(id)?.name ?? '?')
export const ename = (id) => entity(id)?.name ?? '?'
// stati in cui un figlio "non c'è più", proposti dai nomi (stessa regola del backend)
const GONE_WORDS = /ELIMIN|CESTIN|CANCELL|ANNULL|STORN|RIMOSS|ARCHIV|DELET/i
export const suggestChildGone = (e) => [NONE, ...(e?.states || []).filter((s) => s.is_final && GONE_WORDS.test(s.name)).map((s) => s.id)]
