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
from datetime import datetime, timezone

app = Flask(__name__)

logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(levelname)s: %(message)s')

CACHE_INTERVAL = 3.0  # seconds between background polls
_cached_stats = {}
_cache_lock = threading.Lock()

# Track previous disk/net counters for delta speed calculation
_prev_disk_io = None
_prev_net_io = None
_prev_io_time = None


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


def get_cpu_temp_wmi(c):
    """Try to get CPU temperature via WMI MSAcpi_ThermalZoneTemperature."""
    try:
        w = wmi.WMI(namespace="root\\wmi")
        temps = w.MSAcpi_ThermalZoneTemperature()
        if temps:
            # Convert from tenths of Kelvin to Celsius
            celsius_values = [round((t.CurrentTemperature / 10.0) - 273.15, 1) for t in temps]
            # Filter out unreasonable values
            valid = [v for v in celsius_values if 0 < v < 110]
            if valid:
                return max(valid)  # return hottest zone
    except Exception:
        pass
    return None


def collect_stats():
    """Collect system stats using WMI/psutil/GPUtil. Runs in background thread."""
    global _cached_stats, _prev_disk_io, _prev_net_io, _prev_io_time

    # Prime psutil cpu_percent (first call returns 0)
    psutil.cpu_percent(interval=None)
    psutil.cpu_percent(percpu=True, interval=None)
    time.sleep(1)

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

            # System uptime
            uptime_seconds = None
            try:
                boot_time = psutil.boot_time()
                uptime_seconds = int(time.time() - boot_time)
            except Exception:
                pass

            if os_info:
                os_data = {
                    'name': getattr(os_info, 'Caption', 'N/A'),
                    'build': getattr(os_info, 'BuildNumber', 'N/A'),
                    'display_version': display_version,
                    'arch': getattr(os_info, 'OSArchitecture', 'N/A'),
                    'node_name': getattr(os_info, 'CSName', platform.node()),
                    'install_date': getattr(os_info, 'InstallDate', 'N/A'),
                    'last_boot': getattr(os_info, 'LastBootUpTime', 'N/A'),
                    'uptime_seconds': uptime_seconds,
                    'registered_user': getattr(os_info, 'RegisteredUser', 'N/A'),
                    'total_visible_memory_mb': getattr(os_info, 'TotalVisibleMemorySize', None),
                }
            else:
                os_data = {
                    'name': platform.system(),
                    'build': platform.version(),
                    'display_version': display_version,
                    'arch': platform.machine(),
                    'node_name': platform.node(),
                    'install_date': 'N/A',
                    'last_boot': 'N/A',
                    'uptime_seconds': uptime_seconds,
                    'registered_user': 'N/A',
                    'total_visible_memory_mb': None,
                }

            stats['os'] = os_data

            # --- CPU ---
            try:
                p = c.Win32_Processor()[0]
                max_mhz = getattr(p, 'MaxClockSpeed', None)
                current_mhz = None
                try:
                    for perf in c.Win32_PerfFormattedData_Counters_ProcessorInformation():
                        if getattr(perf, 'Name', '') == '_Total':
                            pct_perf = getattr(perf, 'PercentProcessorPerformance', None)
                            if pct_perf is not None and max_mhz:
                                current_mhz = round(max_mhz * (float(pct_perf) / 100.0), 1)
                            break
                except Exception:
                    pass
                if current_mhz is None:
                    cpu_freq = psutil.cpu_freq()
                    current_mhz = round(cpu_freq.current, 1) if cpu_freq and cpu_freq.current else 'N/A'

                # Socket / Cache info
                l2_cache = getattr(p, 'L2CacheSize', None)
                l3_cache = getattr(p, 'L3CacheSize', None)

                cpu_data = {
                    'name': getattr(p, 'Name', 'N/A'),
                    'manufacturer': getattr(p, 'Manufacturer', 'N/A'),
                    'cores': getattr(p, 'NumberOfCores', 'N/A'),
                    'logical_processors': getattr(p, 'NumberOfLogicalProcessors', 'N/A'),
                    'max_clock_mhz': max_mhz if max_mhz else 'N/A',
                    'current_clock_mhz': current_mhz,
                    'socket': getattr(p, 'SocketDesignation', 'N/A'),
                    'architecture': getattr(p, 'Architecture', 'N/A'),
                    'l2_cache_kb': l2_cache,
                    'l3_cache_kb': l3_cache,
                    'virtualization': getattr(p, 'VirtualizationFirmwareEnabled', None),
                    'stepping': getattr(p, 'Stepping', 'N/A'),
                    'revision': getattr(p, 'Revision', 'N/A'),
                }
            except Exception:
                cpu_data = {
                    'name': platform.processor(),
                    'manufacturer': 'N/A',
                    'cores': psutil.cpu_count(logical=False),
                    'logical_processors': psutil.cpu_count(logical=True),
                    'max_clock_mhz': 'N/A',
                    'current_clock_mhz': psutil.cpu_freq().current if psutil.cpu_freq() else 'N/A',
                    'socket': 'N/A',
                    'architecture': 'N/A',
                    'l2_cache_kb': None,
                    'l3_cache_kb': None,
                    'virtualization': None,
                    'stepping': 'N/A',
                    'revision': 'N/A',
                }

            # CPU percent overall + per-core
            cpu_percent = psutil.cpu_percent(interval=None)
            per_core = psutil.cpu_percent(percpu=True, interval=None)
            cpu_data['percent'] = cpu_percent
            cpu_data['per_core_percent'] = per_core

            # CPU temperature
            cpu_temp = get_cpu_temp_wmi(c)
            cpu_data['temp_c'] = cpu_temp

            # CPU frequency per-core if available
            try:
                freq_all = psutil.cpu_freq(percpu=True)
                if freq_all:
                    cpu_data['per_core_freq_mhz'] = [round(f.current, 0) for f in freq_all]
            except Exception:
                pass

            stats['cpu'] = cpu_data

            # --- RAM (total + modules) ---
            svmem = psutil.virtual_memory()
            swap = psutil.swap_memory()
            ram_data = {
                'total_gb': round(svmem.total / (1024 ** 3), 2),
                'available_gb': round(svmem.available / (1024 ** 3), 2),
                'used_gb': round((svmem.total - svmem.available) / (1024 ** 3), 2),
                'cached_gb': round(getattr(svmem, 'cached', 0) / (1024 ** 3), 2),
                'percent': svmem.percent,
                'swap_total_gb': round(swap.total / (1024 ** 3), 2) if swap.total else 0,
                'swap_used_gb': round(swap.used / (1024 ** 3), 2) if swap.used else 0,
                'swap_percent': swap.percent if swap.total else 0,
            }

            # Physical memory modules
            try:
                modules = []
                mem_type_map = {
                    20: 'DDR', 21: 'DDR2', 22: 'DDR2 FB-DIMM', 24: 'DDR3',
                    26: 'DDR4', 34: 'DDR5', 0: 'Unknown', 1: 'Other'
                }
                for m in c.Win32_PhysicalMemory():
                    cap = int(m.Capacity) if m.Capacity else 0
                    mem_type_raw = getattr(m, 'SMBIOSMemoryType', 0) or 0
                    mem_type_str = mem_type_map.get(int(mem_type_raw), f'Type {mem_type_raw}')
                    form_factor_map = {8: 'DIMM', 12: 'SO-DIMM', 13: 'TSOP', 9: 'SIMM'}
                    form_factor_raw = getattr(m, 'FormFactor', 0) or 0
                    form_factor_str = form_factor_map.get(int(form_factor_raw), f'{form_factor_raw}')
                    modules.append({
                        'bank': getattr(m, 'BankLabel', 'N/A'),
                        'slot': getattr(m, 'DeviceLocator', 'N/A'),
                        'manufacturer': getattr(m, 'Manufacturer', 'N/A'),
                        'part_number': (getattr(m, 'PartNumber', '') or '').strip(),
                        'serial': (getattr(m, 'SerialNumber', '') or '').strip(),
                        'capacity_gb': round(cap / (1024 ** 3), 2) if cap else 0,
                        'speed_mhz': getattr(m, 'Speed', 'N/A'),
                        'configured_speed_mhz': getattr(m, 'ConfiguredClockSpeed', 'N/A'),
                        'memory_type': mem_type_str,
                        'form_factor': form_factor_str,
                        'voltage': getattr(m, 'ConfiguredVoltage', None),
                    })
                ram_data['modules'] = modules
                # Count channels
                ram_data['num_modules'] = len(modules)
                if modules:
                    speeds = [m['speed_mhz'] for m in modules if m['speed_mhz'] not in ('N/A', None)]
                    if speeds:
                        ram_data['max_speed_mhz'] = max(speeds)
            except Exception:
                ram_data['modules'] = []

            stats['ram'] = ram_data

            # --- Battery ---
            battery_data = None
            try:
                ps_bat = psutil.sensors_battery()
                if ps_bat is not None:
                    status_text = 'Đang sạc' if ps_bat.power_plugged else 'Đang dùng pin'
                    battery_data = {
                        'name': 'Battery',
                        'status': status_text,
                        'design_mwh': 0,
                        'full_mwh': 0,
                        'wear_level_percent': None,
                        'charge_percent': int(ps_bat.percent) if ps_bat.percent is not None else None,
                        'power_plugged': ps_bat.power_plugged,
                        'secsleft': ps_bat.secsleft
                    }
            except Exception:
                pass

            if battery_data is None:
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
            else:
                try:
                    batteries = c.Win32_Battery()
                    if batteries:
                        b = batteries[0]
                        design_cap = int(getattr(b, 'DesignCapacity', 0)) or 0
                        full_cap = int(getattr(b, 'FullChargeCapacity', 0)) or 0
                        if design_cap and full_cap:
                            battery_data['design_mwh'] = design_cap
                            battery_data['full_mwh'] = full_cap
                            try:
                                battery_data['wear_level_percent'] = round((1 - (full_cap / design_cap)) * 100, 1)
                            except Exception:
                                pass
                except Exception:
                    pass

            stats['battery'] = battery_data

            # --- GPUs ---
            gpu_list = []
            nv_dict = {}
            try:
                for g in GPUtil.getGPUs():
                    nv_dict[g.name] = g
            except Exception:
                nv_dict = {}

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
                            util = None
                            mem_util = None
                            temp_nvml = None
                            power_draw = None
                            power_limit = None
                            fan_speed = None
                            clock_graphics = None
                            clock_mem = None
                            try:
                                util_rates = pynvml.nvmlDeviceGetUtilizationRates(handle)
                                util = util_rates.gpu if hasattr(util_rates, 'gpu') else None
                                mem_util = util_rates.memory if hasattr(util_rates, 'memory') else None
                            except Exception:
                                pass
                            try:
                                temp_nvml = pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU)
                            except Exception:
                                pass
                            try:
                                power_draw = round(pynvml.nvmlDeviceGetPowerUsage(handle) / 1000.0, 1)
                                power_limit = round(pynvml.nvmlDeviceGetPowerManagementLimit(handle) / 1000.0, 1)
                            except Exception:
                                pass
                            try:
                                fan_speed = pynvml.nvmlDeviceGetFanSpeed(handle)
                            except Exception:
                                pass
                            try:
                                clock_graphics = pynvml.nvmlDeviceGetClockInfo(handle, pynvml.NVML_CLOCK_GRAPHICS)
                                clock_mem = pynvml.nvmlDeviceGetClockInfo(handle, pynvml.NVML_CLOCK_MEM)
                            except Exception:
                                pass
                            nvml_map[name] = {
                                'total_mb': int(meminfo.total // (1024**2)),
                                'used_mb': int(meminfo.used // (1024**2)),
                                'free_mb': int(meminfo.free // (1024**2)),
                                'load_percent': util,
                                'mem_util_percent': mem_util,
                                'temp_c': temp_nvml,
                                'power_draw_w': power_draw,
                                'power_limit_w': power_limit,
                                'fan_speed_pct': fan_speed,
                                'clock_graphics_mhz': clock_graphics,
                                'clock_mem_mhz': clock_mem,
                            }
                        except Exception:
                            continue
                except Exception:
                    nvml_map = {}

            try:
                for i, g in enumerate(c.Win32_VideoController()):
                    name = getattr(g, 'Name', 'N/A')
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
                    mem_used_mb = None
                    mem_free_mb = None
                    mem_util_pct = None
                    power_draw = None
                    power_limit = None
                    fan_speed = None
                    clock_graphics = None
                    clock_mem = None

                    matched = None
                    for k, gp in nv_dict.items():
                        if k and (k in name or name in k):
                            matched = gp
                            break
                    if matched:
                        mem_total_mb = int(matched.memoryTotal)
                        if matched.load is not None and matched.load > 0:
                            load = round(matched.load * 100, 1)
                        temp = matched.temperature

                    for k, v in nvml_map.items():
                        if k and (k in name or name in k):
                            mem_nvml_mb = v.get('total_mb')
                            mem_used_mb = v.get('used_mb')
                            mem_free_mb = v.get('free_mb')
                            mem_util_pct = v.get('mem_util_percent')
                            nvml_load = v.get('load_percent')
                            if nvml_load is not None:
                                load = int(nvml_load)
                            if v.get('temp_c') is not None:
                                temp = v.get('temp_c')
                            power_draw = v.get('power_draw_w')
                            power_limit = v.get('power_limit_w')
                            fan_speed = v.get('fan_speed_pct')
                            clock_graphics = v.get('clock_graphics_mhz')
                            clock_mem = v.get('clock_mem_mhz')
                            break

                    gpu_list.append({
                        'index': i,
                        'name': name,
                        'vram_reported_mb': vram_mb,
                        'vram_gputil_mb': mem_total_mb,
                        'vram_nvml_mb': mem_nvml_mb,
                        'vram_used_mb': mem_used_mb,
                        'vram_free_mb': mem_free_mb,
                        'vram_util_percent': mem_util_pct,
                        'driver': getattr(g, 'DriverVersion', 'N/A'),
                        'driver_date': getattr(g, 'DriverDate', 'N/A'),
                        'resolution': f"{getattr(g, 'CurrentHorizontalResolution', 'N/A')}x{getattr(g, 'CurrentVerticalResolution', 'N/A')} @ {getattr(g, 'CurrentRefreshRate', 'N/A')}Hz",
                        'load_percent': load,
                        'temp_c': temp,
                        'power_draw_w': power_draw,
                        'power_limit_w': power_limit,
                        'fan_speed_pct': fan_speed,
                        'clock_graphics_mhz': clock_graphics,
                        'clock_mem_mhz': clock_mem,
                        'video_processor': getattr(g, 'VideoProcessor', 'N/A'),
                        'adapter_dac_type': getattr(g, 'AdapterDACType', 'N/A'),
                        'status': getattr(g, 'Status', 'N/A'),
                    })
            except Exception:
                gpu_list = []

            stats['gpus'] = gpu_list

            # --- Disks ---
            try:
                disk_list = []
                for d in c.Win32_DiskDrive():
                    size_bytes = int(getattr(d, 'Size', 0) or 0)
                    size_gb = round(size_bytes / (1024 ** 3), 2) if size_bytes else 0
                    serial = getattr(d, 'SerialNumber', None)
                    disk_list.append({
                        'model': getattr(d, 'Model', 'N/A'),
                        'interface': getattr(d, 'InterfaceType', 'N/A'),
                        'media_type': getattr(d, 'MediaType', 'N/A'),
                        'size_gb': size_gb,
                        'firmware': getattr(d, 'FirmwareRevision', 'N/A'),
                        'serial': serial.strip() if serial else 'N/A',
                        'partitions': getattr(d, 'Partitions', 'N/A'),
                        'bytes_per_sector': getattr(d, 'BytesPerSector', 'N/A'),
                    })

                logicals = []
                for ld in c.Win32_LogicalDisk():
                    if ld.Size:
                        total = int(ld.Size)
                        free = int(ld.FreeSpace) if ld.FreeSpace else 0
                        used = total - free
                        logicals.append({
                            'device_id': getattr(ld, 'DeviceID', 'N/A'),
                            'total_gb': round(total / (1024 ** 3), 2),
                            'free_gb': round(free / (1024 ** 3), 2),
                            'used_gb': round(used / (1024 ** 3), 2),
                            'filesystem': getattr(ld, 'FileSystem', 'N/A'),
                            'volume_name': getattr(ld, 'VolumeName', '') or '',
                            'drive_type': getattr(ld, 'DriveType', 'N/A'),
                        })

                # Disk I/O speed via psutil delta
                now = time.time()
                curr_disk = psutil.disk_io_counters()
                disk_io = {'read_mb_s': None, 'write_mb_s': None}
                if _prev_disk_io is not None and _prev_io_time is not None and curr_disk:
                    dt = now - _prev_io_time
                    if dt > 0:
                        disk_io['read_mb_s'] = round((curr_disk.read_bytes - _prev_disk_io.read_bytes) / dt / (1024**2), 2)
                        disk_io['write_mb_s'] = round((curr_disk.write_bytes - _prev_disk_io.write_bytes) / dt / (1024**2), 2)
                        disk_io['read_count'] = curr_disk.read_count
                        disk_io['write_count'] = curr_disk.write_count
                _prev_disk_io = curr_disk

                stats['disks'] = {'physical': disk_list, 'logical': logicals, 'io': disk_io}
            except Exception:
                stats['disks'] = {'physical': [], 'logical': [], 'io': {}}

            # --- Network adapters + I/O speed ---
            net_list = []
            try:
                for nic in c.Win32_NetworkAdapterConfiguration(IPEnabled=True):
                    net_list.append({
                        'description': getattr(nic, 'Description', 'N/A'),
                        'mac': getattr(nic, 'MACAddress', 'N/A'),
                        'ips': list(nic.IPAddress) if getattr(nic, 'IPAddress', None) else [],
                        'subnet': list(nic.IPSubnet) if getattr(nic, 'IPSubnet', None) else [],
                        'gateway': list(nic.DefaultIPGateway) if getattr(nic, 'DefaultIPGateway', None) else [],
                        'dns': list(nic.DNSServerSearchOrder) if getattr(nic, 'DNSServerSearchOrder', None) else [],
                        'dhcp': getattr(nic, 'DHCPEnabled', 'N/A'),
                        'dhcp_server': getattr(nic, 'DHCPServer', 'N/A'),
                        'mtu': getattr(nic, 'MTU', 'N/A'),
                    })
            except Exception:
                net_list = []

            # Network I/O speed
            now = time.time()
            curr_net = psutil.net_io_counters()
            net_io = {'sent_mb_s': None, 'recv_mb_s': None}
            if _prev_net_io is not None and _prev_io_time is not None and curr_net:
                dt = now - _prev_io_time
                if dt > 0:
                    net_io['sent_mb_s'] = round((curr_net.bytes_sent - _prev_net_io.bytes_sent) / dt / (1024**2), 3)
                    net_io['recv_mb_s'] = round((curr_net.bytes_recv - _prev_net_io.bytes_recv) / dt / (1024**2), 3)
                    net_io['total_sent_gb'] = round(curr_net.bytes_sent / (1024**3), 3)
                    net_io['total_recv_gb'] = round(curr_net.bytes_recv / (1024**3), 3)
                    net_io['packets_sent'] = curr_net.packets_sent
                    net_io['packets_recv'] = curr_net.packets_recv
                    net_io['errors_in'] = curr_net.errin
                    net_io['errors_out'] = curr_net.errout
            _prev_net_io = curr_net
            _prev_io_time = now

            stats['network'] = net_list
            stats['network_io'] = net_io

            # --- Top Processes ---
            try:
                procs = []
                for proc in psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_info', 'status', 'username', 'create_time']):
                    try:
                        info = proc.info
                        mem_mb = round(info['memory_info'].rss / (1024**2), 1) if info.get('memory_info') else 0
                        procs.append({
                            'pid': info['pid'],
                            'name': info['name'] or 'N/A',
                            'cpu_percent': round(info.get('cpu_percent') or 0, 1),
                            'mem_mb': mem_mb,
                            'status': info.get('status', 'N/A'),
                            'username': (info.get('username') or '').split('\\')[-1],
                        })
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        continue

                top_cpu = sorted(procs, key=lambda x: x['cpu_percent'], reverse=True)[:10]
                top_mem = sorted(procs, key=lambda x: x['mem_mb'], reverse=True)[:10]
                stats['processes'] = {
                    'total': len(procs),
                    'top_cpu': top_cpu,
                    'top_mem': top_mem,
                }
            except Exception:
                stats['processes'] = {'total': 0, 'top_cpu': [], 'top_mem': []}

            stats['timestamp'] = datetime.now(timezone.utc).isoformat()
            stats['cpu_percent'] = cpu_percent
            stats['ram_summary'] = ram_data

            with _cache_lock:
                _cached_stats = stats

        except Exception:
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
    with _cache_lock:
        if _cached_stats:
            return jsonify(_cached_stats)
        else:
            return jsonify({'error': 'no data yet'}), 503


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)