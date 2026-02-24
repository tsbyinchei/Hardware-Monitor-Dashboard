from flask import Flask, render_template, jsonify
import psutil
import wmi
import pythoncom
import socket
import winreg
import GPUtil
import platform

app = Flask(__name__)

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return "127.0.0.1"

SERVER_IP = get_local_ip()

@app.route('/')
def index():
    return render_template('index.html', server_ip=SERVER_IP)

@app.route('/api/detailed_stats')
def detailed_stats():
    pythoncom.CoInitialize()
    c = wmi.WMI()

    # --- 1. HỆ ĐIỀU HÀNH & REGISTRY (Lấy 24H2, 23H2) ---
    os_info = c.Win32_OperatingSystem()[0]
    display_version = "N/A"
    try:
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion")
        display_version = winreg.QueryValueEx(key, "DisplayVersion")[0]
        winreg.CloseKey(key)
    except:
        pass

    os_data = {
        'name': os_info.Caption,
        'build': os_info.BuildNumber,
        'display_version': display_version,
        'arch': os_info.OSArchitecture
    }

    # --- 2. MAINBOARD & BIOS ---
    bios = c.Win32_BIOS()[0]
    board = c.Win32_BaseBoard()[0]
    system_enc = c.Win32_ComputerSystem()[0]
    hw_data = {
        'system_family': system_enc.SystemFamily if hasattr(system_enc, 'SystemFamily') else "N/A",
        'manufacturer': system_enc.Manufacturer,
        'model': system_enc.Model,
        'service_tag': bios.SerialNumber,
        'bios_version': bios.SMBIOSBIOSVersion,
        'board_maker': board.Manufacturer,
        'board_model': board.Product,
        'node_name': os_info.CSName
    }

    # --- 3. PIN (BATTERY) ---
    battery_data = None
    try:
        batteries = c.Win32_Battery()
        if batteries:
            b = batteries[0]
            design_cap = int(b.DesignCapacity) if b.DesignCapacity else 0
            full_cap = int(b.FullChargeCapacity) if b.FullChargeCapacity else 0
            wear_level = round((1 - (full_cap / design_cap)) * 100, 1) if design_cap > 0 else 0
            
            battery_data = {
                'name': b.Name,
                'status': b.BatteryStatus, # 2 = Cắm sạc, 1 = Đang xả
                'design_mwh': design_cap,
                'full_mwh': full_cap,
                'wear_level': wear_level,
                'charge_percent': b.EstimatedChargeRemaining
            }
    except:
        pass

    # --- 4. ĐA GPU & FIX LỖI VRAM ---
    gpu_list = []
    # Lấy thông số NVIDIA chuẩn qua GPUtil
    nv_dict = {}
    try:
        for g in GPUtil.getGPUs():
            nv_dict[g.name] = g
    except: pass

    for i, g in enumerate(c.Win32_VideoController()):
        name = g.Name
        # Fix lỗi tràn 32-bit của WMI khiến VRAM báo âm hoặc sai
        try:
            vram_bytes = int(g.AdapterRAM)
            if vram_bytes < 0:
                vram_bytes += 2**32 # Bù bit
            vram_mb = vram_bytes // (1024**2)
        except:
            vram_mb = 0
            
        load = 0
        temp = "N/A"

        # Nếu là card rời NVIDIA, ưu tiên lấy từ GPUtil cho chuẩn xác
        if name in nv_dict:
            vram_mb = int(nv_dict[name].memoryTotal)
            load = round(nv_dict[name].load * 100, 1)
            temp = nv_dict[name].temperature

        gpu_list.append({
            'index': i,
            'name': name,
            'vram': f"{vram_mb} MB" if vram_mb > 100 else "Shared",
            'driver': g.DriverVersion,
            'resolution': f"{g.CurrentHorizontalResolution}x{g.CurrentVerticalResolution} @ {g.CurrentRefreshRate}Hz" if g.CurrentHorizontalResolution else "N/A",
            'load': load,
            'temp': temp
        })

    # --- 5. Ổ CỨNG (Chi tiết) ---
    disk_list = []
    for d in c.Win32_DiskDrive():
        size_gb = round(int(d.Size) / (1024**3), 2) if d.Size else 0
        disk_list.append({
            'model': d.Model,
            'interface': d.InterfaceType,
            'size': size_gb,
            'firmware': d.FirmwareRevision,
            'serial': d.SerialNumber.strip() if d.SerialNumber else "N/A"
        })

    # --- Đóng gói các thông số cơ bản (CPU, RAM, Mạng giữ nguyên logic cũ) ---
    cpu_percent = psutil.cpu_percent(interval=0.1)
    svmem = psutil.virtual_memory()
    ram_data = {
        'total': round(svmem.total / (1024 ** 3), 2),
        'used': round(svmem.used / (1024 ** 3), 2),
        'percent': svmem.percent
    }

    return jsonify({
        'os': os_data,
        'hw': hw_data,
        'battery': battery_data,
        'gpus': gpu_list,
        'disks': disk_list,
        'cpu_percent': cpu_percent,
        'ram': ram_data
    })

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)