import { T } from "./social-media-api.js"
import { CAMPAIGN_MODE, scheduledPostsCollection } from "./social-media-campaign-utils.js"
import { DEL, postInstagram, socialClaimPublish, socialPatchFields, TS } from "./social-media-data.js"
import { isApprovedForAutoPublish, mergePostImageListOrdered, scheduleSlotTiming } from "./social-media-post-utils.js"
import { s } from "./social-media-state.js"

export async function autoPublishSweep({ buildIntegration, hasPostApiResults, refreshData, setStatus }) {
  if (CAMPAIGN_MODE) return
  if (s.publishBusy) return
  const now = Date.now()
  for (const post of s.posts) {
    const ps = post.publishStatus || "pending"
    if (ps === "published" || ps === "failed" || ps === "publishing") continue
    if (!isApprovedForAutoPublish(post)) continue
    const wantFeed = post.publishTargets?.instagramPost ?? true
    const wantStory = post.publishTargets?.instagramStory ?? false
    const wantFb = post.publishTargets?.facebookPost ?? false
    if (!wantFeed && !wantStory && !wantFb) continue
    if (scheduleSlotTiming(post, now) !== "due") continue
    if (!post.imageUrl.trim()) continue
    if (wantFeed && !post.caption.trim()) continue
    const account = s.accounts.find((a) => a.id === post.accountId)
    if (!account) {
      try {
        await socialPatchFields(scheduledPostsCollection(), post.id, {
          publishStatus: "failed",
          lastPublishError: T("msgAccountNotFoundPublish"),
          publishStartedAt: DEL,
        })
      } catch {
        /* */
      }
      continue
    }
    let claimed = false
    try {
      claimed = await socialClaimPublish(post.id)
    } catch {
      continue
    }
    if (!claimed) continue
    s.publishBusy = true
    try {
      await socialPatchFields(scheduledPostsCollection(), post.id, {
        publishStatus: "publishing",
        status: "publishing",
        publishStartedAt: TS,
        lastPublishError: DEL,
      })
      await refreshData()
      const imgs = mergePostImageListOrdered(post.imageUrl, post.imageUrls || [])
      const { ok, status, data, rawText, jsonError } = await postInstagram({
        image_url: imgs[0] ?? post.imageUrl.trim(),
        image_urls: imgs,
        caption: post.caption.trim(),
        publish_targets: {
          instagram_post: wantFeed,
          instagram_story: wantStory,
          facebook_post: wantFb,
        },
        ...buildIntegration(account),
      })
      if (jsonError) {
        await socialPatchFields(scheduledPostsCollection(), post.id, {
          publishStatus: "failed",
          lastPublishError: T("msgCalendarJsonStoredError")
            .replace("{status}", String(status))
            .replace("{snippet}", rawText.slice(0, 200)),
          publishStartedAt: DEL,
        })
      } else if (ok || (!jsonError && status >= 200 && status < 400 && hasPostApiResults(data))) {
        await socialPatchFields(scheduledPostsCollection(), post.id, {
          publishStatus: "published",
          status: "published",
          publishedAt: TS,
          publishStartedAt: DEL,
          lastPublishError: DEL,
        })
        setStatus(T("msgAutoPublishOk").replace("{name}", post.accountName))
      } else {
        const err = String((data && (data.detail || data.error)) || "publish").slice(0, 500)
        await socialPatchFields(scheduledPostsCollection(), post.id, {
          publishStatus: "failed",
          status: "failed",
          lastPublishError: err,
          publishStartedAt: DEL,
        })
        setStatus(T("msgAutoPublishFailed").replace("{name}", post.accountName))
      }
    } catch {
      /* */
    } finally {
      s.publishBusy = false
    }
    await refreshData()
    return
  }
}
