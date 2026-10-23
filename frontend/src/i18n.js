import { createI18n } from 'vue-i18n'
import en from './locales/en.json'
import it from './locales/it.json'

export const LOCALES = { en: 'EN', it: 'IT' }

// scelta salvata, altrimenti la lingua del browser (inglese di default)
function initialLocale() {
  try {
    const saved = localStorage.getItem('locale')
    if (saved && saved in LOCALES) return saved
  } catch {}
  return (navigator.language || '').toLowerCase().startsWith('it') ? 'it' : 'en'
}

export const i18n = createI18n({ legacy: false, locale: initialLocale(), fallbackLocale: 'en', messages: { en, it } })

/** Per gli script (toast, confirm, diagrammi); nei template si usa $t. */
export const t = (...args) => i18n.global.t(...args)
export const locale = i18n.global.locale

export function setLocale(l) {
  locale.value = l
  document.documentElement.lang = l
  try { localStorage.setItem('locale', l) } catch {}
}
document.documentElement.lang = locale.value
