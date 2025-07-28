# api_server.py (GELİŞTİRİLMİŞ VERSİYON)
# Timeout sorunları çözüldü, daha detaylı bilgiler eklendi

from fastapi import FastAPI, HTTPException, Body, BackgroundTasks
import subprocess
import json
import uvicorn
import time
import asyncio
from typing import Dict
import threading
from concurrent.futures import ThreadPoolExecutor

app = FastAPI(
    title="Multipass VM Yönetim API'si",
    description="Multipass sanal makinelerini yönetmek için kullanılan yerel API.",
    version="2.0.0"
)

# VM oluşturma durumlarını takip etmek için
vm_creation_status = {}
executor = ThreadPoolExecutor(max_workers=3)

def run_multipass_command(command: list, timeout=300):
    """Verilen multipass komutunu çalıştırır ve çıktıyı döner."""
    try:
        print(f"Çalıştırılan Komut: {' '.join(command)}")
        result = subprocess.run(
            command, 
            capture_output=True, 
            text=True, 
            timeout=timeout
        )
        
        if result.returncode != 0:
            error_msg = result.stderr.strip() if result.stderr else "Bilinmeyen hata"
            print(f"Komut hatası: {error_msg}")
            raise HTTPException(
                status_code=400, 
                detail=f"Multipass komutu başarısız oldu: {error_msg}"
            )
        
        print(f"Komut başarılı: {result.stdout}")
        return result.stdout
        
    except subprocess.TimeoutExpired:
        raise HTTPException(
            status_code=408, 
            detail=f"Komut zaman aşımına uğradı ({timeout} saniye)"
        )
    except FileNotFoundError:
        raise HTTPException(
            status_code=500, 
            detail="Multipass komutu bulunamadı. Multipass'ın yüklü ve PATH'de olduğundan emin olun."
        )

def create_vm_background(vm_name: str, config: Dict):
    """Arka planda VM oluşturur"""
    try:
        vm_creation_status[vm_name] = {"status": "creating", "message": "VM oluşturuluyor..."}
        
        # Güvenli karakter kontrolü
        if not vm_name.replace('-', '').replace('_', '').isalnum():
            vm_creation_status[vm_name] = {"status": "error", "message": "VM adı sadece harf, rakam, tire ve alt çizgi içerebilir."}
            return

        # Multipass için geçerli parametreler
        allowed_params = {
            "mem": "--memory",
            "disk": "--disk", 
            "cpus": "--cpus"
        }

        # Temel komutu oluştur
        command = ["multipass", "launch", "--name", vm_name]

        # Parametreleri ekle
        for key, value in config.items():
            if key in allowed_params and value:
                command.extend([allowed_params[key], str(value)])
        
        # VM oluşturma komutunu çalıştır (uzun timeout)
        result = run_multipass_command(command, timeout=600)  # 10 dakika
        
        # VM'in oluştuğunu doğrula
        time.sleep(3)
        try:
            info_result = run_multipass_command(["multipass", "info", vm_name, "--format", "json"])
            vm_info = json.loads(info_result)
            vm_creation_status[vm_name] = {
                "status": "completed", 
                "message": f"'{vm_name}' başarıyla oluşturuldu!",
                "vm_info": vm_info
            }
        except:
            vm_creation_status[vm_name] = {
                "status": "completed", 
                "message": f"'{vm_name}' oluşturuldu ancak detaylar alınamadı."
            }
            
    except Exception as e:
        vm_creation_status[vm_name] = {
            "status": "error", 
            "message": f"VM oluşturma hatası: {str(e)}"
        }

@app.post("/vms/create", summary="Yeni VM Oluştur (Asenkron)")
def create_vm(config: Dict = Body(...)):
    """
    JSON formatında gönderilen konfigürasyona göre yeni bir sanal makine oluşturur.
    Oluşturma işlemi arka planda çalışır, durumu /vms/status/{vm_name} ile kontrol edilir.
    """
    vm_name = config.get("name")
    if not vm_name:
        raise HTTPException(status_code=400, detail="Geçerli bir VM 'name' parametresi zorunludur.")

    # Arka planda VM oluşturmaya başla
    executor.submit(create_vm_background, vm_name, config)
    
    return {
        "status": "started", 
        "message": f"'{vm_name}' oluşturma işlemi başlatıldı. Durumu kontrol etmek için GET /vms/status/{vm_name} kullanın.",
        "vm_name": vm_name
    }

@app.get("/vms/status/{vm_name}", summary="VM Oluşturma Durumunu Kontrol Et")
def get_vm_creation_status(vm_name: str):
    """VM oluşturma durumunu kontrol eder"""
    if vm_name not in vm_creation_status:
        return {"status": "unknown", "message": f"'{vm_name}' için oluşturma işlemi bulunamadı."}
    
    return vm_creation_status[vm_name]

@app.get("/vms/list", summary="Tüm VM'leri Detaylı Listele")
def list_vms():
    """Tüm multipass sanal makinelerini detaylı bilgileriyle birlikte JSON formatında listeler."""
    try:
        # Önce basit liste al
        result_json = run_multipass_command(["multipass", "list", "--format", "json"])
        vm_list_data = json.loads(result_json)
        
        # Her VM için detaylı bilgi al
        detailed_vms = []
        for vm in vm_list_data.get("list", []):
            vm_name = vm.get("name")
            try:
                # Detaylı bilgi al
                info_result = run_multipass_command(["multipass", "info", vm_name, "--format", "json"], timeout=30)
                vm_info = json.loads(info_result)
                
                # Sadece ilgili VM'in bilgisini al
                if vm_name in vm_info.get("info", {}):
                    vm_details = vm_info["info"][vm_name]
                    
                    # Temel bilgileri çıkar
                    detailed_vm = {
                        "name": vm_name,
                        "state": vm_details.get("state", "unknown"),
                        "ipv4": vm_details.get("ipv4", []),
                        "memory": vm_details.get("memory", {}).get("total", "N/A"),
                        "disk": vm_details.get("disks", {}).get("sda1", {}).get("total", "N/A"), 
                        "cpus": vm_details.get("cpus", "N/A"),
                        "image": vm_details.get("image_hash", "N/A"),
                        "release": vm_details.get("release", "N/A")
                    }
                    detailed_vms.append(detailed_vm)
                else:
                    # Temel bilgiyi kullan
                    detailed_vms.append(vm)
                    
            except Exception as e:
                print(f"VM {vm_name} detayları alınamadı: {e}")
                # Temel bilgiyi kullan
                detailed_vms.append(vm)
        
        return {"list": detailed_vms, "total": len(detailed_vms)}
        
    except json.JSONDecodeError:
        # JSON parse edilemezse basit format dene
        result_text = run_multipass_command(["multipass", "list"])
        return {"raw_output": result_text, "error": "JSON formatı parse edilemedi"}

@app.get("/vms/info/{vm_name}", summary="VM Detaylarını Al")
def get_vm_info(vm_name: str):
    """Belirtilen sanal makinenin detaylı bilgisini JSON formatında döndürür."""
    if not vm_name.replace('-', '').replace('_', '').isalnum():
         raise HTTPException(status_code=400, detail="Geçersiz VM adı.")
    
    result_json = run_multipass_command(["multipass", "info", vm_name, "--format", "json"])
    return json.loads(result_json)

@app.post("/vms/start/{vm_name}", summary="VM'i Başlat")
def start_vm(vm_name: str):
    """Belirtilen sanal makineyi başlatır."""
    if not vm_name.replace('-', '').replace('_', '').isalnum():
         raise HTTPException(status_code=400, detail="Geçersiz VM adı.")
    
    run_multipass_command(["multipass", "start", vm_name])
    return {"status": "başlatıldı", "message": f"'{vm_name}' başlatıldı."}

@app.post("/vms/stop/{vm_name}", summary="VM'i Durdur")
def stop_vm(vm_name: str):
    """Belirtilen sanal makineyi durdurur."""
    if not vm_name.replace('-', '').replace('_', '').isalnum():
         raise HTTPException(status_code=400, detail="Geçersiz VM adı.")
    
    run_multipass_command(["multipass", "stop", vm_name])
    return {"status": "durduruldu", "message": f"'{vm_name}' durduruldu."}

@app.delete("/vms/delete/{vm_name}", summary="VM'i Sil")
def delete_vm(vm_name: str, purge: bool = False):
    """Belirtilen sanal makineyi siler. 'purge=true' ile kalıcı olarak siler."""
    if not vm_name.replace('-', '').replace('_', '').isalnum():
         raise HTTPException(status_code=400, detail="Geçersiz VM adı.")
         
    run_multipass_command(["multipass", "delete", vm_name])
    
    if purge:
        run_multipass_command(["multipass", "purge"])
        return {"status": "silindi_ve_temizlendi", "message": f"'{vm_name}' kalıcı olarak silindi."}
    
    return {"status": "silindi", "message": f"'{vm_name}' silindi. Geri getirmek için 'recover' komutunu kullanabilirsiniz."}

@app.post("/vms/recover/{vm_name}", summary="Silinen VM'i Geri Getir")
def recover_vm(vm_name: str):
    """Silinmiş ancak purge edilmemiş VM'i geri getirir."""
    if not vm_name.replace('-', '').replace('_', '').isalnum():
         raise HTTPException(status_code=400, detail="Geçersiz VM adı.")
    
    run_multipass_command(["multipass", "recover", vm_name])
    return {"status": "geri_getirildi", "message": f"'{vm_name}' başarıyla geri getirildi."}

@app.get("/health", summary="API Durumu")
def health_check():
    """API'nin çalışıp çalışmadığını kontrol eder."""
    try:
        # Multipass'ın çalışıp çalışmadığını kontrol et
        version_output = run_multipass_command(["multipass", "version"])
        return {"status": "healthy", "multipass": "available", "version": version_output.strip()}
    except:
        return {"status": "unhealthy", "multipass": "unavailable"}

if __name__ == "__main__":
    print("API sunucusunu başlatmak için terminalde şu komutu çalıştırın:")
    print("uvicorn api_server:app --reload --host 0.0.0.0 --port 8000")