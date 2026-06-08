import requests
import time
import json

# ================== AYARLAR ==================
API_TOKEN = "1d7dd489-d4ef-4d43-a0ed-15821caf8727"   # ← Burayı değiştir!
COLLECTOR_ID = "c_mq47f3vuhl9g6ufks"                    # Senin scraper ID'n

# Çekmek istediğin Trendyol ürünü
PRODUCT_URL = "https://www.trendyol.com/attack-shark/x11-superlight-22000-dpi-paw3311-sarj-istasyonuna-sahip-kablosuz-oyuncu-faresi-p-875000068"

HEADERS = {
    "Authorization": f"Bearer {API_TOKEN}",
    "Content-Type": "application/json"
}

# ================== SCRAPE BAŞLAT ==================
def trigger_scrape():
    payload = [{"url": PRODUCT_URL}]
    
    response = requests.post(
        f"https://api.brightdata.com/dca/trigger?collector={COLLECTOR_ID}&queue_next=1",
        headers=HEADERS,
        json=payload
    )
    
    if response.status_code == 200:
        data = response.json()
        print("✅ Scrape başlatıldı!")
        return data.get("response_id")
    else:
        print("❌ Hata:", response.status_code, response.text)
        return None

# ================== SONUCU AL (Polling) ==================
def get_result(response_id, max_attempts=30):
    url = f"https://api.brightdata.com/dca/get_result?response_id={response_id}"
    
    for attempt in range(max_attempts):
        response = requests.get(url, headers=HEADERS)
        
        if response.status_code == 200:
            result = response.json()
            
            if result.get("status") == "ready":
                print(f"✅ Başarılı! ({attempt+1}. deneme)")
                return result.get("result")
            elif result.get("status") == "pending":
                print(f"⏳ Hâlâ işleniyor... ({attempt+1}/{max_attempts})")
            else:
                print("Durum:", result.get("status"))
        else:
            print("Hata:", response.status_code)
        
        time.sleep(3)  # 3 saniye bekle
    
    print("⏰ Zaman aşımı!")
    return None

# ================== ANA ÇALIŞMA ==================
if __name__ == "__main__":
    response_id = trigger_scrape()
    
    if response_id:
        data = get_result(response_id)
        
        if data:
            # Sonucu güzel şekilde yazdır
            print("\n" + "="*50)
            print(json.dumps(data, indent=2, ensure_ascii=False))
            
            # İstersen dosyaya da kaydet
            with open("trendyol_urun.json", "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            print("\n📁 Sonuç 'trendyol_urun.json' dosyasına kaydedildi.")