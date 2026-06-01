import requests
import base64
from PIL import Image, ImageDraw
import io

API_KEY = "aio_nFuhjG80SbcAEL86ZpB5Uh0qYosjZySqwDAwrHXN8mwG8hCy"
BASE = "https://mtlive.sepetler.com/api/ai/v1"
headers = {"Authorization": f"Bearer {API_KEY}"}

# IP kaydet
ip = requests.get("https://mtlive.sepetler.com/?ip_guncelle=dev")
print(f"IP kayıt: {ip.text.strip()}")

# --- 1. Banner listesi ---
# GET /banners?limit=5&direction=desc
# Başarılı yanıt (200):
# {
#   "data": [
#     {
#       "id": 37,
#       "title": "Satıcı Ol",
#       "type": "default",                  # default | store_wise | item_wise
#       "image": "2026-03-10-69b01223e3607.png",
#       "image_full_url": "https://mtlive.sepetler.com/storage/app/public/banner/2026-03-10-69b01223e3607.png",
#       "status": true,
#       "featured": false,
#       "data": 0,
#       "zone": { "id": 2, "name": "Türkiye" },   # zone_id ile oluşturulduysa dolu, yoksa null
#       "target_store": null,                       # target_store_id ile oluşturulduysa dolu
#       "module": { "id": 4, "name": "Alışveriş" },
#       "default_link": null,
#       "start_date": null,                         # null = sınırsız
#       "end_date": null,
#       "created_by": "admin",                      # admin | store
#       "created_at": "2026-03-10T14:44:19+02:00"
#     }
#   ],
#   "meta": {
#     "limit": 5,
#     "direction": "desc",
#     "next_cursor": 33,
#     "count": 5
#   }
# }
print("\n=== BANNER LİSTESİ ===")
r = requests.get(f"{BASE}/banners", headers=headers, params={"limit": 5, "direction": "desc"})
print(f"Status: {r.status_code}")
banners = r.json()["data"]
for b in banners:
    print(f"  ID={b['id']} | {b['title']} | status={b['status']} | created_by={b['created_by']}")


# --- 2. Banner oluştur ---
# POST /banners
# Request body:
# {
#   "title": "Banner Başlığı",          # zorunlu, max 191 karakter
#   "image_base64": "data:image/png;base64,iVBOR...",  # image_url yoksa zorunlu
#   "image_url": "https://...",          # image_base64 yoksa zorunlu
#   "zone_id": 2,                        # admin scope: zone_id VEYA target_store_id (ikisi birden olmaz)
#   "target_store_id": 3,                # mağazanın tüm hizmet bölgelerinde göster
#   "type": "default",                   # default | store_wise | item_wise
#   "data": 0,                           # type=item_wise → item_id, type=store_wise → store_id
#   "default_link": "https://...",       # afişe tıklayınca gidilecek URL
#   "start_date": "2026-05-18",          # null = sınırsız başlangıç
#   "end_date": "2026-05-25",            # null = sınırsız bitiş
#   "status": true                       # varsayılan true
# }
#
# Başarılı yanıt (201):
# {
#   "data": {
#     "id": 43,
#     "title": "Test Banner - Silinecek",
#     "type": "default",
#     "image": "2026-05-19-6a0c4dbddf75c.png",
#     "image_full_url": "https://mtlive.sepetler.com/storage/app/public/banner/2026-05-19-6a0c4dbddf75c.png",
#     "status": true,
#     "featured": false,
#     "data": 0,
#     "zone": { "id": 2, "name": "Türkiye" },
#     "target_store": null,
#     "module": { "id": 1, "name": "Demo Module" },
#     "default_link": null,
#     "start_date": null,
#     "end_date": null,
#     "created_by": "admin",
#     "created_at": "2026-05-19T14:45:59+03:00"
#   }
# }
#
# Hata yanıtları:
# 422 validation_error   → alan hatası
# 422 xor_violation      → zone_id + target_store_id ikisi birden gönderildi
# 422 missing_target     → admin scope'ta ikisi de gönderilmedi
# 422 image_error        → geçersiz base64, desteklenmeyen MIME, 5MB aşımı
print("\n=== BANNER OLUŞTUR ===")
img = Image.new("RGB", (900, 300), color=(30, 30, 80))
draw = ImageDraw.Draw(img)
draw.text((450, 130), "TEST BANNER", fill=(255, 255, 255), anchor="mm")
draw.text((450, 170), "API Test - Silinecek", fill=(180, 180, 180), anchor="mm")

buffer = io.BytesIO()
img.save(buffer, format="PNG")
b64 = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()

r = requests.post(
    f"{BASE}/banners",
    headers=headers,
    json={
        "title": "Test Banner - Silinecek",
        "image_base64": b64,
        "zone_id": 2,
        "status": True
    }
)
print(f"Status: {r.status_code}")
new_banner = r.json()["data"]
banner_id = new_banner["id"]
print(f"Oluşturuldu: ID={banner_id} | {new_banner['title']} | {new_banner['image_full_url']}")


# --- 3. Tek banner getir ---
# GET /banners/{id}
# Başarılı yanıt (200): yukarıdaki POST yanıtıyla aynı yapı
# Hata: 403 forbidden → token bu bannera erişemez
#        404 not_found  → banner yok veya silinmiş
print(f"\n=== BANNER DETAY (ID={banner_id}) ===")
r = requests.get(f"{BASE}/banners/{banner_id}", headers=headers)
print(f"Status: {r.status_code}")
print(f"Body: {r.text[:300]}")


# --- 4. Banner sil ---
# DELETE /banners/{id}
# Başarılı yanıt (200):
# {
#   "data": { "deleted": true, "id": 43 }
# }
# Hata: 403 forbidden → store scope başka mağazanın bannerını silmeye çalışıyor
#        404 not_found  → banner yok
print(f"\n=== BANNER SİL (ID={banner_id}) ===")
r = requests.delete(f"{BASE}/banners/{banner_id}", headers=headers)
print(f"Status: {r.status_code}")
print(f"Body: {r.text}")


# --- 5. Silindi mi doğrula ---
# 404 not_found gelmesi beklenen doğru davranış
# { "error": { "code": "not_found", "message": "Banner bulunamadi" } }
print(f"\n=== SİLİNDİ Mİ? (ID={banner_id}) ===")
r = requests.get(f"{BASE}/banners/{banner_id}", headers=headers)
print(f"Status: {r.status_code}")
print(f"Body: {r.text[:200]}")