/**
 * React `src/lib/holidays.ts` ile ayni sabitler + date-holidays (CDN).
 */
export const INTERNATIONAL_FIXED_OBSERVANCES = [
  { month: 1, day: 1, nameTr: "Yılbaşı", nameEn: "New Year's Day" },
  { month: 2, day: 14, nameTr: "Sevgililer Günü", nameEn: "Valentine's Day" },
  { month: 3, day: 8, nameTr: "Dünya Kadınlar Günü", nameEn: "International Women's Day" },
  { month: 3, day: 21, nameTr: "Nevruz", nameEn: "Nowruz" },
  { month: 3, day: 22, nameTr: "Dünya Su Günü", nameEn: "World Water Day" },
  { month: 4, day: 1, nameTr: "1 Nisan Şaka Günü", nameEn: "April Fools' Day" },
  { month: 4, day: 7, nameTr: "Dünya Sağlık Günü", nameEn: "World Health Day" },
  { month: 4, day: 22, nameTr: "Dünya Yer Günü", nameEn: "Earth Day" },
  { month: 5, day: 1, nameTr: "Emek ve Dayanışma Günü", nameEn: "International Workers' Day" },
  { month: 5, day: 15, nameTr: "Uluslararası Aileler Günü", nameEn: "International Day of Families" },
  { month: 6, day: 5, nameTr: "Dünya Çevre Günü", nameEn: "World Environment Day" },
  { month: 6, day: 20, nameTr: "Dünya Mülteciler Günü", nameEn: "World Refugee Day" },
  { month: 8, day: 12, nameTr: "Uluslararası Gençlik Günü", nameEn: "International Youth Day" },
  { month: 9, day: 21, nameTr: "Uluslararası Barış Günü", nameEn: "International Day of Peace" },
  { month: 10, day: 1, nameTr: "Uluslararası Yaşlılar Günü", nameEn: "International Day of Older Persons" },
  { month: 10, day: 5, nameTr: "Dünya Öğretmenler Günü", nameEn: "World Teachers' Day" },
  { month: 10, day: 16, nameTr: "Dünya Gıda Günü", nameEn: "World Food Day" },
  { month: 10, day: 31, nameTr: "Cadılar Bayramı (Halloween)", nameEn: "Halloween" },
  { month: 11, day: 20, nameTr: "Dünya Çocuk Hakları Günü", nameEn: "World Children's Day" },
  { month: 12, day: 3, nameTr: "Uluslararası Engelliler Günü", nameEn: "International Day of Persons with Disabilities" },
  { month: 12, day: 10, nameTr: "İnsan Hakları Günü", nameEn: "Human Rights Day" },
  { month: 12, day: 25, nameTr: "Noel", nameEn: "Christmas Day" },
  { month: 12, day: 31, nameTr: "Yılbaşı Gecesi", nameEn: "New Year's Eve" },
]

let hdInstance = null
let hdCountry = ""

function observanceLabel(o, locale) {
  return locale === "en" ? o.nameEn : o.nameTr
}

function internationalLabelsForDate(date, locale) {
  const m = date.getMonth() + 1
  const d = date.getDate()
  const out = []
  for (const o of INTERNATIONAL_FIXED_OBSERVANCES) {
    if (o.month === m && o.day === d) out.push(observanceLabel(o, locale))
  }
  return out
}

function mergeUniqueLabels(a, b) {
  const seen = new Set()
  const out = []
  const add = (s) => {
    const t = s.trim()
    if (!t) return
    const k = t.toLowerCase()
    if (seen.has(k)) return
    seen.add(k)
    out.push(t)
  }
  for (const x of a) add(x)
  for (const x of b) add(x)
  return out
}

async function ensureHd() {
  const country = String(window.__AGENTBASE__?.holidayCountry || "TR").trim() || "TR"
  if (hdInstance && hdCountry === country) return hdInstance
  try {
    const mod = await import("https://esm.sh/date-holidays@3.27.0")
    const Holidays = mod.default
    hdCountry = country
    hdInstance = new Holidays(country, { types: ["public", "bank", "optional"] })
    return hdInstance
  } catch {
    hdInstance = null
    return null
  }
}

export async function primeHolidays() {
  await ensureHd()
}

/** `primeHolidays()` sonrasi takvim hücresi icin (ülke + uluslararasi). */
export function getHolidayLabelsSync(date, locale = "tr") {
  const intl = internationalLabelsForDate(date, locale)
  if (!hdInstance) return intl
  const res = hdInstance.isHoliday(date)
  const countryLabels = !res ? [] : res.map((h) => h.name).filter(Boolean)
  return mergeUniqueLabels(countryLabels, intl)
}

export function holidayTooltipTextSync(date, locale = "tr") {
  const labels = getHolidayLabelsSync(date, locale)
  return labels.length ? labels.join(" · ") : ""
}
