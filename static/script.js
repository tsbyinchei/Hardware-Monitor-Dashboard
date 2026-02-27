const API = '/api/detailed_stats';
let currentData = null;

function setGauge(elem, percent, valueText) {
    if (!elem) return;
    // limit percent 0-100
    const p = Math.max(0, Math.min(100, Math.round(percent || 0)));
    elem.style.setProperty('--percent', p + '%');
    const val = elem.querySelector('.gauge-value');
    if (val) val.innerText = valueText;
}

function createCircularGauge(id, label, color, size=110) {
    const wrapper = document.createElement('div');
    wrapper.className = 'gauge-card';
    const chart = document.createElement('div');
    chart.className = 'circular-chart';
    chart.id = id;
    chart.style.setProperty('--percent', '0%');
    chart.style.setProperty('--color', color);
    chart.style.width = `${size}px`;
    chart.style.height = `${size}px`;
    chart.innerHTML = `<div class="circular-chart-inner"><span class="gauge-value">0%</span><span class="gauge-label">${label}</span></div>`;
    wrapper.appendChild(chart);
    return wrapper;
}

function formatGB(v){ return `${v} GB`; }

function render(data){
    currentData = data;
    // node name
    const node = data.os && data.os.node_name ? data.os.node_name : (data.os && data.os.name ? data.os.name : 'Unknown');
    document.getElementById('node-name').innerText = node;
    document.getElementById('last-updated').innerText = `Last updated: ${data.timestamp || 'N/A'}`;

    // CPU
    const cpuBoxName = document.getElementById('cpu-name');
    const cpuDetails = document.getElementById('cpu-details');
    const cpuGauge = document.getElementById('cpu-gauge');
    if (data.cpu){
        cpuBoxName.innerText = data.cpu.name;
        cpuDetails.innerHTML = `Manufacturer: <span class="small-muted">${data.cpu.manufacturer}</span> • Cores: <strong>${data.cpu.cores}</strong> • Threads: <strong>${data.cpu.logical_processors}</strong> • Max: <strong>${data.cpu.max_clock_mhz} MHz</strong> • Current: <strong>${data.cpu.current_clock_mhz} MHz</strong>`;
        setGauge(cpuGauge, data.cpu.percent, `${data.cpu.percent}%`);
    } else {
        cpuBoxName.innerText = 'CPU info not available';
        cpuDetails.innerText = '';
    }

    // RAM
    const ramGauge = document.getElementById('ram-gauge');
    const ramMeta = document.getElementById('ram-meta');
    if (data.ram_summary){
        setGauge(ramGauge, data.ram_summary.percent, `${data.ram_summary.percent}%`);
        ramMeta.innerText = `${data.ram_summary.used_gb}/${data.ram_summary.total_gb} GB used (${data.ram_summary.percent}%)`;
    }
    // RAM modules
    const ramModules = document.getElementById('ram-modules');
    ramModules.innerHTML = '';
    if (data.ram && data.ram.modules && data.ram.modules.length){
        data.ram.modules.forEach(m => {
            const div = document.createElement('div');
            div.className = 'disk-item';
            div.innerHTML = `<strong>${m.bank}</strong> • ${m.capacity_gb} GB • ${m.speed_mhz} MHz • ${m.manufacturer} <span class="small-muted">${m.part_number}</span>`;
            ramModules.appendChild(div);
        });
    } else {
        ramModules.innerText = 'No module details available';
    }

    // GPUs
    const gpuContent = document.getElementById('gpu-content');
    gpuContent.innerHTML = '';
    const gpuGauge = document.getElementById('gpu-gauge');
    if (data.gpus && data.gpus.length){
        let maxLoad = 0;
        data.gpus.forEach(g => {
            const el = document.createElement('div');
            el.className = 'gpu-item';
            el.innerHTML = `<strong>${g.name}</strong><br>
                VRAM reported: <strong>${g.vram_reported_mb || 'N/A'} MB</strong> • GPUtil: <strong>${g.vram_gputil_mb || 'N/A'} MB</strong><br>
                Driver: <span class="small-muted">${g.driver}</span> • VideoProc: <span class="small-muted">${g.video_processor}</span><br>
                Temp: <strong>${g.temp_c || 'N/A'} °C</strong> • Load: <strong>${g.load_percent || 'N/A'}%</strong>`;
            gpuContent.appendChild(el);
            if (g.load_percent && g.load_percent > maxLoad) maxLoad = g.load_percent;
        });
        setGauge(gpuGauge, Math.round(maxLoad || 0), `${Math.round(maxLoad||0)}%`);
    } else {
        gpuContent.innerText = 'No GPU data';
        setGauge(gpuGauge, 0, '0%');
    }

    // Disks
    const diskContent = document.getElementById('disk-content');
    diskContent.innerHTML = '';
    const extra = document.getElementById('extra-gauges');
    extra.innerHTML = '';
    if (data.disks){
        (data.disks.physical||[]).forEach(d => {
            const div = document.createElement('div');
            div.className = 'disk-item';
            div.innerHTML = `<strong>${d.model}</strong><br>${d.interface} • ${d.size_gb} GB • FW: ${d.firmware} • S/N: ${d.serial}`;
            diskContent.appendChild(div);
        });
        (data.disks.logical||[]).forEach((ld, idx) => {
            const usedPct = ld.total_gb? Math.round(((ld.total_gb-ld.free_gb)/ld.total_gb)*100):0;
            const g = createCircularGauge(`disk-${idx}` , ld.device_id, '#f39c12', 90);
            setGauge(g.querySelector('.circular-chart'), usedPct, `${usedPct}%`);
            const label = document.createElement('div');
            label.style.fontSize='0.85rem';
            label.style.marginTop='6px';
            label.innerText = `${ld.free_gb} GB free / ${ld.total_gb} GB`;
            g.appendChild(label);
            extra.appendChild(g);
        });
    }

    // Network
    const netContent = document.getElementById('network-content');
    netContent.innerHTML = '';
    if (data.network && data.network.length){
        data.network.forEach((n, idx)=>{
            const div = document.createElement('div');
            div.className = 'net-item';
            div.innerHTML = `<strong>${n.description}</strong><br>MAC: ${n.mac} • IPs: ${n.ips.join(', ') || 'N/A'} • DHCP: ${n.dhcp}`;
            netContent.appendChild(div);
            const g = createCircularGauge(`net-${idx}`,'NET','#16a085',70);
            const up = (n.ips && n.ips.length)?100:0;
            setGauge(g.querySelector('.circular-chart'), up, up? 'up':'down');
            extra.appendChild(g);
        });
    } else {
        netContent.innerText = 'No network data';
    }

    // Battery
    const batteryBox = document.getElementById('battery-box');
    const batteryContent = document.getElementById('battery-content');
    if (data.battery){
        batteryBox.style.display='block';
        batteryContent.innerHTML = `<strong>${data.battery.name}</strong><br>Charge: <strong>${data.battery.charge_percent || 'N/A'}%</strong> • Design: <strong>${data.battery.design_mwh || 0} mWh</strong> • Full: <strong>${data.battery.full_mwh || 0} mWh</strong> • Wear: <strong>${data.battery.wear_level_percent || 'N/A'}%</strong> • Status: <span class="small-muted">${data.battery.status}</span>`;
    } else {
        batteryBox.style.display='none';
    }
}

async function poll(){
    try{
        const res = await fetch(API);
        if (!res.ok) throw new Error('no data');
        const data = await res.json();
        render(data);
    }catch(e){
        console.error('Poll error', e);
    }
}

window.addEventListener('load', ()=>{ poll(); setInterval(poll, 2000); });