// Testi brevi di guardie ed effetti, condivisi tra scheda entità e specifica
import { ename, sname } from './store'
import { t } from './i18n'

export const guardText = (g) =>
  g.kind === 'rule'
    ? g.description
    : `${ename(g.entity_id)} ${g.negate ? '∉' : '∈'} {${g.state_ids.map(sname).join(', ')}}` +
      (g.each_entity_id ? `, ${t('entity.eachText', { entity: ename(g.each_entity_id) })}` : '')

export const effectText = (f) =>
  (f.kind === 'create' ? t('entity.effect.created') : f.kind === 'action' ? t('entity.effect.runs', { action: f.action }) : sname(f.to_state_id)) +
  (f.skip_final ? ` ${t('entity.effect.activeOnly')}` : '')

// guardie alternative (OR): le guardie con lo stesso alt_group bastano una per tutte
export const isAlternative = (guards, i) => i > 0 && !!guards[i].alt_group && guards[i].alt_group === guards[i - 1].alt_group
