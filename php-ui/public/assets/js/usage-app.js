import { authHeaders, apiRequest, esc } from "./social-media-api.js"

const KIND_LABELS = {
  caption: "Açıklama",
  caption_revize: "Açıklama Revize",
  image: "Görsel",
  image_reference: "Görsel (Referans)",
  image_revise: "Görsel Revize",
  video: "Video",
  holiday: "Tatil İçeriği",
}

function fmtUsd(n) {
  const v = Number(n || 0)
  return v.toFixed(v >= 1 ? 2 : 4) + " USD"
}

function statCard(title, value, sub) {
  return `<div class="rounded-2xl border border-gray-200 bg-gray-50 p-4">
    <p class="text-xs font-semibold uppercase tracking-wide text-gray-500">${esc(title)}</p>
    <p class="mt-1 text-2xl font-semibold text-gray-900">${esc(value)}</p>
    ${sub ? `<p class="mt-0.5 text-xs text-gray-500">${esc(sub)}</p>` : ""}
  </div>`
}

function table(headers, rows, emptyText) {
  if (!rows.length) {
    return `<p class="rounded-xl bg-gray-50 px-4 py-4 text-sm text-gray-500">${esc(emptyText)}</p>`
  }
  return `<div class="overflow-hidden rounded-xl border border-gray-200">
    <table class="w-full text-sm">
      <thead class="bg-gray-50 text-left text-xs font-semibold uppercase tracking-wide text-gray-500">
        <tr>${headers.map((h) => `<th class="px-4 py-2">${esc(h)}</th>`).join("")}</tr>
      </thead>
      <tbody>
        ${rows
          .map(
            (r) =>
              `<tr class="border-t border-gray-100">${r
                .map((c) => `<td class="px-4 py-2 text-gray-800">${esc(String(c))}</td>`)
                .join("")}</tr>`,
          )
          .join("")}
      </tbody>
    </table>
  </div>`
}

async function load() {
  const root = document.getElementById("usage-root")
  if (!root) return
  try {
    const summary = await apiRequest("/social-media/usage/summary?days=90", { headers: authHeaders(false) })
    render(root, summary)
  } catch (err) {
    root.innerHTML = `<p class="rounded-xl bg-red-50 px-4 py-4 text-sm text-red-700">Yüklenemedi: ${esc(err instanceof Error ? err.message : String(err))}</p>`
  }
}

function render(root, s) {
  const summary = s && typeof s === "object" ? s : {}
  const cards = `<div class="grid grid-cols-1 gap-3 sm:grid-cols-4">
    ${statCard("Bugün", fmtUsd(summary.today_usd))}
    ${statCard("Bu Ay", fmtUsd(summary.this_month_usd))}
    ${statCard("Toplam (90 gün)", fmtUsd(summary.total_usd))}
    ${statCard("Ortalama Günlük", fmtUsd(summary.average_daily_usd))}
  </div>`

  const byKindRows = (summary.by_kind || []).map((k) => [
    KIND_LABELS[k.kind] || k.kind,
    k.count,
    fmtUsd(k.cost_usd),
  ])
  const byAccountRows = (summary.by_account || []).map((a) => [a.account_name, fmtUsd(a.cost_usd)])
  const byDayRows = (summary.by_day || []).slice(-30).reverse().map((d) => [d.date, fmtUsd(d.cost_usd)])
  const byMonthRows = (summary.by_month || []).slice(-12).reverse().map((m) => [m.month, fmtUsd(m.cost_usd)])

  root.innerHTML = `
${cards}
<section class="mt-6 grid grid-cols-1 gap-4 lg:grid-cols-2">
  <div>
    <h2 class="mb-2 text-sm font-semibold text-gray-900">Üretim Türüne Göre</h2>
    ${table(["Tür", "Adet", "Maliyet"], byKindRows, "Henüz kayıt yok.")}
  </div>
  <div>
    <h2 class="mb-2 text-sm font-semibold text-gray-900">Hesap Bazında</h2>
    ${table(["Hesap", "Maliyet"], byAccountRows, "Hesap bilgisi olan kayıt yok.")}
  </div>
  <div>
    <h2 class="mb-2 text-sm font-semibold text-gray-900">Son 30 Gün</h2>
    ${table(["Tarih", "Maliyet"], byDayRows, "Henüz kayıt yok.")}
  </div>
  <div>
    <h2 class="mb-2 text-sm font-semibold text-gray-900">Aylık</h2>
    ${table(["Ay", "Maliyet"], byMonthRows, "Henüz kayıt yok.")}
  </div>
</section>`
}

void load()
