import { apiBase, apiRequest, authHeaders } from "./social-media-api.js"
import {
  CAMPAIGN_MODE,
  accountsCollection,
  isSepetlerCampaignApiBase,
  scheduledPostsCollection,
} from "./social-media-campaign-utils.js"
import { CAMPAIGN_CATALOG_CACHE_TTL_MS, CAMPAIGN_CATALOG_RETRY_MS, DEFAULT_CAMPAIGN_API_BASE_URL } from "./social-media-constants.js"
import { activeAccount, campaignCatalogCredentialsReady } from "./social-media-selectors.js"
import { s } from "./social-media-state.js"

export const DEL = { __agentBaseDeleteField: true }
export const TS = { __agentBaseServerTimestamp: true }

let campaignCatalogNextFetchAt = 0
let lastCampaignCatalogErrorMsg = ""

export async function socialList(collection) {
  return apiRequest("/social-data/collections/" + encodeURIComponent(collection), { headers: authHeaders(false) })
}

export async function socialCreate(collection, body) {
  return apiRequest("/social-data/collections/" + encodeURIComponent(collection), {
    method: "POST",
    headers: authHeaders(true),
    body: JSON.stringify(body),
  })
}

export async function socialPut(collection, id, body, merge) {
  const q = merge ? "?merge=true" : ""
  return apiRequest(
    "/social-data/collections/" + encodeURIComponent(collection) + "/" + encodeURIComponent(id) + q,
    { method: "PUT", headers: authHeaders(true), body: JSON.stringify(body) },
  )
}

function splitPatch(updates) {
  const merge = {}
  const unset = []
  for (const [k, v] of Object.entries(updates)) {
    if (v && typeof v === "object" && v.__agentBaseDeleteField) unset.push(k)
    else if (v && typeof v === "object" && v.__agentBaseServerTimestamp) merge[k] = new Date().toISOString()
    else merge[k] = v
  }
  return { merge, unset }
}

export async function socialPatchFields(collection, id, updates) {
  const { merge, unset } = splitPatch(updates)
  return apiRequest("/social-data/collections/" + encodeURIComponent(collection) + "/" + encodeURIComponent(id), {
    method: "PATCH",
    headers: authHeaders(true),
    body: JSON.stringify({ merge, unset }),
  })
}

export async function socialDelete(collection, id) {
  await apiRequest("/social-data/collections/" + encodeURIComponent(collection) + "/" + encodeURIComponent(id), {
    method: "DELETE",
    headers: authHeaders(false),
  })
}

export async function automationListWorkflows() {
  const res = await apiRequest("/social-media/automation/workflows", { headers: authHeaders(false) })
  return Array.isArray(res?.items) ? res.items : []
}

export async function automationListEvents() {
  return socialList("automation_events")
}

function campaignCatalogCacheKey() {
  const u = window.__AGENTBASE__?.user || {}
  const wsUid = String(u.workspace_uid || u.workspaceUid || u.uid || "anon").trim() || "anon"
  const accId = String(s?.activeAccountId || "no-account").trim() || "no-account"
  return `campaign_catalog_cache_v2:${wsUid}:${accId}`
}

function campaignCatalogSignature(stores) {
  const rows = Array.isArray(stores) ? stores : []
  return rows.map((store) => {
    const sid = String(store?.id || "").trim()
    const campaigns = Array.isArray(store?.campaigns) ? store.campaigns : []
    const campStr = campaigns.map((c) => {
      const cid = String(c?.id || "").trim()
      const media = Array.isArray(c?.media) ? c.media.map((u) => String(u || "").trim()).filter(Boolean).join("|") : ""
      return `${cid}:${media}`
    }).join(";")
    return `${sid}[${campStr}]`
  }).join("||")
}

function readCampaignCatalogCache() {
  try {
    const raw = localStorage.getItem(campaignCatalogCacheKey())
    if (!raw) return null
    const parsed = JSON.parse(raw)
    if (!parsed || typeof parsed !== "object") return null
    if (Date.now() - Number(parsed.cachedAt || 0) > CAMPAIGN_CATALOG_CACHE_TTL_MS) return null
    if (!Array.isArray(parsed.stores) && !Array.isArray(parsed.campaigns)) return null
    const prov = parsed.provider && typeof parsed.provider === "object" ? parsed.provider : {}
    if (String(prov.source || "") === "example_py_fallback") return null
    if (!Array.isArray(parsed.campaigns)) parsed.campaigns = []
    if (!Array.isArray(parsed.stores)) parsed.stores = []
    return parsed
  } catch {
    return null
  }
}

function writeCampaignCatalogCache(stores, provider, campaigns = []) {
  try {
    const sigStores = campaignCatalogSignature(stores)
    const sigCamps = (Array.isArray(campaigns) ? campaigns : []).map((c) => String(c?.id ?? "")).join(",")
    localStorage.setItem(
      campaignCatalogCacheKey(),
      JSON.stringify({
        cachedAt: Date.now(),
        signature: `${sigStores}|${sigCamps}`,
        stores: Array.isArray(stores) ? stores : [],
        campaigns: Array.isArray(campaigns) ? campaigns : [],
        provider: provider || {},
      }),
    )
  } catch {
    /* */
  }
}

export function clearCampaignCatalogCache() {
  try {
    localStorage.removeItem(campaignCatalogCacheKey())
  } catch {
    /* */
  }
}

export function getLastCampaignCatalogErrorMsg() {
  return lastCampaignCatalogErrorMsg
}

export async function campaignLoadCatalog({ force = false } = {}) {
  if (CAMPAIGN_MODE && !campaignCatalogCredentialsReady()) {
    clearCampaignCatalogCache()
    lastCampaignCatalogErrorMsg =
      "Kampanya kataloğu için seçili hesapta Campaign API Key ve Campaign API Base URL girin."
    campaignCatalogNextFetchAt = Date.now() + CAMPAIGN_CATALOG_RETRY_MS
    s.campaignStores = []
    s.campaignList = []
    s.campaignCatalogProvider = {}
    return { stores: [], campaigns: [], provider: {}, upstream_error: lastCampaignCatalogErrorMsg }
  }
  const cached = !force ? readCampaignCatalogCache() : null
  const noAccountMode = !String(s.activeAccountId || "").trim()
  if (cached && Date.now() < campaignCatalogNextFetchAt) return cached
  if (cached && !force && noAccountMode) return cached
  if (!force && Date.now() < campaignCatalogNextFetchAt && (s.campaignStores.length || (s.campaignList || []).length)) {
    return { stores: s.campaignStores, campaigns: s.campaignList || [], provider: {} }
  }
  try {
    const q = new URLSearchParams()
    if (s.activeAccountId) q.set("campaign_account_id", s.activeAccountId)
    const data = await apiRequest("/social-media/campaign/catalog" + (q.toString() ? `?${q}` : ""), {
      headers: authHeaders(false),
    })
    const stores = Array.isArray(data?.stores) ? data.stores : []
    const campaigns = Array.isArray(data?.campaigns) ? data.campaigns : []
    const provider = data?.provider && typeof data.provider === "object" ? data.provider : {}
    s.campaignCatalogProvider = provider
    const upstreamErr = typeof data?.upstream_error === "string" ? String(data.upstream_error).trim() : ""
    if (upstreamErr) lastCampaignCatalogErrorMsg = upstreamErr
    else lastCampaignCatalogErrorMsg = ""
    const hasAny = stores.length || campaigns.length
    const nextSig = `${campaignCatalogSignature(stores)}|${(campaigns || []).map((c) => String(c?.id ?? "")).join(",")}`
    const cachedSig = cached?.signature || `${campaignCatalogSignature(cached?.stores || [])}|${(cached?.campaigns || []).map((c) => String(c?.id ?? "")).join(",")}`
    if (hasAny || noAccountMode || nextSig !== cachedSig) {
      writeCampaignCatalogCache(stores, provider, campaigns)
      s.campaignStores = stores
      s.campaignList = campaigns
    } else if (cached) {
      s.campaignStores = cached.stores
      s.campaignList = cached.campaigns || []
    }
    campaignCatalogNextFetchAt = 0
    return { stores: s.campaignStores, campaigns: s.campaignList, provider, ...(upstreamErr ? { upstream_error: upstreamErr } : {}) }
  } catch (err) {
    lastCampaignCatalogErrorMsg = err instanceof Error ? err.message : String(err || "")
    campaignCatalogNextFetchAt = Date.now() + CAMPAIGN_CATALOG_RETRY_MS
    const msg = lastCampaignCatalogErrorMsg.toLowerCase()
    const looksConfig =
      msg.includes("campaign api") ||
      msg.includes("api key") ||
      msg.includes("base url") ||
      msg.includes("ayarlanmamis") ||
      msg.includes("kampanya hesabi") ||
      msg.includes("yok.")
    if (cached && looksConfig) {
      clearCampaignCatalogCache()
      s.campaignStores = []
      s.campaignList = []
      return { stores: [], campaigns: [], provider: {}, upstream_error: lastCampaignCatalogErrorMsg }
    }
    if (cached) {
      s.campaignStores = cached.stores
      s.campaignList = cached.campaigns || []
      return cached
    }
    throw err
  }
}

export function usesStoreDiscountedProductCatalog() {
  if (!CAMPAIGN_MODE) return false
  const acc = activeAccount()
  const base = String(acc?.campaignApiBaseUrl || "").trim() || DEFAULT_CAMPAIGN_API_BASE_URL
  return isSepetlerCampaignApiBase(base)
}

export function attachDiscountedProductsToCampaignStore(storeId, products) {
  const sid = String(storeId || "").trim()
  for (const row of s.campaignStores || []) {
    if (String(row?.id || "").trim() === sid) {
      row.campaigns = Array.isArray(products) ? products : []
      return
    }
  }
}

export async function campaignLoadStoreDiscountedProducts(storeId) {
  const sid = String(storeId || "").trim()
  if (!sid || !/^\d+$/.test(sid)) {
    attachDiscountedProductsToCampaignStore(sid, [])
    return []
  }
  if (!campaignCatalogCredentialsReady()) {
    const msg = "Campaign API kimlik bilgileri eksik."
    s.campaignStoreProductsError = msg
    attachDiscountedProductsToCampaignStore(sid, [])
    throw new Error(msg)
  }
  const q = new URLSearchParams({ store_id: sid })
  if (s.activeAccountId) q.set("campaign_account_id", s.activeAccountId)
  s.campaignStoreProductsLoading = true
  s.campaignStoreProductsError = ""
  try {
    const data = await apiRequest("/social-media/campaign/store-products?" + q, {
      headers: authHeaders(false),
    })
    const products = Array.isArray(data?.products) ? data.products : []
    attachDiscountedProductsToCampaignStore(sid, products)
    if (!products.length) {
      s.campaignStoreProductsError = "Bu magazada indirimli urun bulunamadi."
    }
    return products
  } catch (err) {
    s.campaignStoreProductsError = err instanceof Error ? err.message : String(err || "")
    attachDiscountedProductsToCampaignStore(sid, [])
    throw err
  } finally {
    s.campaignStoreProductsLoading = false
  }
}

export async function campaignPublish(body) {
  return apiRequest("/social-media/campaign/publish", {
    method: "POST",
    headers: authHeaders(true),
    body: JSON.stringify(body || {}),
  })
}

export async function socialClaimPublish(postId) {
  const r = await apiRequest(
    "/social-data/collections/" + encodeURIComponent(scheduledPostsCollection()) + "/" + encodeURIComponent(postId) + "/claim-publish",
    { method: "POST", headers: authHeaders(false) },
  )
  return Boolean(r.claimed)
}

export async function postInstagram(body) {
  const response = await fetch(apiBase() + "/social-media/post", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  })
  const rawText = await response.text()
  let data = {}
  let jsonError = false
  try {
    data = rawText ? JSON.parse(rawText) : {}
  } catch {
    jsonError = true
  }
  const ok = !jsonError && response.ok && data.success === true
  return { ok, status: response.status, data, rawText, jsonError }
}

export async function deleteStorageImages(urls) {
  const list = Array.isArray(urls) ? urls : [urls]
  const filtered = list.map((u) => (u || "").trim()).filter(Boolean)
  if (!filtered.length) return
  try {
    await fetch(apiBase() + "/social-media/image/delete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ urls: filtered }),
    })
  } catch {
    /* */
  }
}

export function isManagedUploadStorageUrl(raw) {
  const t = (raw || "").trim()
  if (!t) return false
  if (t.includes("/media/api_uploads/") || t.includes("/media/user_templates/")) return true
  if (t.includes(".r2.dev/")) return true
  return false
}

export function collectManagedUrlsForPost(post) {
  const urls = new Set()
  const add = (u) => {
    const x = (u || "").trim()
    if (isManagedUploadStorageUrl(x)) urls.add(x)
  }
  add(post.imageUrl)
  ;(post.imageUrls || []).forEach(add)
  try {
    const snap = post.revisionSnapshotJson ? JSON.parse(post.revisionSnapshotJson) : null
    if (snap?.revisionMap && typeof snap.revisionMap === "object") {
      Object.entries(snap.revisionMap).forEach(([base, list]) => {
        add(base)
        if (Array.isArray(list)) list.forEach(add)
      })
    }
  } catch {
    /* */
  }
  return [...urls]
}

export async function getTaskStatus(taskId) {
  return apiRequest("/social-media/tasks/" + encodeURIComponent(taskId), { headers: authHeaders(false) })
}

export async function resolveQueued(data, intervalMs = 2000, maxWaitMs = 900000) {
  if (!data || data.queued !== true || !data.task_id) return data
  const start = Date.now()
  while (Date.now() - start < maxWaitMs) {
    await new Promise((r) => window.setTimeout(r, intervalMs))
    const status = await getTaskStatus(data.task_id)
    if (status.status === "succeeded") return status.result || status
    if (status.status === "failed" || status.status === "cancelled") {
      throw new Error(status.error || status.status)
    }
  }
  throw new Error("Task timed out")
}

export async function fetchLinkedIg(accessToken) {
  return apiRequest("/social-media/instagram/linked-accounts", {
    method: "POST",
    headers: authHeaders(true),
    body: JSON.stringify({ access_token: accessToken.trim() }),
  })
}

export async function enrichAccountGraphIds(accountId, tok) {
  const t = tok.trim()
  if (t.length < 10) return false
  try {
    const { accounts } = await fetchLinkedIg(t)
    const row = accounts && accounts[0]
    const ig = String(row?.instagram_user_id ?? "").trim()
    if (!ig) return false
    const patch = { instagramUserId: ig, updatedAt: TS }
    const fb = String(row?.facebook_page_id ?? "").trim()
    if (fb) patch.facebookPageId = fb
    await socialPatchFields(accountsCollection(), accountId, patch)
    return true
  } catch {
    return false
  }
}
