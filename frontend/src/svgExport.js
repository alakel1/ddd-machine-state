// Esportazione PNG dei diagrammi Mermaid: SVG → immagine → canvas.
// Dentro un'immagine SVG il browser non vede i font né il CSS della pagina: i font (Inter e Font Awesome)
// vengono incorporati come data URL e le icone Font Awesome, disegnate dal CSS con ::before,
// vengono sostituite dal loro carattere.

const fontCache = new Map() // url → data URL, i font si scaricano una volta sola
const LABEL_STYLES = ['font-family', 'font-size', 'font-weight', 'line-height', 'letter-spacing', 'color',
  'margin', 'padding', 'display', 'white-space', 'text-align', 'background-color']

async function toDataUrl(url) {
  if (!fontCache.has(url)) {
    fontCache.set(url, fetch(url).then((r) => r.blob()).then((blob) => new Promise((resolve, reject) => {
      const reader = new FileReader()
      reader.onload = () => resolve(reader.result)
      reader.onerror = reject
      reader.readAsDataURL(blob)
    })))
  }
  return fontCache.get(url)
}

/** @font-face della pagina che servono ai diagrammi: Inter (latino) e Font Awesome solid. */
function usedFontFaces() {
  const out = []
  for (const sheet of document.styleSheets) {
    let rules
    try { rules = sheet.cssRules } catch { continue } // fogli di altri domini: non leggibili
    for (const rule of rules) {
      if (!(rule instanceof CSSFontFaceRule)) continue
      const family = rule.style.getPropertyValue('font-family').replace(/["']/g, '').trim()
      const range = rule.style.getPropertyValue('unicode-range')
      const weight = rule.style.getPropertyValue('font-weight')
      // latin e latin-ext; il browser può riscrivere gli intervalli in forma breve (U+0000-00FF → U+0-FF)
      const inter = family === 'Inter Variable' && /U\+0{1,4}-0{0,2}FF(?![0-9A-F])|U\+0?100-0?2BA/i.test(range)
      const icons = family === 'Font Awesome 6 Free' && weight.includes('900')
      const src = rule.style.getPropertyValue('src').match(/url\(["']?([^"')]+\.woff2[^"')]*)["']?\)/)?.[1]
      if ((inter || icons) && src) out.push({ family, range, weight, url: new URL(src, sheet.href || location.href).href })
    }
  }
  return out
}

async function fontCss() {
  const faces = await Promise.all(usedFontFaces().map(async (f) => {
    const data = await toDataUrl(f.url)
    return `@font-face{font-family:"${f.family}";src:url(${data}) format("woff2");font-weight:${f.weight || 'normal'};` +
      (f.range ? `unicode-range:${f.range};` : '') + '}'
  }))
  return faces.join('')
}

/** Copia dell'SVG pronta per diventare un'immagine autonoma. */
async function standaloneSvg(svg) {
  const clone = svg.cloneNode(true)
  // etichette HTML (foreignObject): Mermaid le ha misurate con il CSS della pagina, che nell'immagine non c'è.
  // Gli stili calcolati diventano stili in linea, così il testo occupa lo stesso spazio e non viene tagliato.
  const liveLabels = svg.querySelectorAll('foreignObject *')
  clone.querySelectorAll('foreignObject *').forEach((el, k) => {
    const cs = getComputedStyle(liveLabels[k])
    for (const p of LABEL_STYLES) el.style.setProperty(p, cs.getPropertyValue(p))
  })
  // icone Font Awesome: il carattere che il CSS mette in ::before, scritto dentro l'elemento
  const live = svg.querySelectorAll('i')
  clone.querySelectorAll('i').forEach((el, k) => {
    const glyph = getComputedStyle(live[k], '::before').content.replace(/^["']|["']$/g, '')
    if (!glyph || glyph === 'none') return
    el.textContent = glyph
    el.style.fontFamily = '"Font Awesome 6 Free"'
    el.style.fontWeight = '900'
    el.style.fontStyle = 'normal'
  })
  const [, , w, h] = (svg.getAttribute('viewBox') || '').split(/\s+/).map(Number)
  const width = Math.ceil(w || svg.getBoundingClientRect().width)
  const height = Math.ceil(h || svg.getBoundingClientRect().height)
  clone.setAttribute('width', width)
  clone.setAttribute('height', height)
  clone.style.maxWidth = 'none'
  // regole della pagina che valgono per i diagrammi (style.css, .mermaid-host)
  const style = document.createElementNS('http://www.w3.org/2000/svg', 'style')
  style.textContent = (await fontCss()) +
    '.edgeLabel,.edgeLabel p{background:#fff!important;font-size:11px;color:#52525b}'
  clone.insertBefore(style, clone.firstChild)
  return { markup: new XMLSerializer().serializeToString(clone), width, height }
}

/** PNG del diagramma, a doppia risoluzione e su sfondo bianco. */
export async function svgToPng(svg, { scale = 2, background = '#ffffff' } = {}) {
  const { markup, width, height } = await standaloneSvg(svg)
  const img = new Image()
  img.src = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(markup)}`
  await img.decode()
  const canvas = document.createElement('canvas')
  canvas.width = width * scale
  canvas.height = height * scale
  const ctx = canvas.getContext('2d')
  ctx.fillStyle = background
  ctx.fillRect(0, 0, canvas.width, canvas.height)
  ctx.drawImage(img, 0, 0, canvas.width, canvas.height)
  return new Promise((resolve, reject) => canvas.toBlob((b) => (b ? resolve(b) : reject(new Error('toBlob'))), 'image/png'))
}

/** Nome di file sicuro: "Fase di gara · stati" → "fase-di-gara-stati". */
export const fileName = (name) =>
  (name || 'diagram').normalize('NFD').replace(/[̀-ͯ]/g, '').replace(/[^a-zA-Z0-9]+/g, '-').replace(/^-|-$/g, '').toLowerCase()

export function download(blob, name) {
  const url = URL.createObjectURL(blob)
  const a = Object.assign(document.createElement('a'), { href: url, download: `${fileName(name)}.png` })
  a.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
