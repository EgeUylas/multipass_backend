# 🚀 Multipass VM Yönetim Frontend

ChatGPT benzeri arayüzle Multipass sanal makinelerinizi yönetin! Bu frontend, OpenWebUI'dan aldığı Mistral model kod üretim yetenekleri ile çalışır.

## 🎯 Özellikler

- **ChatGPT Benzeri Arayüz**: Doğal dil ile VM komutları verin
- **Gerçek Zamanlı Chat**: Streaming yanıtlar ile anlık etkileşim
- **VM Durum Paneli**: Yan panel'de VM'lerinizi görüntüleyin ve yönetin
- **Akıllı Komut İşleme**: Mistral model'i ile doğal dil komutlarını anlama
- **Modern UI**: Tailwind CSS ile tasarlanmış responsive arayüz

## 🏗️ Sistem Mimarisi

```
Frontend (Next.js) → proxy_agent.py → Ollama Mistral Model
                                  ↓
                               api_server.py → Multipass
```

## 📋 Gereksinimler

1. **Node.js 18+** ve npm
2. **Python Backend'ler**:
   - `api_server.py` (Port: 8000)
   - `proxy_agent.py` (Port: 11434)
3. **Ollama** + Mistral model
4. **Multipass** kurulu ve çalışır durumda

## 🚀 Kurulum ve Çalıştırma

### 1. Backend'leri Başlatın

```bash
# Terminal 1: API Server'ı başlat
cd ..
python api_server.py

# Terminal 2: Proxy Agent'ı başlat  
python proxy_agent.py
```

### 2. Frontend'i Başlatın

```bash
# Bu dizinde (multipass-frontend/)
npm install
npm run dev
```

Frontend: http://localhost:3000 adresinde çalışacak

## 🎮 Kullanım

### Temel Komutlar

- **"Merhaba"** → Asistan ile tanışın
- **"VM listele"** → Mevcut VM'leri görün
- **"Ubuntu VM oluştur"** → Yeni Ubuntu VM oluşturun
- **"2GB RAM ile VM oluştur"** → Özel konfigürasyonla VM oluşturun

### Örnek Konuşmalar

```
👤 Kullanıcı: "4GB RAM ve 20GB disk ile geliştirme VM'i oluştur"
🤖 Asistan: "Sanal makine gelistirme-vm oluşturuluyor..."
           "✅ Sanal makine gelistirme-vm başarıyla oluşturuldu!"

👤 Kullanıcı: "VM'leri listele"  
🤖 Asistan: "İşte mevcut sanal makineleriniz:
           - gelistirme-vm: running, IP: 192.168.64.2"
```

## 🎨 Arayüz Özellikleri

### Sol Panel
- **VM Listesi**: Tüm sanal makinelerinizi görün
- **Durum İkonları**: Running (🟢), Stopped (🔴), Starting (🟡)
- **VM Bilgileri**: IP, RAM, CPU, Disk bilgileri
- **Hızlı Aksiyonlar**: Başlat, Durdur, Sil butonları

### Chat Alanı  
- **Streaming Yanıtlar**: Gerçek zamanlı mesaj alımı
- **VM İşlem Durumu**: Oluşturma/güncelleme progress'i
- **Hızlı Komutlar**: Önceden tanımlı butonlar
- **Otomatik Scroll**: Yeni mesajlara otomatik kaydırma

## 🔧 Konfigürasyon

### Backend URL'leri
```javascript
// src/app/page.tsx içinde
const OLLAMA_URL = "http://localhost:11434"; // proxy_agent.py
const API_URL = "http://localhost:8000";     // api_server.py
```

### CORS Ayarları
`next.config.js` dosyasında API proxy ve CORS ayarları mevcuttur.

## 🚨 Sorun Giderme

### "API hatası" mesajı alıyorum
- Backend'lerin çalıştığından emin olun
- Port'ların müsait olduğunu kontrol edin (8000, 11434)

### VM işlemleri çalışmıyor
- Multipass'ın kurulu ve PATH'de olduğunu kontrol edin
- `multipass --version` komutu ile test edin

### Streaming yanıtlar gelmiyor
- proxy_agent.py'nin düzgün çalıştığından emin olun
- Browser console'da network hatalarını kontrol edin

## 📁 Proje Yapısı

```
multipass-frontend/
├── src/
│   ├── app/
│   │   └── page.tsx          # Ana chat sayfası
│   └── components/
│       ├── ChatMessage.tsx   # Mesaj bileşeni
│       ├── ChatInput.tsx     # Mesaj girişi
│       └── VMStatus.tsx      # VM durum paneli
├── next.config.js            # Next.js konfigürasyonu
└── package.json
```

## 🔮 Gelecek Özellikler

- [ ] VM log görüntüleyici
- [ ] Batch VM işlemleri
- [ ] VM template'leri
- [ ] SSH terminal entegrasyonu
- [ ] VM performans grafikleri

## 📝 Notlar

Bu frontend, Mistral model'inin multipass dokümantasyonu ile eğitilmiş kod üretim yeteneklerini kullanır. Model, doğal dil komutlarını multipass CLI komutlarına çevirir ve proxy_agent.py üzerinden işleme alır.

---

**Geliştirici**: VM Yönetim Asistanı ile daha verimli çalışın! 🎉
