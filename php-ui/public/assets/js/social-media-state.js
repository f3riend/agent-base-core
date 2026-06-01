import { CAMPAIGN_MODE } from "./social-media-campaign-utils.js"
import { CAMPAIGN_HOLIDAY_WATCH_KEY, HOLIDAY_WATCH_KEY } from "./social-media-constants.js"
import { formatDateKey } from "./social-media-post-utils.js"
import { configurePendingState } from "./social-media-runtime.js"

export const s = {
  accounts: [],
  posts: [],
  workflows: [],
  automationEvents: [],
  selectedWorkflowId: "",
  drafts: [],
  tickets: [],
  userTemplates: [],
  globalTemplates: [],
  appSettings: { openaiApiKey: "", falApiKey: "" },
  monthDate: new Date(new Date().getFullYear(), new Date().getMonth(), 1),
  selectedDate: formatDateKey(new Date()),
  filterAccountId: null,
  activeAccountId: "",
  campaignStores: [],
  campaignCatalogProvider: {},
  campaignStoreProductsLoading: false,
  campaignStoreProductsError: "",
  campaignStoreId: "",
  campaignId: "",
  campaignMediaLoading: false,
  campaignMediaUrls: [],
  campaignMediaKey: "",
  mediaItems: [],
  selectedMediaId: "",
  campaignStartDate: formatDateKey(new Date()),
  campaignEndDate: formatDateKey(new Date(new Date().getTime() + 7 * 24 * 60 * 60 * 1000)),
  dayScope: "today",
  dayListTab: "approved",
  draggingPostId: null,
  draggingRailAssetUrl: null,
  pendingTaskProgress: {},
  dragOverDateKey: null,
  dropFlashDateKey: null,
  contextAccountId: null,
  contextX: 0,
  contextY: 0,
  dayMenu: null,
  holidaySettings: null,
  studioOpen: false,
  studioDefaultTab: "manual",
  editingPostId: null,
  activeDraftId: null,
  editingPostCost: 0,
  activeDraftCost: 0,
  caption: "",
  prompt: "",
  imageUrl: "",
  scheduledTime: "12:00",
  composerApproved: false,
  publishTargets: { instagramPost: true, instagramStory: true, facebookPost: true },
  accountModal: false,
  editingAccountId: null,
  accName: "",
  accToken: "",
  accCampaignBaseUrl: "",
  accCampaignKind: "store",
  accLogo: "",
  logoUploading: false,
  linkedRows: [],
  linkedSelected: new Set(),
  linkedLoading: false,
  linkedErr: "",
  ticketModal: false,
  ticketDraft: { name: "", description: "" },
  editingTicket: null,
  templateModal: false,
  templateDraft: { title: "", prompt: "", imageUrls: [], outputSize: "post_4_5" },
  editingTemplate: null,
  selectedTemplateId: null,
  selectedTemplateScope: "user",
  templateUploading: false,
  statusLine: "",
  sessionId: "",
  holidayBusyDateKey: null,
  publishBusy: false,
  studioTab: "manual",
  composerStep: 0,
  lastTopic: "",
  captionReviseFeedback: "",
  imageVariantCount: 1,
  composerBusy: false,
  studioPublishBusy: false,
  captionMode: "manual",
  visualOutputKind: "image",
  mediaMode: "manual",
  generateSubTab: "manual",
  selectedTicketId: null,
  useSelectedAsReference: false,
  useSelectedRefsForRevise: false,
  directImagePrompt: "",
  reviseFeedback: "",
  aiImageUrls: [],
  uploadedImageUrls: [],
  videoAiMode: "text",
  videoDurationSec: 5,
  videoGenerateAudio: true,
  holidayVideoName: "",
  holidayVideoDate: "",
  revisionMap: {},
  selectedRevisionByBase: {},
  graphPublishCards: null,
  graphPublishLoading: false,
  graphPublishError: "",
  selectedGraphPublishKey: null,
  modalPanel: "caption",
  /** "post" | "story" — sağ tıkla menüden hangisinin seçildiğine göre Studio modal davranışı (boyut, toggle filtre, şablon filtre). */
  studioMode: "post",
  selectedPostId: "",
  editingPostCost: 0,
  activeDraftCost: 0,
  referenceCheckedUrls: [],
  assetOrder: [],
  dayCardSlides: {},
  dayCardTouch: null,
}

configurePendingState(() => s)

export let rootEl = null

export function setRootEl(el) {
  rootEl = el
}

export let pendingPollTimer = null

export function setPendingPollTimer(timer) {
  pendingPollTimer = timer
}

export let lastServerDataSig = undefined

export function setLastServerDataSig(sig) {
  lastServerDataSig = sig
}

export function holidayWatchStorageKey() {
  return CAMPAIGN_MODE ? CAMPAIGN_HOLIDAY_WATCH_KEY : HOLIDAY_WATCH_KEY
}
