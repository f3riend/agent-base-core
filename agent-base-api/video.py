import fal_client
import requests
import os
import time
from dotenv import load_dotenv

# 🔐 .env yükle
load_dotenv()

FAL_KEY = os.getenv("FAL_KEY")
if not FAL_KEY:
    raise Exception("❌ FAL_KEY bulunamadı (.env kontrol et)")

os.environ["FAL_KEY"] = FAL_KEY

# ⏱️ zaman başlat
START_TIME = time.time()

def log_status(status_text):
    elapsed = time.time() - START_TIME

    # basit tahmin (ortalama süre)
    estimated_total = 60
    remaining = max(0, estimated_total - elapsed)

    print(f"[{status_text}] ⏱️ {elapsed:.1f}s | kalan ~{remaining:.1f}s")


print("🚀 Video oluşturma başladı...")
log_status("START")

# 🎬 async job başlat — Kling 3.0 Pro (fal.ai)
handler = fal_client.submit(
    "fal-ai/kling-video/v3/pro/text-to-video",
    arguments={
        "prompt": "a futuristic cyberpunk city at night, neon lights, cinematic, 4k, ultra realistic",
        "duration": "5",
    },
)

# 🔁 durum kontrol
while True:
    try:
        status = handler.status()
        status_name = status.__class__.__name__

        if status_name == "InProgress":
            log_status("GENERATING")
            time.sleep(5)

        elif status_name == "InQueue":
            log_status("QUEUE")
            time.sleep(5)

        elif status_name == "Completed":
            log_status("DONE ✅")
            break

        else:
            print("❌ Beklenmeyen durum:", status)
            exit()

    except Exception as e:
        print("⚠️ Status hatası:", e)
        time.sleep(5)


# 📦 sonucu al
try:
    result = handler.get()
except Exception as e:
    print("❌ Sonuç alınamadı:", e)
    exit()

# 🎬 video url
video_url = result["video"]["url"]
print("🎬 Video URL:", video_url)

# 💾 indir
try:
    response = requests.get(video_url)
    response.raise_for_status()
except Exception as e:
    print("❌ Video indirilemedi:", e)
    exit()

# 📁 dosya adı
filename = f"video_{int(time.time())}.mp4"

with open(filename, "wb") as f:
    f.write(response.content)

# 🏁 süre
total_time = time.time() - START_TIME

print(f"✅ Kaydedildi: {filename}")
print(f"🏁 Toplam süre: {total_time:.1f} saniye")