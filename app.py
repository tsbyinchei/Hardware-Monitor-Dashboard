from flask import Flask, render_template, jsonify
import psutil
import wmi
import pythoncom
import socket
import winreg
import GPUtil
import platform
import threading
import time
import logging
from datetime import datetime

app = Flask(__name__)

logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(levelname)s: %(message)s')

CACHE_INTERVAL = 5.0  # seconds between background polls (reduced frequency)
_cached_stats = {}
_cache_lock = threading.Lock()


def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

SERVER_IP = get_local_ip()

# Try initialize NVML (nvidia-ml) to get more accurate GPU info if available
PYNVML_AVAILABLE = False
try:
    import pynvml
    try:
        pynvml.nvmlInit()
        PYNVML_AVAILABLE = True
    except Exception:
        PYNVML_AVAILABLE = False
except Exception:
    PYNVML_AVAILABLE = False


def collect_stats():
    """Collect system stats using WMI/psutil/GPUtil. This runs in a background thread and updates _cached_stats."""
    global _cached_stats
    while True:
        stats = {}
        pythoncom.CoInitialize()
        try:
            c = wmi.WMI()
            # --- OS / Registry ---
            try:
                os_info = c.Win32_OperatingSystem()[0]
            except Exception:
                os_info = None

            display_version = "N/A"
            try:
                key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion")
                display_version = winreg.QueryValueEx(key, "DisplayVersion")[0]
                winreg.CloseKey(key)
            except Exception:
                pass

            if os_info:
                os_data = {
                    'name': getattr(os_info, 'Caption', 'N/A'),
                    'build': getattr(os_info, 'BuildNumber', 'N/A'),
                    'display_version': display_version,
                    'arch': getattr(os_info, 'OSArchitecture', 'N/A'),
                    'node_name': getattr(os_info, 'CSName', platform.node())
                }
            else:
                os_data = {'name': platform.system(), 'build': platform.version(), 'display_version': display_version, 'arch': platform.machine(), 'node_name': platform.node()}

            stats['os'] = os_data

            # --- CPU ---
            try:
                p = c.Win32_Processor()[0]
                cpu_freq = psutil.cpu_freq()
                cpu_data = {
                    'name': getattr(p, 'Name', 'N/A'),
                    'manufacturer': getattr(p, 'Manufacturer', 'N/A'),
                    'cores': getattr(p, 'NumberOfCores', 'N/A'),
                    'logical_processors': getattr(p, 'NumberOfLogicalProcessors', 'N/A'),
                    'max_clock_mhz': getattr(p, 'MaxClockSpeed', 'N/A'),
                    'current_clock_mhz': round(cpu_freq.current, 1) if cpu_freq else 'N/A'
                }
            except Exception:
                cpu_data = {
                    'name': platform.processor(),
                    'manufacturer': 'N/A',
                    'cores': psutil.cpu_count(logical=False),
                    'logical_processors': psutil.cpu_count(logical=True),
                    'max_clock_mhz': 'N/A',
                    'current_clock_mhz': psutil.cpu_freq().current if psutil.cpu_freq() else 'N/A'
                }

            # CPU percent (non-blocking): use psutil built-in (since background thread running periodically)
            cpu_percent = psutil.cpu_percent(interval=None)
            cpu_data['percent'] = cpu_percent
            stats['cpu'] = cpu_data

            # --- RAM (total + modules) ---
            svmem = psutil.virtual_memory()
            ram_data = {
                'total_gb': round(svmem.total / (1024 ** 3), 2),
                'available_gb': round(svmem.available / (1024 ** 3), 2),
                'used_gb': round((svmem.total - svmem.available) / (1024 ** 3), 2),
                'percent': svmem.percent
            }

            # Physical memory modules
            try:
                modules = []
                for m in c.Win32_PhysicalMemory():
                    cap = int(m.Capacity) if m.Capacity else 0
                    modules.append({
                        'bank': getattr(m, 'BankLabel', 'N/A'),
                        'manufacturer': getattr(m, 'Manufacturer', 'N/A'),
                        'part_number': getattr(m, 'PartNumber', 'N/A'),
                        'capacity_gb': round(cap / (1024 ** 3), 2) if cap else 0,
                        'speed_mhz': getattr(m, 'Speed', 'N/A'),
                        'memory_type': getattr(m, 'SMBIOSMemoryType', 'N/A')
                    })
                ram_data['modules'] = modules
            except Exception:
                ram_data['modules'] = []

            stats['ram'] = ram_data

            # --- Battery ---
            battery_data = None
            try:
                batteries = c.Win32_Battery()
                if batteries:
                    b = batteries[0]
                    design_cap = int(getattr(b, 'DesignCapacity', 0)) or 0
                    full_cap = int(getattr(b, 'FullChargeCapacity', 0)) or 0
                    est = getattr(b, 'EstimatedChargeRemaining', None)
                    wear_level = None
                    if design_cap and full_cap:
                        try:
                            wear_level = round((1 - (full_cap / design_cap)) * 100, 1)
                        except Exception:
                            wear_level = None

                    battery_data = {
                        'name': getattr(b, 'Name', 'Battery'),
                        'status': getattr(b, 'BatteryStatus', 'N/A'),
                        'design_mwh': design_cap,
                        'full_mwh': full_cap,
                        'wear_level_percent': wear_level,
                        'charge_percent': est
                    }
            except Exception:
                battery_data = None

            stats['battery'] = battery_data

            # --- GPUs ---
            gpu_list = []
            nv_dict = {}
            try:
                for g in GPUtil.getGPUs():
                    nv_dict[g.name] = g
            except Exception:
                nv_dict = {}

            # collect NVML info map if available
            nvml_map = {}
            if PYNVML_AVAILABLE:
                try:
                    nvml_count = pynvml.nvmlDeviceGetCount()
                    for ni in range(nvml_count):
                        try:
                            handle = pynvml.nvmlDeviceGetHandleByIndex(ni)
                            name_raw = pynvml.nvmlDeviceGetName(handle)
                            name = name_raw.decode() if isinstance(name_raw, bytes) else str(name_raw)
                            meminfo = pynvml.nvmlDeviceGetMemoryInfo(handle)
                            nvml_map[name] = {
                                'total_mb': int(meminfo.total // (1024**2)),
                                'used_mb': int(meminfo.used // (1024**2))
                            }
                        except Exception:
                            continue
                except Exception:
                    nvml_map = {}

            try:
                for i, g in enumerate(c.Win32_VideoController()):
                    name = getattr(g, 'Name', 'N/A')
                    # AdapterRAM may be None or an int (bytes)
                    try:
                        vram_bytes = int(getattr(g, 'AdapterRAM', 0) or 0)
                        if vram_bytes < 0:
                            vram_bytes += 2 ** 32
                        vram_mb = vram_bytes // (1024 ** 2)
                    except Exception:
                        vram_mb = 0

                    load = None
                    temp = None
                    mem_total_mb = None
                    mem_nvml_mb = None
                    # try GPUtil mapping by substring match if exact name not found
                    matched = None
                    for k, gp in nv_dict.items():
                        if k and k in name:
                            matched = gp
                            break
                    if matched:
                        mem_total_mb = int(matched.memoryTotal)
                        load = round(matched.load * 100, 1)
                        temp = matched.temperature

                    # try NVML map by substring
                    for k, v in nvml_map.items():
                        if k and k in name:
                            mem_nvml_mb = v.get('total_mb')
                            break

                    gpu_list.append({
                        'index': i,
                        'name': name,
                        'vram_reported_mb': vram_mb,
                        'vram_gputil_mb': mem_total_mb,
                        'vram_nvml_mb': mem_nvml_mb,
                        'driver': getattr(g, 'DriverVersion', 'N/A'),
                        'resolution': f"{getattr(g, 'CurrentHorizontalResolution', 'N/A')}x{getattr(g, 'CurrentVerticalResolution', 'N/A')} @ {getattr(g, 'CurrentRefreshRate', 'N/A')}Hz",
                        'load_percent': load,
                        'temp_c': temp,
                        'video_processor': getattr(g, 'VideoProcessor', 'N/A')
                    })
            except Exception:
                gpu_list = []

            stats['gpus'] = gpu_list

            # --- Disks ---
            disk_list = []
            try:
                for d in c.Win32_DiskDrive():
                    size_bytes = int(getattr(d, 'Size', 0) or 0)
                    size_gb = round(size_bytes / (1024 ** 3), 2) if size_bytes else 0
                    serial = getattr(d, 'SerialNumber', None)
                    disk_list.append({
                        'model': getattr(d, 'Model', 'N/A'),
                        'interface': getattr(d, 'InterfaceType', 'N/A'),
                        'size_gb': size_gb,
                        'firmware': getattr(d, 'FirmwareRevision', 'N/A'),
                        'serial': serial.strip() if serial else 'N/A'
                    })
                # also include logical drives and usage
                logicals = []
                for ld in c.Win32_LogicalDisk():
                    if ld.Size:
                        total = int(ld.Size)
                        free = int(ld.FreeSpace) if ld.FreeSpace else 0
                        logicals.append({
                            'device_id': getattr(ld, 'DeviceID', 'N/A'),
                            'total_gb': round(total / (1024 ** 3), 2),
                            'free_gb': round(free / (1024 ** 3), 2),
                            'filesystem': getattr(ld, 'FileSystem', 'N/A')
                        })
                stats['disks'] = {'physical': disk_list, 'logical': logicals}
            except Exception:
                stats['disks'] = {'physical': [], 'logical': []}

            # --- Network adapters ---
            net_list = []
            try:
                for nic in c.Win32_NetworkAdapterConfiguration(IPEnabled=True):
                    net_list.append({
                        'description': getattr(nic, 'Description', 'N/A'),
                        'mac': getattr(nic, 'MACAddress', 'N/A'),
                        'ips': list(nic.IPAddress) if getattr(nic, 'IPAddress', None) else [],
                        'gateway': list(nic.DefaultIPGateway) if getattr(nic, 'DefaultIPGateway', None) else [],
                        'dns': list(nic.DNSServerSearchOrder) if getattr(nic, 'DNSServerSearchOrder', None) else [],
                        'dhcp': getattr(nic, 'DHCPEnabled', 'N/A')
                    })
            except Exception:
                net_list = []
            stats['network'] = net_list

            stats['timestamp'] = datetime.utcnow().isoformat() + 'Z'

            # update non-wmi metrics collected via psutil
            stats['cpu_percent'] = cpu_percent
            stats['ram_summary'] = ram_data

            # lock and store
            with _cache_lock:
                _cached_stats = stats

        except Exception as ex:
            logging.exception('Error collecting stats:')
        finally:
            try:
                pythoncom.CoUninitialize()
            except Exception:
                pass

        time.sleep(CACHE_INTERVAL)


# start background thread
_collector_thread = threading.Thread(target=collect_stats, daemon=True)
_collector_thread.start()


@app.route('/')
def index():
    return render_template('index.html', server_ip=SERVER_IP)


@app.route('/api/detailed_stats')
def detailed_stats():
    # return the latest cached stats
    with _cache_lock:
        if _cached_stats:
            return jsonify(_cached_stats)
        else:
            return jsonify({'error': 'no data yet'}), 503


if __name__ == '__main__':
    # Debug should be disabled when running as a production service
    app.run(host='0.0.0.0', port=5000, debug=False)