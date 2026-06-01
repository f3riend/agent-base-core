import {
  ACCOUNTS,
  CAMPAIGN_ACCOUNTS,
  CAMPAIGN_DRAFTS,
  CAMPAIGN_SCHEDULED_POSTS,
  DRAFTS,
  SCHEDULED_POSTS,
} from "./social-media-constants.js"

export function isCampaignManagementMode() {
  try {
    const p = String(window.location.pathname || "")
    return p.includes("/campaign-management")
  } catch {
    return false
  }
}

export const CAMPAIGN_MODE = isCampaignManagementMode()

/** Campaign revise-image / banner canvas — fixed size so output is never locked to square refs. */
export const CAMPAIGN_BANNER_REVISE_OUTPUT_SIZE = "1600x704"

export function isSepetlerCampaignApiBase(baseUrl) {
  return String(baseUrl || "")
    .trim()
    .toLowerCase()
    .includes("/api/ai/v1")
}

/** Sepetler banner yayını için sayısal mağaza kimliği gerekir (module-* sentetik satırlar kullanılamaz). */
export function isPublishableCampaignStoreId(storeId) {
  return /^\d+$/.test(String(storeId || "").trim())
}

export function countCampaignCatalogCampaigns(stores) {
  const rows = Array.isArray(stores) ? stores : []
  const ids = new Set()
  for (const store of rows) {
    for (const camp of Array.isArray(store?.campaigns) ? store.campaigns : []) {
      const id = String(camp?.id || "").trim()
      if (id) ids.add(id)
    }
  }
  return ids.size
}

export function formatCampaignStatusLabel(campaign) {
  if (!campaign || typeof campaign !== "object") return ""
  if (campaign.published === true) return "Yayında"
  if (campaign.published === false) return "Pasif"
  return ""
}

export function formatCampaignOptionLabel(campaign) {
  const c = campaign && typeof campaign === "object" ? campaign : {}
  const name = String(c.product || c.name || c.title || c.id || "Kampanya").trim()
  const id = String(c.id || "").trim()
  const status = formatCampaignStatusLabel(c)
  const dates = c.campaign_dates && typeof c.campaign_dates === "object" ? c.campaign_dates : {}
  const start = String(dates.start_date || dates.startDate || c.start_date || "").trim()
  const end = String(dates.end_date || dates.endDate || c.end_date || "").trim()
  const bits = [name]
  if (id) bits[0] = `${name} (#${id})`
  if (String(c.source || "") === "store_item") {
    const { oldPrice, newPrice, discountPercent } = canonicalCampaignPricing(
      c.pricing && typeof c.pricing === "object" ? c.pricing : {},
      c,
    )
    if (oldPrice && newPrice) bits.push(`${oldPrice}→${newPrice} TL`)
    else if (discountPercent) bits.push(`%${discountPercent}`)
  } else if (status) {
    bits.push(status)
  }
  if (start || end) bits.push(`${start || "…"} – ${end || "…"}`)
  return bits.join(" · ")
}

export function formatCampaignCatalogProviderLine(provider, stores) {
  const prov = provider && typeof provider === "object" ? provider : {}
  const source = String(prov.source || "").trim()
  const base = String(prov.base_url || "").trim()
  const storeCount = Array.isArray(stores) ? stores.length : 0
  const campaignCount = countCampaignCatalogCampaigns(stores)
  const counts = `${storeCount} mağaza · ${campaignCount} kampanya`
  if (source === "sepetler_ai_v1") {
    return `Sepetler AI API · ${storeCount} magaza · indirimli urunler magaza secilince yuklenir${base ? ` · ${base}` : ""}`
  }
  if (source === "upstream" && base) return `Campaign API · ${counts} · ${base}`
  if (counts !== "0 mağaza · 0 kampanya") return counts
  return ""
}

export function draftsCollection() {
  return CAMPAIGN_MODE ? CAMPAIGN_DRAFTS : DRAFTS
}

export function accountsCollection() {
  return CAMPAIGN_MODE ? CAMPAIGN_ACCOUNTS : ACCOUNTS
}

export function scheduledPostsCollection() {
  return CAMPAIGN_MODE ? CAMPAIGN_SCHEDULED_POSTS : SCHEDULED_POSTS
}

/**
 * Calendar reads — campaign modunda sadece campaign_scheduled_posts,
 * normal modda hem scheduled_posts hem story_scheduled_posts.
 * Yazma akışları için scheduledPostsCollection() (tek koleksiyon) kalır.
 */
export function scheduledPostsCollections() {
  if (CAMPAIGN_MODE) return [CAMPAIGN_SCHEDULED_POSTS]
  return [SCHEDULED_POSTS, "story_scheduled_posts"]
}

export function withCampaignBannerConstraint(text) {
  const base = String(text || "").trim()
  if (!CAMPAIGN_MODE) return base
  const rule =
    "Cikti 1600x704 yatay kampanya banner formatinda olmali (OpenAI 16px grid; ~1600x700); kare urun referansi kompozisyonu kare yapmamalidir. Secili sablonun genis layoutunu, metin/urun yerlesimini koru; gorseli kirpma."
  const bleed =
    "Tam genislik ve tam yuksekligi kullan: yan veya ust-alt beyaz bos serit birakma; kompozisyonu ortada kare vitrin gibi kucultme. Sablonun yatay alanini bastan sona doldur; sadece metin/urun/logo alanlarini guncelle."
  const bleedEn =
    "Use the full width and height: no white margins; do not shrink the scene to a centered square; keep the wide banner composition and only update text/product/logo areas."
  if (!base) return `${rule}\n\n${bleed}\n${bleedEn}`
  const low = base.toLowerCase()
  if (low.includes("1600x704") || low.includes("1600x700")) {
    if (low.includes("beyaz bos") || low.includes("white margin")) return base
    return `${base}\n\n${bleed}\n${bleedEn}`
  }
  return `${base}\n\n${rule}\n\n${bleed}\n${bleedEn}`
}

/** When studio main image is not the saved template layout, revise edits that image — append hint for users. */
export function appendCampaignReviseLayoutMismatchHint(feedback, mainImageUrl, layoutUrl) {
  const m = String(mainImageUrl || "").trim()
  const l = String(layoutUrl || "").trim()
  if (!CAMPAIGN_MODE || !l || m === l) return String(feedback || "").trim()
  const hint =
    "Not: Su an duzenlenen ana gorsel studio onizlemesidir (sablon layout dosyasi degil). Dogrudan sablon dosyasinda degisiklik icin once studio ana gorselini sablonla ayni yapin veya Kampanya banner olustur ile sablon image_url uzerinden ilerleyin."
  const b = String(feedback || "").trim()
  return b ? `${b}\n\n${hint}` : hint
}

/** For revise-image reference_image_urls: logo [1], layout [0], then slice(2); drop main image_url and dedupe. */
export function buildCampaignTemplateRefsExcludingMain(selectedTpl, mainImageUrl) {
  const img = String(mainImageUrl || "").trim()
  const raw = Array.isArray(selectedTpl?.imageUrls) ? selectedTpl.imageUrls : []
  const u0 = String(raw[0] || "").trim()
  const u1 = String(raw[1] || "").trim()
  const rest = raw.slice(2).map((u) => String(u || "").trim()).filter(Boolean)
  const out = []
  const seen = new Set()
  for (const u of [u1, u0, ...rest]) {
    if (!u || u === img || seen.has(u)) continue
    seen.add(u)
    out.push(u)
  }
  return out
}

/** Banner generate: refs after layout (image_url); order logo, extra template layers, then tail URLs. */
export function buildCampaignBannerReferenceImageList(layoutUrl, selectedTpl, orderedTailRefs) {
  const layout = String(layoutUrl || "").trim()
  const seen = new Set()
  if (layout) seen.add(layout)
  const out = []
  const push = (u) => {
    const v = String(u || "").trim()
    if (!v || seen.has(v)) return
    seen.add(v)
    out.push(v)
  }
  const raw = Array.isArray(selectedTpl?.imageUrls) ? selectedTpl.imageUrls : []
  push(String(raw[1] || "").trim())
  for (let i = 2; i < raw.length; i++) push(String(raw[i] || "").trim())
  for (const u of orderedTailRefs || []) push(u)
  return out
}

/** Merge `pricing` with common alternate blobs/keys so banner prompts stay populated. */
export function canonicalCampaignPricing(pricing, campaign) {
  const p = pricing && typeof pricing === "object" ? pricing : {}
  const c = campaign && typeof campaign === "object" ? campaign : {}
  const priceBlob =
    (c.price && typeof c.price === "object" ? c.price : null) ||
    (c.prices && typeof c.prices === "object" ? c.prices : null)
  const merged = Object.assign({}, priceBlob || {}, p)
  const firstStr = (...vals) => {
    for (const v of vals) {
      if (v === undefined || v === null) continue
      const s = String(v).trim()
      if (s) return s
    }
    return ""
  }
  const oldPrice = firstStr(
    merged.old_price,
    merged.oldPrice,
    merged.price_old,
    merged.previous_price,
    merged.list_price,
    merged.regular_price,
    merged.msrp,
    c.old_price,
    c.oldPrice,
    c.price_old,
  )
  const newPrice = firstStr(
    merged.new_price,
    merged.newPrice,
    merged.price_new,
    merged.sale_price,
    merged.current_price,
    merged.discounted_price,
    merged.final_price,
    c.new_price,
    c.newPrice,
    c.sale_price,
  )
  let discountPercent = firstStr(
    merged.discount_percent,
    merged.discountPercent,
    merged.discount_pct,
    merged.discount,
    merged.percent_off,
    merged.pct_off,
    c.discount_percent,
    c.discountPercent,
  )
  discountPercent = discountPercent.replace(/%/g, "").trim()
  return { oldPrice, newPrice, discountPercent }
}

/** Catalog string/number -> finite number for CAMPAIGN_DATA JSON (fiyatlar sayisal olsun). */
function campaignMetricToNumber(raw) {
  if (raw === undefined || raw === null) return undefined
  if (typeof raw === "number" && Number.isFinite(raw)) return raw
  const s = String(raw).trim().replace(/%/g, "")
  if (!s) return undefined
  const normalized = s.replace(/,/g, ".").replace(/[^\d.-]/g, "")
  if (!normalized || normalized === "-" || normalized === ".") return undefined
  const n = Number(normalized)
  return Number.isFinite(n) ? n : undefined
}

/**
 * Campaign API katalogundan yapilandirilmis veri — prompta CAMPAIGN_DATA JSON olarak eklenir.
 */
export function buildCampaignDataPayload(campaign) {
  const c = campaign && typeof campaign === "object" ? campaign : {}
  const idRaw = c.id
  const idNum =
    typeof idRaw === "number" && Number.isFinite(idRaw)
      ? idRaw
      : (() => {
          const n = Number(String(idRaw ?? "").trim())
          return Number.isFinite(n) ? n : undefined
        })()
  const product = String(c.product || c.name || c.title || c.campaign_name || "").trim()
  const descriptionRaw = String(
    c.description ||
      c.desc ||
      c.body ||
      c.summary ||
      c.product_description ||
      c.productDescription ||
      "",
  ).trim()
  /** 5000 char prompt sınırına marj bırakmak için description'ı 400 char ile kırp. */
  const description = descriptionRaw.length > 400 ? descriptionRaw.slice(0, 400) + "…" : descriptionRaw
  const redirect = String(c.redirect_url || c.redirectUrl || c.url || c.link || c.deeplink || "").trim()

  const pricingSrc = c.pricing && typeof c.pricing === "object" ? c.pricing : {}
  const { oldPrice, newPrice, discountPercent } = canonicalCampaignPricing(pricingSrc, c)
  const pricing = {}
  const op = campaignMetricToNumber(oldPrice)
  const np = campaignMetricToNumber(newPrice)
  const dp = campaignMetricToNumber(discountPercent)
  if (op !== undefined) pricing.old_price = op
  if (np !== undefined) pricing.new_price = np
  if (dp !== undefined) pricing.discount_percent = dp

  const datesNested = c.campaign_dates && typeof c.campaign_dates === "object" ? c.campaign_dates : {}
  const start = String(
    datesNested.start_date ||
      datesNested.startDate ||
      datesNested.from ||
      datesNested.valid_from ||
      datesNested.validFrom ||
      c.start_date ||
      c.startDate ||
      c.date_start ||
      c.starts_at ||
      c.valid_from ||
      "",
  ).trim()
  const end = String(
    datesNested.end_date ||
      datesNested.endDate ||
      datesNested.to ||
      datesNested.valid_until ||
      datesNested.validTo ||
      c.end_date ||
      c.endDate ||
      c.date_end ||
      c.ends_at ||
      c.valid_until ||
      "",
  ).trim()
  const campaign_dates = {}
  if (start) campaign_dates.start_date = start
  if (end) campaign_dates.end_date = end

  const out = {}
  if (idNum !== undefined) out.id = idNum
  if (product) out.product = product
  if (description) out.description = description
  if (Object.keys(pricing).length) out.pricing = pricing
  if (Object.keys(campaign_dates).length) out.campaign_dates = campaign_dates
  /** `media` array URL'leri kaldırıldı — model URL'i bilmiyor, 5000 char prompt sınırını boşa şişiriyordu. */
  if (redirect) out.redirect_url = redirect
  return out
}

export function formatCampaignPricing(pricing, campaign) {
  const { oldPrice, newPrice, discountPercent } = canonicalCampaignPricing(pricing, campaign)
  const out = []
  if (discountPercent) out.push(`Indirim yuzdesi: ${discountPercent}%`)
  if (oldPrice) out.push(`Eski fiyat: ${oldPrice}`)
  if (newPrice) out.push(`Yeni fiyat: ${newPrice}`)
  return out
}

/** One-line discount summary for the model (e.g. "%33 indirim (30 -> 20)"). */
export function formatCampaignPricingSummaryLine(pricing, campaign) {
  const { oldPrice, newPrice, discountPercent } = canonicalCampaignPricing(pricing, campaign)
  const discount = discountPercent
  if (!discount && !oldPrice && !newPrice) return ""
  if (discount && oldPrice && newPrice) {
    return `Kampanya fiyat ozeti: %${discount} indirim (${oldPrice} -> ${newPrice}).`
  }
  if (discount && newPrice) return `Kampanya fiyat ozeti: %${discount} indirim, yeni fiyat ${newPrice}.`
  if (discount) return `Kampanya fiyat ozeti: %${discount} indirim.`
  if (oldPrice && newPrice) return `Kampanya fiyat ozeti: ${oldPrice} yerine ${newPrice}.`
  if (newPrice) return `Kampanya fiyat ozeti: yeni fiyat ${newPrice}.`
  return ""
}

export function buildCampaignBannerPrompt(
  campaign,
  {
    templatePrompt = "",
    reviseFeedback = "",
    directImagePrompt = "",
  } = {},
) {
  const c = campaign && typeof campaign === "object" ? campaign : {}
  const datesNested = c.campaign_dates && typeof c.campaign_dates === "object" ? c.campaign_dates : {}
  const start = String(
    datesNested.start_date ||
      datesNested.startDate ||
      datesNested.from ||
      datesNested.valid_from ||
      datesNested.validFrom ||
      c.start_date ||
      c.startDate ||
      c.date_start ||
      c.starts_at ||
      c.valid_from ||
      "",
  ).trim()
  const end = String(
    datesNested.end_date ||
      datesNested.endDate ||
      datesNested.to ||
      datesNested.valid_until ||
      datesNested.validTo ||
      c.end_date ||
      c.endDate ||
      c.date_end ||
      c.ends_at ||
      c.valid_until ||
      "",
  ).trim()
  const pricing = c.pricing && typeof c.pricing === "object" ? c.pricing : {}
  const description = String(
    c.description ||
      c.desc ||
      c.body ||
      c.summary ||
      c.product_description ||
      c.productDescription ||
      "",
  ).trim()
  const tpl = String(templatePrompt || "").trim()
  const revise = String(reviseFeedback || "").trim()
  const dataPayload = buildCampaignDataPayload(c)
  const parts = []
  if (Object.keys(dataPayload).length) {
    parts.push(`CAMPAIGN_DATA:\n${JSON.stringify(dataPayload, null, 2)}`)
    parts.push(
      "CAMPAIGN_DATA JSON'daki pricing ve campaign_dates degerlerini bannerdaki fiyat, indirim yuzdesi ve tarih metinlerine uygula; product, description, media ve redirect_url alanlarini baslik, aciklama, urun gorseli ve CTA ile tutarli kullan. Sablondaki ornek yuzde/fiyat/tarihleri bu verilerle degistir; JSON disi rakamlari koruma.",
    )
  }
  parts.push(
    tpl,
    revise && revise !== tpl ? revise : "",
    String(directImagePrompt || "").trim(),
    "Yeni banneri secili sablon gorselinin layout'una gore hazirla.",
    "Tam genislik: cikti tum 1600x704 cerceveyi doldurmalidir; ortada kare bos alan veya genis beyaz kenar birakma.",
    "Referanslardaki logoyu (varsa) ve urun gorsellerini sablonun uygun alanlarina yerlestir; logo bozulmadan okunakli kalsin.",
    "Sablondaki ornek indirim/fiyat/metinleri asagidaki katalog bilgileriyle degistir; eski ornek metinleri aynen kopyalama.",
  )
  const product = String(c.product || c.name || c.title || c.campaign_name || c.id || "").trim()
  if (product) parts.push(`Urun / kampanya adi: ${product}`)
  const priceSummary = formatCampaignPricingSummaryLine(pricing, c)
  if (priceSummary) parts.push(priceSummary)
  parts.push(...formatCampaignPricing(pricing, c))
  if (description) parts.push(`Kampanya tasarim ve urun gorunumu (katalog aciklamasi): ${description}`)
  if (start || end) parts.push(`Kampanya tarihleri: ${start || "-"} - ${end || "-"}`)
  const redirect = String(c.redirect_url || c.redirectUrl || c.url || c.link || c.deeplink || "").trim()
  if (redirect) parts.push(`Yonlendirme URL: ${redirect}`)
  return parts.filter(Boolean).join("\n")
}
