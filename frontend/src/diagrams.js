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

/** Archi della context map: relazioni e, se richieste, dipendenze di stato (guardie ed effetti tra entità). */
function mapEdges(model, dependencies) {
  const edges = model.relations.map((r) => ({ kind: r.composition ? 'composition' : 'relation', from: r.source_id, to: r.target_id, r }))
  if (dependencies) {
    const seen = new Set()
    for (const e of model.entities)
      for (const tr of e.transitions) {
        for (const g of tr.guards)
          if (g.kind === 'state' && g.entity_id && g.entity_id !== e.id && !seen.has(`g${e.id}-${g.entity_id}`)) {
            seen.add(`g${e.id}-${g.entity_id}`)
            edges.push({ kind: 'guard', from: e.id, to: g.entity_id })
          }
        for (const f of tr.effects)
          if (f.entity_id !== e.id && !seen.has(`f${e.id}-${f.entity_id}`)) {
            seen.add(`f${e.id}-${f.entity_id}`)
            edges.push({ kind: 'effect', from: e.id, to: f.entity_id })
          }
      }
  }
  return edges
}

/** Distanza (in gradi di separazione) di ogni entità raggiungibile da focus, entro depth (null = senza limite). */
export function neighborhood(model, focus, depth = null, { dependencies = false } = {}) {
  const adj = {}
  for (const x of mapEdges(model, dependencies)) {
    ;(adj[x.from] ||= new Set()).add(x.to)
    ;(adj[x.to] ||= new Set()).add(x.from)
  }
  const dist = new Map([[focus, 0]])
  let frontier = [focus]
  for (let d = 1; frontier.length && (depth == null || d <= depth); d++) {
    const next = []
    for (const id of frontier)
      for (const n of adj[id] || [])
        if (!dist.has(n)) {
          dist.set(n, d)
          next.push(n)
        }
    frontier = next
  }
  return dist
}

/**
 * Context map: entità, relazioni e dipendenze di stato.
 * Senza focus: tutte le entità raggruppate per bounded context.
 * Con focus: solo le entità entro depth gradi di separazione, con focus evidenziata al centro.
 */
export function contextMap(model, { dependencies = true, focus = null, depth = null } = {}) {
  if (!model?.entities?.length) return ''
  const dist = focus != null ? neighborhood(model, focus, depth, { dependencies }) : null
  const shown = model.entities.filter((e) => !dist || dist.has(e.id))
  const edges = mapEdges(model, dependencies).filter((x) => !dist || (dist.has(x.from) && dist.has(x.to)))
  const L = ['flowchart LR']
  const node = (e, indent) => {
    const icon = { aggregate: 'fa:fa-cubes', entity: 'fa:fa-cube', value_object: 'fa:fa-gem' }[e.kind]
    const kind = icon ? `${icon} ${t(`kind.${e.kind}`).toLowerCase()}` : ''
    // nella vista centrata non ci sono i riquadri dei contesti: il contesto va nell'etichetta
    const ctx = dist && e.context ? `${q(e.context)}<br/>` : ''
    L.push(`${indent}e${e.id}["<b>${q(e.name)}</b><br/><small>${ctx}${kind} · ${t('diagram.states', e.states.length)}</small>"]`)
  }
  const groups = {}
  if (dist) shown.forEach((e) => node(e, '  '))
  else {
    for (const e of shown) (groups[e.context || '—'] ||= []).push(e)
    Object.entries(groups).forEach(([ctx, ents], i) => {
      L.push(`  subgraph ctx${i}["${q(ctx)}"]`)
      ents.forEach((e) => node(e, '    '))
      L.push('  end')
    })
  }
  const linkStyles = []
  edges.forEach((x, idx) => {
    if (x.kind === 'composition') {
      // composizione: il figlio non esiste senza il padre
      L.push(`  e${x.from} -- "fa:fa-cubes-stacked ${q(x.r.label) || q(t('diagram.contains'))} ${q(x.r.cardinality)}" --o e${x.to}`)
      linkStyles.push(`  linkStyle ${idx} stroke:#3f3f46,stroke-width:2.5px`)
    } else if (x.kind === 'relation') {
      L.push(`  e${x.from} -- "${q(x.r.label)} ${q(x.r.cardinality)}" --> e${x.to}`)
    } else if (x.kind === 'guard') {
      L.push(`  e${x.from} -. "${q(t('diagram.dependsOn'))}" .-> e${x.to}`)
      linkStyles.push(`  linkStyle ${idx} stroke:#a1a1aa,stroke-width:1px`)
    } else {
      L.push(`  e${x.from} == "fa:fa-bolt ${q(t('diagram.effect'))}" ==> e${x.to}`)
      linkStyles.push(`  linkStyle ${idx} stroke:#f59e0b,stroke-width:2px`)
    }
  })
  for (const e of shown) {
    const c = pal(e.color)
    const d = dist?.get(e.id)
    if (d === 0) L.push(`  style e${e.id} fill:${c.hex},stroke:${c.hex},stroke-width:3px,color:#ffffff`)
    // oltre il primo grado: bordo tratteggiato e sfondo bianco, per far risaltare i vicini diretti
    else if (d > 1) L.push(`  style e${e.id} fill:#ffffff,stroke:${c.hex},stroke-width:1.5px,stroke-dasharray:4 3,color:#3f3f46`)
    else L.push(`  style e${e.id} fill:${c.bg},stroke:${c.hex},stroke-width:1.5px,color:#18181b`)
  }
  Object.keys(groups).forEach((_, i) => L.push(`  style ctx${i} fill:#fafafa,stroke:#e4e4e7,color:#71717a`))
  return [...L, ...linkStyles].join('\n')
}
