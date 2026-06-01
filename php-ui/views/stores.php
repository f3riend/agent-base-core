<?php
declare(strict_types=1);
/** Mağazalar — Mağaza halkaları + ürün grid + sağ panel + modaller.
 * Backend: fake_commerce_app GET /commerce-platform/stores ve /stores/{id}/items
 * Mutate:  POST /commerce-platform/internal/create-store ve /create-product
 */
$apiBase = htmlspecialchars(app_browser_api_base(), ENT_QUOTES, 'UTF-8');
$cu = function_exists('app_current_user') ? app_current_user() : null;
$userId = isset($cu['id']) && is_int($cu['id'])
    ? (string) $cu['id']
    : (isset($cu['user_id']) && is_int($cu['user_id']) ? (string) $cu['user_id'] : '3');
$userIdAttr = htmlspecialchars($userId, ENT_QUOTES, 'UTF-8');
?>
<div class="sm-premium-page" id="stores-page">
  <header class="sm-premium-page__header" style="display:flex; justify-content:space-between; align-items:flex-start; gap:1rem; flex-wrap:wrap;">
    <div>
      <h1 class="sm-premium-page__title">Mağazalar</h1>
      <p class="sm-premium-page__subtitle">Mağazaları yönet ve ürünlerini gör.</p>
    </div>
    <button type="button" data-act="open-create-store"
            style="padding:0.55rem 1.1rem; background:#111827; color:#fff; border:0; border-radius:9999px; cursor:pointer; font-weight:600;">
      + Yeni Mağaza
    </button>
  </header>

  <section
    id="stores-app"
    data-api-base="<?= $apiBase ?>"
    data-user-id="<?= $userIdAttr ?>"
    style="display:flex; flex-direction:column; gap:1rem;"
  >
    <!-- Mağaza halkaları -->
    <div data-rings style="display:flex; gap:0.75rem; overflow-x:auto; padding:0.5rem 0.25rem; scrollbar-width:thin;">
      <p style="color:#9ca3af; padding:0.5rem;">Yükleniyor…</p>
    </div>

    <!-- Ürün grid + sağ panel -->
    <div style="display:grid; grid-template-columns:minmax(0,1fr) 320px; gap:1rem;" data-content>
      <div data-product-grid style="display:grid; grid-template-columns:repeat(auto-fill,minmax(220px,1fr)); gap:0.75rem;">
        <p style="color:#9ca3af; padding:1rem;">Önce bir mağaza seç.</p>
      </div>
      <aside data-side-panel style="background:#fff; border:1px solid #e5e7eb; border-radius:0.75rem; padding:1rem; height:fit-content; position:sticky; top:1rem;">
        <h3 style="margin:0 0 0.5rem; font-size:0.85rem; color:#6b7280; text-transform:uppercase; letter-spacing:0.04em;">Seçili Ürün</h3>
        <p style="margin:0; color:#9ca3af; font-size:0.85rem;">Bir karta tıklayarak detayları gör.</p>
      </aside>
    </div>
  </section>
</div>

<!-- Context menu (sağ tık + ⋮ buton) -->
<div id="stores-ctx-menu" hidden
     style="position:absolute; z-index:1000; background:#fff; border:1px solid #e5e7eb; border-radius:0.5rem; box-shadow:0 8px 24px rgba(15,23,42,0.12); min-width:180px; padding:0.3rem;">
  <button type="button" data-ctx="add-product" style="display:flex; width:100%; gap:0.5rem; padding:0.45rem 0.65rem; background:transparent; border:0; cursor:pointer; text-align:left; border-radius:0.4rem;">📦 <span>Ürün Ekle</span></button>
  <button type="button" data-ctx="view-products" style="display:flex; width:100%; gap:0.5rem; padding:0.45rem 0.65rem; background:transparent; border:0; cursor:pointer; text-align:left; border-radius:0.4rem;">📋 <span>Ürünleri Gör</span></button>
</div>

<!-- Modal: Yeni Mağaza -->
<div data-modal="create-store" hidden
     style="position:fixed; inset:0; z-index:900; background:rgba(15,23,42,0.4); display:flex; align-items:center; justify-content:center; padding:1rem;">
  <form data-modal-form="create-store"
        style="background:#fff; border-radius:0.75rem; padding:1.25rem; width:100%; max-width:480px; display:flex; flex-direction:column; gap:0.6rem;">
    <h2 style="margin:0; font-size:1.1rem;">Yeni Mağaza</h2>
    <label style="display:flex; flex-direction:column; gap:0.2rem; font-size:0.85rem;">Mağaza Adı <span style="color:#dc2626;">*</span>
      <input type="text" name="name" required style="padding:0.45rem 0.6rem; border:1px solid #e5e7eb; border-radius:0.4rem;">
    </label>
    <label style="display:flex; flex-direction:column; gap:0.2rem; font-size:0.85rem;">Sahip
      <input type="text" name="owner" style="padding:0.45rem 0.6rem; border:1px solid #e5e7eb; border-radius:0.4rem;">
    </label>
    <label style="display:flex; flex-direction:column; gap:0.2rem; font-size:0.85rem;">Instagram
      <input type="text" name="instagram" placeholder="@handle" style="padding:0.45rem 0.6rem; border:1px solid #e5e7eb; border-radius:0.4rem;">
    </label>
    <label style="display:flex; flex-direction:column; gap:0.2rem; font-size:0.85rem;">Logo URL
      <input type="url" name="logo_url" style="padding:0.45rem 0.6rem; border:1px solid #e5e7eb; border-radius:0.4rem;">
    </label>
    <label style="display:flex; flex-direction:column; gap:0.2rem; font-size:0.85rem;">Banner URL
      <input type="url" name="banner_url" style="padding:0.45rem 0.6rem; border:1px solid #e5e7eb; border-radius:0.4rem;">
    </label>
    <div style="display:flex; gap:0.5rem; justify-content:flex-end; margin-top:0.4rem;">
      <button type="button" data-modal-cancel
              style="padding:0.5rem 1rem; background:#fff; color:#374151; border:1px solid #e5e7eb; border-radius:0.5rem; cursor:pointer;">İptal</button>
      <button type="submit"
              style="padding:0.5rem 1rem; background:#111827; color:#fff; border:0; border-radius:0.5rem; cursor:pointer; font-weight:600;">Oluştur</button>
    </div>
    <div data-modal-result></div>
  </form>
</div>

<!-- Modal: Ürün Ekle -->
<div data-modal="create-product" hidden
     style="position:fixed; inset:0; z-index:900; background:rgba(15,23,42,0.4); display:flex; align-items:center; justify-content:center; padding:1rem;">
  <form data-modal-form="create-product"
        style="background:#fff; border-radius:0.75rem; padding:1.25rem; width:100%; max-width:480px; display:flex; flex-direction:column; gap:0.6rem;">
    <h2 style="margin:0; font-size:1.1rem;">Ürün Ekle — <span data-modal-title-store></span></h2>
    <input type="hidden" name="store_id">
    <label style="display:flex; flex-direction:column; gap:0.2rem; font-size:0.85rem;">Ürün Adı <span style="color:#dc2626;">*</span>
      <input type="text" name="name" required style="padding:0.45rem 0.6rem; border:1px solid #e5e7eb; border-radius:0.4rem;">
    </label>
    <label style="display:flex; flex-direction:column; gap:0.2rem; font-size:0.85rem;">Fiyat (TL) <span style="color:#dc2626;">*</span>
      <input type="number" name="price" step="0.01" min="0" required style="padding:0.45rem 0.6rem; border:1px solid #e5e7eb; border-radius:0.4rem;">
    </label>
    <label style="display:flex; flex-direction:column; gap:0.2rem; font-size:0.85rem;">İndirim (%)
      <input type="number" name="discount_percent" step="0.1" min="0" max="100" style="padding:0.45rem 0.6rem; border:1px solid #e5e7eb; border-radius:0.4rem;">
    </label>
    <label style="display:flex; flex-direction:column; gap:0.2rem; font-size:0.85rem;">Stok
      <input type="number" name="stock" value="50" min="0" style="padding:0.45rem 0.6rem; border:1px solid #e5e7eb; border-radius:0.4rem;">
    </label>
    <label style="display:flex; flex-direction:column; gap:0.2rem; font-size:0.85rem;">Kategori
      <input type="text" name="category" style="padding:0.45rem 0.6rem; border:1px solid #e5e7eb; border-radius:0.4rem;">
    </label>
    <label style="display:flex; flex-direction:column; gap:0.2rem; font-size:0.85rem;">Görsel URL
      <input type="url" name="image_url" style="padding:0.45rem 0.6rem; border:1px solid #e5e7eb; border-radius:0.4rem;">
    </label>
    <div style="display:flex; gap:0.5rem; justify-content:flex-end; margin-top:0.4rem;">
      <button type="button" data-modal-cancel
              style="padding:0.5rem 1rem; background:#fff; color:#374151; border:1px solid #e5e7eb; border-radius:0.5rem; cursor:pointer;">İptal</button>
      <button type="submit"
              style="padding:0.5rem 1rem; background:#16a34a; color:#fff; border:0; border-radius:0.5rem; cursor:pointer; font-weight:600;">Ürün Ekle</button>
    </div>
    <div data-modal-result></div>
  </form>
</div>

<!-- Modal: Ürünleri Gör (fullscreen) -->
<div data-modal="view-products" hidden
     style="position:fixed; inset:0; z-index:900; background:rgba(15,23,42,0.4); display:flex; align-items:center; justify-content:center; padding:1rem;">
  <div style="background:#fff; border-radius:0.75rem; padding:1.25rem; width:100%; max-width:1100px; max-height:90vh; overflow:auto; display:flex; flex-direction:column; gap:0.75rem;">
    <header style="display:flex; justify-content:space-between; align-items:center;">
      <h2 style="margin:0; font-size:1.1rem;"><span data-modal-title-store></span> Ürünleri</h2>
      <button type="button" data-modal-cancel
              style="padding:0.4rem 0.85rem; background:#fff; color:#374151; border:1px solid #e5e7eb; border-radius:0.5rem; cursor:pointer;">Kapat</button>
    </header>
    <div data-fullscreen-grid style="display:grid; grid-template-columns:repeat(auto-fill,minmax(200px,1fr)); gap:0.65rem;"></div>
  </div>
</div>
<script type="module" src="<?= htmlspecialchars(app_url('/assets/js/stores-app.js'), ENT_QUOTES, 'UTF-8') ?>" defer></script>
