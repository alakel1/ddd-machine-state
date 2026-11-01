// Testi brevi di guardie ed effetti, condivisi tra scheda entità e specifica
import { entity, ename, sname } from './store'
import { t } from './i18n'

export const guardText = (g) =>
  g.kind === 'rule'
    ? g.description
    : `${ename(g.entity_id)} ${g.negate ? '∉' : '∈'} {${g.state_ids.map(sname).join(', ')}}` +
      (g.each_entity_id ? `, ${t('entity.eachText', { entity: ename(g.each_entity_id) })}` : '')

export const effectText = (f) =>
  (f.kind === 'create' ? t('entity.effect.created') : f.kind === 'action' ? t('entity.effect.runs', { action: f.action }) : sname(f.to_state_id)) +
  (f.skip_final ? ` ${t('entity.effect.activeOnly')}` : '')

// transizioni del figlio che un effetto può far eseguire (per la pagina Azioni)
export function effectTargets(f) {
  const c = entity(f.entity_id)
  if (!c) return []
  if (f.kind === 'create') return c.transitions.filter((x) => x.kind === 'create')
  const name = (f.action || '').trim().toLowerCase()
  if (f.kind === 'action') return c.transitions.filter((x) => x.kind !== 'create' && x.action.trim().toLowerCase() === name)
  return c.transitions.filter((x) => x.kind !== 'create' && x.to_state_id === f.to_state_id)
}
// stato di partenza di un'azione come lo vuole /effect-sources: 0 = creazione, null = qualsiasi
export const fromOf = (tr) => (tr.kind === 'create' ? 0 : tr.from_state_id)

// guardie alternative (OR): le guardie con lo stesso alt_group bastano una per tutte
export const isAlternative = (guards, i) => i > 0 && !!guards[i].alt_group && guards[i].alt_group === guards[i - 1].alt_group
