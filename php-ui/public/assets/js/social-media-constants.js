export const DRAFTS = "composer_drafts"
export const CAMPAIGN_DRAFTS = "campaign_composer_drafts"
export const ACCOUNTS = "accounts"
export const CAMPAIGN_ACCOUNTS = "campaign_accounts"
export const SCHEDULED_POSTS = "scheduled_posts"
export const CAMPAIGN_SCHEDULED_POSTS = "campaign_scheduled_posts"
export const USER_TEMPLATES = "content_templates"
export const GLOBAL_TEMPLATES = "content_templates_global"
export const CAMPAIGN_USER_TEMPLATES = "campaign_templates"
export const CAMPAIGN_GLOBAL_TEMPLATES = "campaign_templates_global"

/** Sosyal medya şablon çıktı boyutu presetleri (OpenAI gpt-image-1, 16x snap). */
export const OUTPUT_SIZE_PRESETS = {
  square: "1024x1024",
  post_4_5: "1088x1360",
  story: "1088x1920",
}
export const DEFAULT_OUTPUT_SIZE_PRESET = "post_4_5"
export function resolveOutputSize(preset) {
  const k = String(preset || "").trim()
  return OUTPUT_SIZE_PRESETS[k] || OUTPUT_SIZE_PRESETS[DEFAULT_OUTPUT_SIZE_PRESET]
}
export const APP_SETTINGS_COLLECTION = "app_settings"
export const APP_SETTINGS_DOC_ID = "api_keys"
export const GRACE_MS = 6 * 60 * 60 * 1000
export const CAMPAIGN_CATALOG_CACHE_TTL_MS = 60 * 1000
export const CAMPAIGN_CATALOG_RETRY_MS = 15 * 1000
/** Sepetler canlı AI API — kampanya hesabı modalında varsayılan base URL. */
export const DEFAULT_CAMPAIGN_API_BASE_URL = "https://mtlive.sepetler.com/api/ai/v1"
export const LABEL_COLORS = [
  "#ef4444", "#f97316", "#eab308", "#22c55e",
  "#14b8a6", "#3b82f6", "#8b5cf6", "#ec4899",
  "#6b7280", "#1e293b",
]

export const HOLIDAY_WATCH_KEY = "app_settings_holiday_yearly_watchlist"
export const CAMPAIGN_HOLIDAY_WATCH_KEY = "app_settings_campaign_holiday_yearly_watchlist"
/** React `src/lib/socialMediaPendingTasks.ts` ile ayni anahtar. */
export const PENDING_TASKS_STORAGE_KEY = "social_media_pending_tasks_v1"
export const SM_CAPTION_IN_FLIGHT_KEY = "sm_composer_caption_in_flight"
export const SM_IMAGE_HTTP_IN_FLIGHT_KEY = "sm_composer_image_http_in_flight"
/** `src/lib/composerInFlightStorage.ts` SM_VISUAL_PENDING_HINT_KEY */
export const SM_VISUAL_PENDING_HINT_KEY = "sm_visual_pending_hint"
export const MAX_VISUAL_PENDING_HINT_MS = 30 * 60 * 1000
export const PENDING_POLL_MS = 3500
export const SM_DEBUG_MODE_KEY = "sm_debug_mode_v1"
export const SM_DEBUG_EVENTS_KEY = "sm_debug_events_v1"
export const SM_DEBUG_MAX_EVENTS = 400
export const SM_DEBUG_FLUSH_MS = 1200
export const SM_DEBUG_BATCH_MAX = 40

export const COMPOSER_PENDING_KINDS = new Set(["generate", "reference", "revise", "video"])
