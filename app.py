from flask import Flask, render_template, jsonify, Response, stream_with_context
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
import os
import json
import warnings
import re
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

app = Flask(__name__)

logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(levelname)s: %(message)s')

CACHE_INTERVAL = float(os.getenv('CACHE_INTERVAL', '3.0'))
GPU_SNAPSHOT_INTERVAL = float(os.getenv('GPU_SNAPSHOT_INTERVAL', '6.0'))
PROCESS_SNAPSHOT_INTERVAL = float(os.getenv('PROCESS_SNAPSHOT_INTERVAL', '15.0'))
ENABLE_PROCESSES = os.getenv('ENABLE_PROCESSES', '1').strip().lower() not in ('0', 'false', 'no')
HOST = os.getenv('HOST', '0.0.0.0')
PORT = int(os.getenv('PORT', '5000'))
_cached_stats = {}
_cache_lock = threading.Lock()
_last_collect_duration_ms = 0.0
_last_gpu_stats = []
_last_gpu_collect_ts = 0.0
_last_processes = {'total': 0, 'top_cpu': [], 'top_mem': []}
_last_process_collect_ts = 0.0

# Keep static hardware metadata to avoid expensive WMI calls every polling cycle.
_static_hw_info = {
    'os': {},
    'cpu': {},
    'ram': {},
    'disks': {'physical': []},
    'network': [],
    'gpus': [],
}

# Track simple anomaly counters across polling cycles.
_alert_state = {
    'cpu_temp_high_streak': 0,
    'ram_high_streak': 0,
}

# Track previous disk/net counters for delta speed calculation
_prev_disk_io = None
_prev_net_io = None
_prev_io_time = None
_com_state = threading.local()


def ensure_com_initialized():
    """Initialize COM once per thread before using WMI."""
    if not getattr(_com_state, 'initialized', False):
        pythoncom.CoInitialize()
        _com_state.initialized = True


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
    with warnings.catch_warnings():
        warnings.filterwarnings(
            'ignore',
            category=FutureWarning,
            message=r'The pynvml package is deprecated\\..*',
        )
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
        temps = c.MSAcpi_ThermalZoneTemperature()
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


def _safe_wmi_date(raw):
    return raw if raw else 'N/A'


def _get_display_version():
    display_version = 'N/A'
    try:
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion")
        display_version = winreg.QueryValueEx(key, 'DisplayVersion')[0]
        winreg.CloseKey(key)
    except Exception:
        pass
    return display_version


def init_static_hw_info():
    """Collect slow-changing hardware metadata once at startup."""
    static_info = {
        'os': {},
        'cpu': {},
        'ram': {'modules': []},
        'disks': {'physical': []},
        'network': [],
        'gpus': [],
    }

    ensure_com_initialized()
    try:
        c = wmi.WMI()

        # OS static
        try:
            os_info = c.Win32_OperatingSystem()[0]
            static_info['os'] = {
                'name': getattr(os_info, 'Caption', platform.system()),
                'build': getattr(os_info, 'BuildNumber', platform.version()),
                'display_version': _get_display_version(),
                'arch': getattr(os_info, 'OSArchitecture', platform.machine()),
                'node_name': getattr(os_info, 'CSName', platform.node()),
                'install_date': _safe_wmi_date(getattr(os_info, 'InstallDate', 'N/A')),
                'last_boot': _safe_wmi_date(getattr(os_info, 'LastBootUpTime', 'N/A')),
                'registered_user': getattr(os_info, 'RegisteredUser', 'N/A'),
                'total_visible_memory_mb': getattr(os_info, 'TotalVisibleMemorySize', None),
            }
        except Exception:
            static_info['os'] = {
                'name': platform.system(),
                'build': platform.version(),
                'display_version': _get_display_version(),
                'arch': platform.machine(),
                'node_name': platform.node(),
                'install_date': 'N/A',
                'last_boot': 'N/A',
                'registered_user': 'N/A',
                'total_visible_memory_mb': None,
            }

        # CPU static
        try:
            p = c.Win32_Processor()[0]
            static_info['cpu'] = {
                'name': getattr(p, 'Name', 'N/A'),
                'manufacturer': getattr(p, 'Manufacturer', 'N/A'),
                'cores': getattr(p, 'NumberOfCores', psutil.cpu_count(logical=False)),
                'logical_processors': getattr(p, 'NumberOfLogicalProcessors', psutil.cpu_count(logical=True)),
                'max_clock_mhz': getattr(p, 'MaxClockSpeed', 'N/A') or 'N/A',
                'socket': getattr(p, 'SocketDesignation', 'N/A'),
                'architecture': getattr(p, 'Architecture', 'N/A'),
                'l2_cache_kb': getattr(p, 'L2CacheSize', None),
                'l3_cache_kb': getattr(p, 'L3CacheSize', None),
                'virtualization': getattr(p, 'VirtualizationFirmwareEnabled', None),
                'stepping': getattr(p, 'Stepping', 'N/A'),
                'revision': getattr(p, 'Revision', 'N/A'),
            }
        except Exception:
            static_info['cpu'] = {
                'name': platform.processor(),
                'manufacturer': 'N/A',
                'cores': psutil.cpu_count(logical=False),
                'logical_processors': psutil.cpu_count(logical=True),
                'max_clock_mhz': 'N/A',
                'socket': 'N/A',
                'architecture': 'N/A',
                'l2_cache_kb': None,
                'l3_cache_kb': None,
                'virtualization': None,
                'stepping': 'N/A',
                'revision': 'N/A',
            }

        # RAM module static
        mem_type_map = {
            20: 'DDR', 21: 'DDR2', 22: 'DDR2 FB-DIMM', 24: 'DDR3',
            26: 'DDR4', 34: 'DDR5', 0: 'Unknown', 1: 'Other'
        }
        form_factor_map = {8: 'DIMM', 12: 'SO-DIMM', 13: 'TSOP', 9: 'SIMM'}
        modules = []
        try:
            for m in c.Win32_PhysicalMemory():
                cap = int(m.Capacity) if m.Capacity else 0
                mem_type_raw = getattr(m, 'SMBIOSMemoryType', 0) or 0
                form_factor_raw = getattr(m, 'FormFactor', 0) or 0
                modules.append({
                    'bank': getattr(m, 'BankLabel', 'N/A'),
                    'slot': getattr(m, 'DeviceLocator', 'N/A'),
                    'manufacturer': getattr(m, 'Manufacturer', 'N/A'),
                    'part_number': (getattr(m, 'PartNumber', '') or '').strip(),
                    'serial': (getattr(m, 'SerialNumber', '') or '').strip(),
                    'capacity_gb': round(cap / (1024 ** 3), 2) if cap else 0,
                    'speed_mhz': getattr(m, 'Speed', 'N/A'),
                    'configured_speed_mhz': getattr(m, 'ConfiguredClockSpeed', 'N/A'),
                    'memory_type': mem_type_map.get(int(mem_type_raw), f'Type {mem_type_raw}'),
                    'form_factor': form_factor_map.get(int(form_factor_raw), f'{form_factor_raw}'),
                    'voltage': getattr(m, 'ConfiguredVoltage', None),
                })
        except Exception:
            modules = []

        static_info['ram']['modules'] = modules
        static_info['ram']['num_modules'] = len(modules)
        speeds = [m['speed_mhz'] for m in modules if m['speed_mhz'] not in ('N/A', None)]
        if speeds:
            static_info['ram']['max_speed_mhz'] = max(speeds)

        # GPU and disk static
        try:
            gpu_static = []
            for i, g in enumerate(c.Win32_VideoController()):
                gpu_static.append({
                    'index': i,
                    'name': getattr(g, 'Name', 'N/A'),
                    'driver': getattr(g, 'DriverVersion', 'N/A'),
                    'driver_date': getattr(g, 'DriverDate', 'N/A'),
                    'resolution': f"{getattr(g, 'CurrentHorizontalResolution', 'N/A')}x{getattr(g, 'CurrentVerticalResolution', 'N/A')} @ {getattr(g, 'CurrentRefreshRate', 'N/A')}Hz",
                    'video_processor': getattr(g, 'VideoProcessor', 'N/A'),
                    'adapter_dac_type': getattr(g, 'AdapterDACType', 'N/A'),
                    'status': getattr(g, 'Status', 'N/A'),
                    'vram_reported_mb': max(0, int((getattr(g, 'AdapterRAM', 0) or 0) / (1024 ** 2))),
                })
            static_info['gpus'] = gpu_static
        except Exception:
            static_info['gpus'] = []

        try:
            disk_static = []
            for d in c.Win32_DiskDrive():
                size_bytes = int(getattr(d, 'Size', 0) or 0)
                disk_static.append({
                    'model': getattr(d, 'Model', 'N/A'),
                    'interface': getattr(d, 'InterfaceType', 'N/A'),
                    'media_type': getattr(d, 'MediaType', 'N/A'),
                    'size_gb': round(size_bytes / (1024 ** 3), 2) if size_bytes else 0,
                    'firmware': getattr(d, 'FirmwareRevision', 'N/A'),
                    'serial': (getattr(d, 'SerialNumber', '') or '').strip() or 'N/A',
                    'partitions': getattr(d, 'Partitions', 'N/A'),
                    'bytes_per_sector': getattr(d, 'BytesPerSector', 'N/A'),
                })
            static_info['disks']['physical'] = disk_static
        except Exception:
            static_info['disks']['physical'] = []

        # Network adapter static
        adapters = []
        try:
            for nic in c.Win32_NetworkAdapterConfiguration(IPEnabled=True):
                adapters.append({
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
            adapters = []
        static_info['network'] = adapters
    except Exception:
        logging.exception('Error collecting static hardware info:')

    return static_info


def _collect_os_dynamic(static_os):
    out = dict(static_os)
    try:
        out['uptime_seconds'] = int(time.time() - psutil.boot_time())
    except Exception:
        out['uptime_seconds'] = None
    return out


def _collect_cpu_dynamic(static_cpu):
    ensure_com_initialized()
    cpu_data = dict(static_cpu)
    cpu_percent = psutil.cpu_percent(interval=None)
    per_core = psutil.cpu_percent(percpu=True, interval=None)
    cpu_data['percent'] = cpu_percent
    cpu_data['per_core_percent'] = per_core

    # Prefer psutil current clock for dynamic frequency.
    try:
        cpu_freq = psutil.cpu_freq()
        cpu_data['current_clock_mhz'] = round(cpu_freq.current, 1) if cpu_freq and cpu_freq.current else 'N/A'
    except Exception:
        cpu_data['current_clock_mhz'] = 'N/A'

    try:
        freq_all = psutil.cpu_freq(percpu=True)
        if freq_all:
            cpu_data['per_core_freq_mhz'] = [round(f.current, 0) for f in freq_all]
    except Exception:
        pass

    try:
        c_temp = wmi.WMI(namespace="root\\wmi")
        cpu_data['temp_c'] = get_cpu_temp_wmi(c_temp)
    except Exception:
        cpu_data['temp_c'] = None

    return cpu_data, cpu_percent


def _collect_ram_dynamic(static_ram):
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
        'modules': static_ram.get('modules', []),
        'num_modules': static_ram.get('num_modules', 0),
        'max_speed_mhz': static_ram.get('max_speed_mhz'),
    }
    return ram_data


def _collect_battery_dynamic():
    ensure_com_initialized()
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
                'secsleft': ps_bat.secsleft,
            }
    except Exception:
        pass

    try:
        c = wmi.WMI()
        batteries = c.Win32_Battery()
        if batteries:
            b = batteries[0]
            design_cap = int(getattr(b, 'DesignCapacity', 0)) or 0
            full_cap = int(getattr(b, 'FullChargeCapacity', 0)) or 0
            wear_level = None
            if design_cap and full_cap:
                wear_level = round((1 - (full_cap / design_cap)) * 100, 1)

            if battery_data is None:
                battery_data = {
                    'name': getattr(b, 'Name', 'Battery'),
                    'status': getattr(b, 'BatteryStatus', 'N/A'),
                    'charge_percent': getattr(b, 'EstimatedChargeRemaining', None),
                    'power_plugged': None,
                    'secsleft': None,
                }

            battery_data['design_mwh'] = design_cap
            battery_data['full_mwh'] = full_cap
            battery_data['wear_level_percent'] = wear_level
    except Exception:
        pass

    return battery_data


def _collect_gpu_dynamic(static_gpus):
    def _normalize_gpu_name(name):
        return re.sub(r'[^a-z0-9]+', '', (name or '').lower())

    def _match_name_key(target_name, source_map):
        target = _normalize_gpu_name(target_name)
        if not target:
            return None
        for key in source_map.keys():
            if not key:
                continue
            if key in target or target in key:
                return key
        return None

    gpu_list = [dict(g) for g in static_gpus]

    nv_dict = {}
    nv_list = []
    try:
        for g in GPUtil.getGPUs():
            nv_list.append(g)
            nv_dict[_normalize_gpu_name(getattr(g, 'name', ''))] = g
    except Exception:
        nv_dict = {}
        nv_list = []

    nvml_map = {}
    nvml_list = []
    if PYNVML_AVAILABLE:
        try:
            nvml_count = pynvml.nvmlDeviceGetCount()
            for ni in range(nvml_count):
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

                entry = {
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
                nvml_list.append(entry)
                nvml_map[_normalize_gpu_name(name)] = entry
        except Exception:
            nvml_map = {}
            nvml_list = []

    for idx, gpu in enumerate(gpu_list):
        name = gpu.get('name', '')
        gpu.update({
            'vram_gputil_mb': None,
            'vram_nvml_mb': None,
            'vram_used_mb': None,
            'vram_free_mb': None,
            'vram_util_percent': None,
            'load_percent': None,
            'temp_c': None,
            'power_draw_w': None,
            'power_limit_w': None,
            'fan_speed_pct': None,
            'clock_graphics_mhz': None,
            'clock_mem_mhz': None,
        })

        gp = None
        gp_key = _match_name_key(name, nv_dict)
        if gp_key:
            gp = nv_dict.get(gp_key)
        elif idx < len(nv_list):
            gp = nv_list[idx]

        if gp is not None:
            gpu['vram_gputil_mb'] = int(gp.memoryTotal)
            gpu['load_percent'] = round(gp.load * 100, 1) if gp.load is not None else None
            gpu['temp_c'] = gp.temperature

        nv = None
        nv_key = _match_name_key(name, nvml_map)
        if nv_key:
            nv = nvml_map.get(nv_key)
        elif idx < len(nvml_list):
            nv = nvml_list[idx]

        if nv is not None:
            gpu['vram_nvml_mb'] = nv.get('total_mb')
            gpu['vram_used_mb'] = nv.get('used_mb')
            gpu['vram_free_mb'] = nv.get('free_mb')
            gpu['vram_util_percent'] = nv.get('mem_util_percent')
            if nv.get('load_percent') is not None:
                gpu['load_percent'] = int(nv.get('load_percent'))
            if nv.get('temp_c') is not None:
                gpu['temp_c'] = nv.get('temp_c')
            gpu['power_draw_w'] = nv.get('power_draw_w')
            gpu['power_limit_w'] = nv.get('power_limit_w')
            gpu['fan_speed_pct'] = nv.get('fan_speed_pct')
            gpu['clock_graphics_mhz'] = nv.get('clock_graphics_mhz')
            gpu['clock_mem_mhz'] = nv.get('clock_mem_mhz')

    return gpu_list


def _collect_disk_dynamic(static_disks):
    global _prev_disk_io, _prev_io_time

    logicals = []
    try:
        for part in psutil.disk_partitions(all=False):
            if not part.device:
                continue
            try:
                usage = psutil.disk_usage(part.mountpoint)
            except Exception:
                continue
            logicals.append({
                'device_id': part.device,
                'total_gb': round(usage.total / (1024 ** 3), 2),
                'free_gb': round(usage.free / (1024 ** 3), 2),
                'used_gb': round(usage.used / (1024 ** 3), 2),
                'filesystem': part.fstype or 'N/A',
                'volume_name': part.mountpoint,
                'drive_type': 'N/A',
            })
    except Exception:
        logicals = []

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

    return {
        'physical': static_disks.get('physical', []),
        'logical': logicals,
        'io': disk_io,
    }


def _collect_network_dynamic(static_network):
    global _prev_net_io, _prev_io_time

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

    return static_network, net_io


def _collect_processes_dynamic():
    procs = []
    for proc in psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_info', 'status', 'username']):
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

    return {
        'total': len(procs),
        'top_cpu': sorted(procs, key=lambda x: x['cpu_percent'], reverse=True)[:10],
        'top_mem': sorted(procs, key=lambda x: x['mem_mb'], reverse=True)[:10],
    }


def _build_alerts(stats):
    cpu_temp = (stats.get('cpu') or {}).get('temp_c')
    ram_pct = (stats.get('ram') or {}).get('percent')

    if cpu_temp is not None and cpu_temp > 85:
        _alert_state['cpu_temp_high_streak'] += 1
    else:
        _alert_state['cpu_temp_high_streak'] = 0

    if ram_pct is not None and ram_pct > 95:
        _alert_state['ram_high_streak'] += 1
    else:
        _alert_state['ram_high_streak'] = 0

    alerts = []
    if _alert_state['cpu_temp_high_streak'] >= 3:
        alerts.append({
            'type': 'cpu_temp',
            'severity': 'danger',
            'message': f'Nhiệt độ CPU cao liên tiếp: {cpu_temp}°C',
        })

    if _alert_state['ram_high_streak'] >= 3:
        alerts.append({
            'type': 'ram_usage',
            'severity': 'danger',
            'message': f'RAM sử dụng cao liên tiếp: {ram_pct}%',
        })

    return alerts


def collect_stats():
    """Collect dynamic system stats. Slow static hardware data is initialized once."""
    global _cached_stats, _last_collect_duration_ms
    global _last_gpu_stats, _last_gpu_collect_ts, _last_processes, _last_process_collect_ts

    # Prime psutil cpu_percent (first call returns 0)
    psutil.cpu_percent(interval=None)
    psutil.cpu_percent(percpu=True, interval=None)
    time.sleep(1)

    executor = ThreadPoolExecutor(max_workers=7)

    while True:
        cycle_start = time.perf_counter()
        now_ts = time.time()
        stats = {}
        try:
            futures = {
                'os': executor.submit(_collect_os_dynamic, _static_hw_info['os']),
                'cpu': executor.submit(_collect_cpu_dynamic, _static_hw_info['cpu']),
                'ram': executor.submit(_collect_ram_dynamic, _static_hw_info['ram']),
                'battery': executor.submit(_collect_battery_dynamic),
                'disk': executor.submit(_collect_disk_dynamic, _static_hw_info['disks']),
                'net': executor.submit(_collect_network_dynamic, _static_hw_info['network']),
            }

            if (not _last_gpu_stats) or (now_ts - _last_gpu_collect_ts >= GPU_SNAPSHOT_INTERVAL):
                futures['gpu'] = executor.submit(_collect_gpu_dynamic, _static_hw_info['gpus'])

            if ENABLE_PROCESSES and ((not _last_processes.get('top_cpu')) or (now_ts - _last_process_collect_ts >= PROCESS_SNAPSHOT_INTERVAL)):
                futures['proc'] = executor.submit(_collect_processes_dynamic)

            stats['os'] = futures['os'].result()
            cpu_data, cpu_percent = futures['cpu'].result()
            ram_data = futures['ram'].result()
            stats['cpu'] = cpu_data
            stats['ram'] = ram_data
            stats['battery'] = futures['battery'].result()

            if 'gpu' in futures:
                _last_gpu_stats = futures['gpu'].result()
                _last_gpu_collect_ts = now_ts
            stats['gpus'] = deepcopy(_last_gpu_stats)

            stats['disks'] = futures['disk'].result()
            stats['network'], stats['network_io'] = futures['net'].result()

            if ENABLE_PROCESSES:
                if 'proc' in futures:
                    _last_processes = futures['proc'].result()
                    _last_process_collect_ts = now_ts
                stats['processes'] = deepcopy(_last_processes)
            else:
                stats['processes'] = {'total': 0, 'top_cpu': [], 'top_mem': [], 'disabled': True}

            stats['alerts'] = _build_alerts(stats)
            stats['timestamp'] = datetime.now(timezone.utc).isoformat()
            stats['cpu_percent'] = cpu_percent
            stats['ram_summary'] = ram_data
            stats['collect_duration_ms'] = round((time.perf_counter() - cycle_start) * 1000, 2)
            _last_collect_duration_ms = stats['collect_duration_ms']

            with _cache_lock:
                _cached_stats = stats

        except Exception:
            logging.exception('Error collecting stats:')

        time.sleep(CACHE_INTERVAL)


# start background thread
_static_hw_info = init_static_hw_info()
_collector_thread = threading.Thread(target=collect_stats, daemon=True)
_collector_thread.start()


@app.route('/')
def index():
    return render_template('index.html', server_ip=SERVER_IP)


@app.route('/api/detailed_stats')
def detailed_stats():
    with _cache_lock:
        if _cached_stats:
            return jsonify(deepcopy(_cached_stats))
        else:
            return jsonify({'error': 'no data yet'}), 503


@app.route('/api/stream')
def detailed_stats_stream():
    def event_stream():
        last_ts = None
        while True:
            payload = None
            with _cache_lock:
                if _cached_stats:
                    payload = deepcopy(_cached_stats)

            if payload:
                ts = payload.get('timestamp')
                if ts != last_ts:
                    last_ts = ts
                    yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                else:
                    yield ': keepalive\n\n'
            else:
                yield 'event: waiting\ndata: {"status":"warming_up"}\n\n'
            time.sleep(1)

    headers = {
        'Cache-Control': 'no-cache',
        'X-Accel-Buffering': 'no',
    }
    return Response(stream_with_context(event_stream()), mimetype='text/event-stream', headers=headers)


def log_access_urls(host, port):
    if host == '0.0.0.0':
        logging.info('Open dashboard at http://127.0.0.1:%s', port)
        logging.info('Or from another device on your LAN: http://%s:%s', SERVER_IP, port)
    else:
        logging.info('Open dashboard at http://%s:%s', host, port)


if __name__ == '__main__':
    try:
        from waitress import serve
        logging.info('Starting with Waitress on %s:%s (interval=%ss)', HOST, PORT, CACHE_INTERVAL)
        log_access_urls(HOST, PORT)
        serve(app, host=HOST, port=PORT)
    except Exception:
        logging.info('Waitress not available, fallback to Flask development server on %s:%s', HOST, PORT)
        log_access_urls(HOST, PORT)
        app.run(host=HOST, port=PORT, debug=False)