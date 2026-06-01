import { formatDateKey } from "./social-media-post-utils.js"

function splitDoc(entry) {
  const data = { ...entry }
  const id = String(data.id ?? "")
  delete data.id
  return { id, data }
}

export function mapAccount(row) {
  const { id, data } = splitDoc(row)
  const campaignKind = String(data.campaignAccountKind ?? data.kind ?? "store").trim().toLowerCase()
  return {
    id,
    name: String(data.name ?? ""),
    instagramAccessToken: String(data.instagramAccessToken ?? ""),
    campaignAccountKind: campaignKind === "restaurant" ? "restaurant" : "store",
    campaignApiKey: String(data.campaignApiKey ?? ""),
    campaignApiBaseUrl: String(data.campaignApiBaseUrl ?? "").trim(),
    instagramUserId: typeof data.instagramUserId === "string" ? data.instagramUserId.trim() || undefined : undefined,
    facebookPageId: typeof data.facebookPageId === "string" ? data.facebookPageId.trim() || undefined : undefined,
    logoUrl: typeof data.logoUrl === "string" && data.logoUrl.trim() ? data.logoUrl.trim() : undefined,
    instagramTokenExpiresAt: typeof data.instagramTokenExpiresAt === "string" ? data.instagramTokenExpiresAt : undefined,
  }
}

export function mapPost(row) {
  const { id, data } = splitDoc(row)
  const scheduledAtRaw = String(data.scheduledAt ?? data.scheduled_at ?? "").trim()
  const parsedScheduled = scheduledAtRaw ? new Date(scheduledAtRaw) : null
  const hasParsedScheduled = parsedScheduled instanceof Date && !Number.isNaN(parsedScheduled.getTime())
  const derivedDate = hasParsedScheduled ? formatDateKey(parsedScheduled) : ""
  const derivedTime = hasParsedScheduled
    ? `${String(parsedScheduled.getHours()).padStart(2, "0")}:${String(parsedScheduled.getMinutes()).padStart(2, "0")}`
    : ""

  const rawStatus = String(data.publishStatus ?? data.status ?? "pending").toLowerCase()
  const publishStatus = rawStatus === "published"
    ? "published"
    : rawStatus === "failed"
      ? "failed"
      : rawStatus === "publishing" || rawStatus === "queued"
        ? "publishing"
        : "pending"

  const rawAp = String(data.approvalStatus ?? data.approvalState ?? data.approval_state ?? "")
    .trim()
    .toLowerCase()
  let approvalStatus =
    rawAp === "waiting_approval"
      ? "pending"
      : ["pending", "approved", "rejected"].includes(rawAp)
        ? rawAp
        : undefined
  if (!approvalStatus) {
    const pub = String(data.publishStatus ?? data.status ?? "")
      .trim()
      .toLowerCase()
    if (pub === "pending") approvalStatus = "pending"
  }
  const rawSrc = String(data.source ?? "")
  const source =
    rawSrc === "holiday" ||
    rawSrc === "manual" ||
    rawSrc === "store_workflow" ||
    rawSrc === "automation_rule" ||
    rawSrc === "campaign_banner"
      ? rawSrc
      : undefined
  const assetUrl = typeof data.asset === "object" && data.asset ? String(data.asset.url ?? "").trim() : ""
  const imageUrl = String(data.imageUrl ?? assetUrl ?? "").trim()
  const platform = String(data.platform ?? "instagram").trim()
  const contentType = String(data.contentType ?? data.content_type ?? "instagram_feed_post").trim()
  const operationId = String(data.operationId ?? data.operation_id ?? "").trim()
  const productId = String(data.productId ?? data.product_id ?? "").trim()
  return {
    id,
    accountId: String(data.accountId ?? ""),
    accountName: String(data.accountName ?? ""),
    date: String(data.date ?? data.scheduledDate ?? derivedDate ?? ""),
    time: String(data.time ?? data.scheduledTime ?? derivedTime ?? ""),
    prompt: String(data.prompt ?? ""),
    caption: String(data.caption ?? ""),
    imageUrl,
    imageUrls: Array.isArray(data.imageUrls) ? data.imageUrls.map((u) => String(u ?? "").trim()).filter(Boolean) : [],
    publishStatus,
    lastPublishError: typeof data.lastPublishError === "string" ? data.lastPublishError : undefined,
    approvalStatus,
    platform,
    contentType,
    scheduledAt: scheduledAtRaw,
    operationId,
    productId,
    status: String(data.status ?? "").trim() || undefined,
    approvalState: String(data.approvalState ?? data.approval_state ?? "").trim() || undefined,
    source,
    sourceRaw: rawSrc,
    holidayName: typeof data.holidayName === "string" ? data.holidayName : undefined,
    storeId: String(data.storeId ?? data.store_id ?? "").trim(),
    campaignAccountId: String(data.campaignAccountId ?? data.campaign_account_id ?? data.accountId ?? "").trim(),
    campaignStoreId: String(data.campaignStoreId ?? data.campaign_store_id ?? data.storeId ?? data.store_id ?? "").trim(),
    campaignStoreName: String(data.campaignStoreName ?? data.campaign_store_name ?? "").trim(),
    campaignId: String(data.campaignId ?? data.campaign_id ?? "").trim(),
    campaignName: String(data.campaignName ?? data.campaign_name ?? "").trim(),
    campaignStartDate: String(data.campaignStartDate ?? data.campaign_start_date ?? "").trim(),
    campaignEndDate: String(data.campaignEndDate ?? data.campaign_end_date ?? "").trim(),
    bannerSize: String(data.bannerSize ?? data.banner_size ?? "").trim(),
    campaignRedirectUrl: String(data.campaignRedirectUrl ?? data.redirect_url ?? "").trim(),
    campaignPricing: data.campaignPricing && typeof data.campaignPricing === "object" ? data.campaignPricing : undefined,
    automationWorkflowId: String(data.automationWorkflowId ?? data.automation_workflow_id ?? "").trim(),
    templateId: String(data.templateId ?? data.template_id ?? "").trim(),
    templateSnapshot: data.templateSnapshot && typeof data.templateSnapshot === "object" ? data.templateSnapshot : undefined,
    eventType: String(data.eventType ?? data.event_type ?? "").trim(),
    publishTargets:
      data.publishTargets && typeof data.publishTargets === "object"
        ? {
            instagramPost: Boolean(data.publishTargets.instagramPost ?? true),
            instagramStory: Boolean(data.publishTargets.instagramStory ?? true),
            facebookPost: Boolean(data.publishTargets.facebookPost ?? true),
          }
        : undefined,
    revisionSnapshotJson: typeof data.revisionSnapshotJson === "string" ? data.revisionSnapshotJson : undefined,
  }
}

export function mapDraft(row) {
  const { id, data } = splitDoc(row)
  let snapshot = null
  const jsonStr = typeof data.snapshotJson === "string" ? data.snapshotJson : null
  if (jsonStr) {
    try {
      snapshot = JSON.parse(jsonStr)
    } catch {
      /* */
    }
  }
  return {
    id,
    accountId: String(data.accountId ?? ""),
    accountName: String(data.accountName ?? ""),
    campaignAccountId: String(data.campaignAccountId ?? data.campaign_account_id ?? data.accountId ?? ""),
    campaignStoreId: String(data.campaignStoreId ?? data.campaign_store_id ?? ""),
    campaignId: String(data.campaignId ?? data.campaign_id ?? ""),
    campaignStartDate: String(data.campaignStartDate ?? data.campaign_start_date ?? ""),
    campaignEndDate: String(data.campaignEndDate ?? data.campaign_end_date ?? ""),
    date: String(data.date ?? ""),
    time: String(data.time ?? "12:00"),
    prompt: String(data.prompt ?? ""),
    caption: String(data.caption ?? ""),
    imageUrl: String(data.imageUrl ?? ""),
    imageUrls: Array.isArray(data.imageUrls) ? data.imageUrls.map((u) => String(u ?? "").trim()).filter(Boolean) : undefined,
    snapshot,
  }
}

export function mapWorkflow(row) {
  const { id, data } = splitDoc(row)
  return {
    id,
    workflowType: String(data.workflow_type ?? data.workflowType ?? "").trim(),
    storeId: String(data.store_id ?? data.storeId ?? "").trim(),
    scheduledFor: String(data.scheduled_for ?? data.scheduledFor ?? "").trim(),
    status: String(data.status ?? "pending").trim().toLowerCase(),
    cancellationPolicy: String(data.cancellation_policy ?? data.cancellationPolicy ?? "").trim(),
    templateId: String(data.template_id ?? data.templateId ?? "").trim(),
    scheduledPostId: String(data.scheduled_post_id ?? data.scheduledPostId ?? "").trim(),
    createdAt: String(data.created_at ?? data.createdAt ?? "").trim(),
    publishedAt: String(data.published_at ?? data.publishedAt ?? "").trim(),
    cancelledAt: String(data.cancelled_at ?? data.cancelledAt ?? "").trim(),
    cancelReason: String(data.cancel_reason ?? data.cancelReason ?? "").trim(),
  }
}

export function mapAutomationEvent(row) {
  const { id, data } = splitDoc(row)
  const payload = data.eventPayload && typeof data.eventPayload === "object" ? data.eventPayload : {}
  return {
    id,
    eventType: String(data.eventType ?? data.event_type ?? "").trim(),
    payload,
    triggeredAt: String(data.triggeredAt ?? data.triggered_at ?? data.createdAt ?? "").trim(),
  }
}
