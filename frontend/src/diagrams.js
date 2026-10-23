import { pal } from './colors'
import { t } from './i18n'

const q = (s) => String(s ?? '').replace(/["#:;]/g, ' ').trim()

/** Diagramma degli stati di un'entità; `current` evidenzia lo stato attivo. */
export function stateDiagram(entity, { current = null, highlight = [], direction = 'TB' } = {}) {
  if (!entity?.states?.length) return ''
  const c = pal(entity.color)
  const L = ['stateDiagram-v2', `  direction ${direction}`]
  for (const s of entity.states) L.push(`  state "${q(s.name)}" as s${s.id}`)
  const creations = entity.transitions.filter((x) => x.kind === 'create')
  if (!creations.length) for (const s of entity.states.filter((s) => s.is_initial)) L.push(`  [*] --> s${s.id}`)
  for (const tr of entity.transitions) {
    const lock = tr.guards.some((g) => g.kind === 'state') ? " <i class='fa-solid fa-lock'></i>" : ''
    const fx = tr.effects.length ? " <i class='fa-solid fa-bolt'></i>" : ''
    const cascade = tr.cascade_only ? " <i class='fa-solid fa-arrows-turn-right'></i>" : ''
    const label = `${q(tr.action)}${cascade}${lock}${fx}`
    if (tr.kind === 'create') {
      L.push(`  [*] --> s${tr.to_state_id} : ${label}`)
      continue
    }
    const froms = tr.from_state_id
      ? [tr.from_state_id]
      : entity.states.filter((s) => !s.is_final && s.id !== tr.to_state_id).map((s) => s.id)
    for (const f of froms) L.push(`  s${f} --> s${tr.to_state_id} : ${label}`)
  }
  for (const s of entity.states.filter((s) => s.is_final)) L.push(`  s${s.id} --> [*]`)
  L.push(`  classDef final fill:#fafafa,stroke:#a1a1aa,stroke-width:2px,stroke-dasharray:4 3,color:#3f3f46`)
  L.push(`  classDef current fill:${c.hex},stroke:${c.hex},color:#ffffff,font-weight:700`)
  L.push(`  classDef hl fill:#fff1f2,stroke:#f43f5e,stroke-width:2px,color:#9f1239`)
  const finals = entity.states.filter((s) => s.is_final && s.id !== current)
  if (finals.length) L.push(`  class ${finals.map((s) => 's' + s.id).join(',')} final`)
  const hl = highlight.filter((id) => id !== current)
  if (hl.length) L.push(`  class ${hl.map((id) => 's' + id).join(',')} hl`)
  if (current) L.push(`  class s${current} current`)
  return L.join('\n')
}

/** Context map: entità raggruppate per bounded context, relazioni e dipendenze di stato. */
export function contextMap(model, { dependencies = true } = {}) {
  if (!model?.entities?.length) return ''
  const L = ['flowchart LR']
  const groups = {}
  for (const e of model.entities) (groups[e.context || '—'] ||= []).push(e)
  Object.entries(groups).forEach(([ctx, ents], i) => {
    L.push(`  subgraph ctx${i}["${q(ctx)}"]`)
    for (const e of ents) {
      const icon = { aggregate: 'fa:fa-cubes', entity: 'fa:fa-cube', value_object: 'fa:fa-gem' }[e.kind]
      const kind = icon ? `${icon} ${t(`kind.${e.kind}`).toLowerCase()}` : ''
      L.push(`    e${e.id}["<b>${q(e.name)}</b><br/><small>${kind} · ${t('diagram.states', e.states.length)}</small>"]`)
    }
    L.push('  end')
  })
  let idx = 0
  const linkStyles = []
  for (const r of model.relations) {
    if (r.composition) {
      // composizione: il figlio non esiste senza il padre
      L.push(`  e${r.source_id} -- "fa:fa-cubes-stacked ${q(r.label) || q(t('diagram.contains'))} ${q(r.cardinality)}" --o e${r.target_id}`)
      linkStyles.push(`  linkStyle ${idx} stroke:#3f3f46,stroke-width:2.5px`)
    } else {
      L.push(`  e${r.source_id} -- "${q(r.label)} ${q(r.cardinality)}" --> e${r.target_id}`)
    }
    idx++
  }
  if (dependencies) {
    const seen = new Set()
    for (const e of model.entities)
      for (const tr of e.transitions) {
        for (const g of tr.guards)
          if (g.kind === 'state' && g.entity_id && g.entity_id !== e.id && !seen.has(`g${e.id}-${g.entity_id}`)) {
            seen.add(`g${e.id}-${g.entity_id}`)
            L.push(`  e${e.id} -. "${q(t('diagram.dependsOn'))}" .-> e${g.entity_id}`)
            linkStyles.push(`  linkStyle ${idx++} stroke:#a1a1aa,stroke-width:1px`)
          }
        for (const f of tr.effects)
          if (f.entity_id !== e.id && !seen.has(`f${e.id}-${f.entity_id}`)) {
            seen.add(`f${e.id}-${f.entity_id}`)
            L.push(`  e${e.id} == "fa:fa-bolt ${q(t('diagram.effect'))}" ==> e${f.entity_id}`)
            linkStyles.push(`  linkStyle ${idx++} stroke:#f59e0b,stroke-width:2px`)
          }
      }
  }
  for (const e of model.entities) {
    const c = pal(e.color)
    L.push(`  style e${e.id} fill:${c.bg},stroke:${c.hex},stroke-width:1.5px,color:#18181b`)
  }
  Object.keys(groups).forEach((_, i) => L.push(`  style ctx${i} fill:#fafafa,stroke:#e4e4e7,color:#71717a`))
  return [...L, ...linkStyles].join('\n')
}
