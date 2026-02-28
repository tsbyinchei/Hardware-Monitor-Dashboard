const API = '/api/detailed_stats';
let currentData = null;
let currentSection = 'cpu';

// History for mini charts (~30 points)
const HISTORY_LENGTH = 30;
let cpuHistory = [];
let ramHistory = [];
let cpuChart = null;
let ramChart = null;

// ===== Navigation =====
function initNavigation() {
    const navItems = document.querySelectorAll('.nav-item');
    const sections = document.querySelectorAll('.detail-section');
    const sidebar = document.getElementById('sidebar');
    const overlay = document.getElementById('sidebar-overlay');
    const menuToggle = document.getElementById('menu-toggle');

    navItems.forEach(item => {
        item.addEventListener('click', (e) => {
            e.preventDefault();
            const section = item.dataset.section;
            switchSection(section);

            // Close mobile sidebar
            if (window.innerWidth <= 768) {
                sidebar.classList.remove('open');
                overlay.classList.remove('visible');
            }
        });
    });

    menuToggle.addEventListener('click', () => {
        sidebar.classList.toggle('open');
        overlay.classList.toggle('visible');
    });

    overlay.addEventListener('click', () => {
        sidebar.classList.remove('open');
        overlay.classList.remove('visible');
    });
}

function switchSection(section) {
    currentSection = section;
    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
    document.querySelector(`.nav-item[data-section="${section}"]`)?.classList.add('active');

    document.querySelectorAll('.detail-section').forEach(s => s.classList.remove('active'));
    const target = document.getElementById(`section-${section}`);
    if (target) target.classList.add('active');

    // Resize charts when switching to CPU/RAM (fix Chart.js in hidden container)
    setTimeout(() => {
        cpuChart?.resize();
        ramChart?.resize();
    }, 100);
}

// ===== Copy to clipboard =====
function initCopyButtons() {
    document.querySelectorAll('.btn-copy').forEach(btn => {
        btn.addEventListener('click', async () => {
            const section = btn.dataset.copy;
            const content = document.getElementById(`${section}-content`);
            if (!content) return;

            const text = content.innerText;
            try {
                await navigator.clipboard.writeText(text);
                btn.classList.add('copied');
                btn.querySelector('i').className = 'fa-solid fa-check';
                setTimeout(() => {
                    btn.classList.remove('copied');
                    btn.querySelector('i').className = 'fa-regular fa-copy';
                }, 2000);
            } catch (e) {
                console.error('Copy failed', e);
            }
        });
    });
}

// ===== Refresh =====
function initRefresh() {
    const btn = document.getElementById('btn-refresh');
    btn.addEventListener('click', async () => {
        btn.classList.add('loading');
        await poll();
        btn.classList.remove('loading');
    });
}

// ===== Info row helper =====
function infoRow(label, value) {
    return `<div class="info-row"><span class="info-label">${label}</span><span class="info-value">${value ?? 'N/A'}</span></div>`;
}

// ===== Render functions =====
function renderCPU(data) {
    const el = document.getElementById('cpu-content');
    if (!data.cpu) {
        el.innerHTML = '<p class="text-muted">Không có thông tin CPU</p>';
        return;
    }

    const cpu = data.cpu;
    const percent = Math.round(cpu.percent || 0);

    el.innerHTML = `
        <div class="gauge-wrapper">
            <div class="circular-gauge" style="--percent: ${percent}; --gauge-color: #06b6d4;">
                <div class="circular-gauge-inner">
                    <span class="gauge-value">${percent}%</span>
                    <span class="gauge-label">Sử dụng</span>
                </div>
            </div>
            <div style="flex:1; min-width:200px;">
                <div class="info-row">
                    <span class="info-label">Tên CPU</span>
                    <span class="info-value" style="text-align:right; font-family:Inter;">${cpu.name}</span>
                </div>
                <div class="info-row">
                    <span class="info-label">Nhà sản xuất</span>
                    <span class="info-value">${cpu.manufacturer}</span>
                </div>
                <div class="info-row">
                    <span class="info-label">Số nhân / Luồng</span>
                    <span class="info-value">${cpu.cores} / ${cpu.logical_processors}</span>
                </div>
                <div class="info-row">
                    <span class="info-label">Xung tối đa</span>
                    <span class="info-value">${cpu.max_clock_mhz} MHz</span>
                </div>
                <div class="info-row">
                    <span class="info-label">Xung hiện tại</span>
                    <span class="info-value">${cpu.current_clock_mhz} MHz</span>
                </div>
            </div>
        </div>
        <div class="chart-container">
            <canvas id="cpu-chart"></canvas>
        </div>
    `;

    updateCpuChart();
}

function renderRAM(data) {
    const el = document.getElementById('ram-content');
    const ram = data.ram || data.ram_summary;
    if (!ram) {
        el.innerHTML = '<p class="text-muted">Không có thông tin RAM</p>';
        return;
    }

    const total = ram.total_gb ?? 0;
    const used = ram.used_gb ?? (total - (ram.available_gb ?? 0));
    const percent = Math.round(ram.percent || 0);
    const modules = ram.modules || [];

    let progressClass = '';
    if (percent >= 90) progressClass = 'danger';
    else if (percent >= 75) progressClass = 'warning';

    el.innerHTML = `
        <div class="gauge-wrapper">
            <div class="circular-gauge" style="--percent: ${percent}; --gauge-color: #a855f7;">
                <div class="circular-gauge-inner">
                    <span class="gauge-value">${percent}%</span>
                    <span class="gauge-label">Sử dụng</span>
                </div>
            </div>
            <div style="flex:1; min-width:200px;">
                <div class="info-row">
                    <span class="info-label">Tổng dung lượng</span>
                    <span class="info-value">${total} GB</span>
                </div>
                <div class="info-row">
                    <span class="info-label">Đã sử dụng</span>
                    <span class="info-value">${used} GB</span>
                </div>
                <div class="info-row">
                    <span class="info-label">Còn trống</span>
                    <span class="info-value">${ram.available_gb ?? (total - used).toFixed(2)} GB</span>
                </div>
                <div class="info-row" style="align-items:center;">
                    <span class="info-label">Tiến trình</span>
                    <div class="progress-bar" style="flex:1;">
                        <div class="progress-fill ${progressClass}" style="width:${percent}%"></div>
                    </div>
                </div>
            </div>
        </div>
        <div class="chart-container">
            <canvas id="ram-chart"></canvas>
        </div>
        ${modules.length ? `
        <div style="margin-top:24px;">
            <h3 style="margin:0 0 12px 0; font-size:1rem; color:var(--accent);">Chi tiết từng thanh RAM</h3>
            <div class="info-table-wrapper">
            <table class="info-table">
                <thead><tr><th>Bank</th><th>Dung lượng</th><th>Tốc độ</th><th>Hãng</th><th>Part Number</th><th>Loại</th></tr></thead>
                <tbody>
                    ${modules.map(m => `
                        <tr>
                            <td>${m.bank}</td>
                            <td>${m.capacity_gb} GB</td>
                            <td>${m.speed_mhz} MHz</td>
                            <td>${m.manufacturer}</td>
                            <td class="text-muted small">${m.part_number || '-'}</td>
                            <td>${m.memory_type || '-'}</td>
                        </tr>
                    `).join('')}
                </tbody>
            </table>
            </div>
        </div>
        ` : ''}
    `;

    updateRamChart();
}

function renderGPU(data) {
    const el = document.getElementById('gpu-content');
    const gpus = data.gpus || [];

    if (!gpus.length) {
        el.innerHTML = '<p class="text-muted">Không có thông tin GPU</p>';
        return;
    }

    el.innerHTML = gpus.map((g, i) => {
        const load = g.load_percent ?? 0;
        const vram = g.vram_nvml_mb ?? g.vram_gputil_mb ?? g.vram_reported_mb;
        return `
            <div class="component-item">
                <h3><i class="fa-solid fa-desktop"></i> GPU ${i + 1}: ${g.name}</h3>
                <div class="gauge-wrapper" style="margin-bottom:12px;">
                    <div class="circular-gauge" style="--percent: ${load}; --gauge-color: #22c55e;">
                        <div class="circular-gauge-inner">
                            <span class="gauge-value">${load}%</span>
                            <span class="gauge-label">Tải</span>
                        </div>
                    </div>
                    <div style="flex:1;">
                        <div class="info-row"><span class="info-label">VRAM</span><span class="info-value">${vram ?? 'N/A'} MB</span></div>
                        <div class="info-row"><span class="info-label">Nhiệt độ</span><span class="info-value">${g.temp_c ?? 'N/A'} °C</span></div>
                        <div class="info-row"><span class="info-label">Driver</span><span class="info-value">${g.driver}</span></div>
                        <div class="info-row"><span class="info-label">Độ phân giải</span><span class="info-value">${g.resolution}</span></div>
                        <div class="info-row"><span class="info-label">Video Processor</span><span class="info-value">${g.video_processor}</span></div>
                    </div>
                </div>
            </div>
        `;
    }).join('');
}

function renderDisk(data) {
    const el = document.getElementById('disk-content');
    const disks = data.disks || { physical: [], logical: [] };
    const physical = disks.physical || [];
    const logical = disks.logical || [];

    let html = '';

    if (physical.length) {
        html += `
            <h3 style="margin:0 0 12px 0; font-size:1rem; color:var(--accent);">Ổ cứng vật lý</h3>
            ${physical.map(d => `
                <div class="component-item">
                    <div class="info-row"><span class="info-label">Model</span><span class="info-value" style="font-family:Inter;">${d.model}</span></div>
                    <div class="info-row"><span class="info-label">Giao diện</span><span class="info-value">${d.interface}</span></div>
                    <div class="info-row"><span class="info-label">Dung lượng</span><span class="info-value">${d.size_gb} GB</span></div>
                    <div class="info-row"><span class="info-label">Firmware</span><span class="info-value">${d.firmware}</span></div>
                    <div class="info-row"><span class="info-label">Serial</span><span class="info-value small">${d.serial}</span></div>
                </div>
            `).join('')}
        `;
    }

    if (logical.length) {
        html += `
            <h3 style="margin:24px 0 12px 0; font-size:1rem; color:var(--accent);">Ổ logic</h3>
            ${logical.map(ld => {
                const usedPct = ld.total_gb ? Math.round(((ld.total_gb - ld.free_gb) / ld.total_gb) * 100) : 0;
                const pClass = usedPct >= 90 ? 'danger' : usedPct >= 75 ? 'warning' : '';
                return `
                    <div class="component-item">
                        <div class="info-row">
                            <span class="info-label">${ld.device_id}</span>
                            <span class="info-value">${ld.free_gb} GB trống / ${ld.total_gb} GB (${ld.filesystem})</span>
                        </div>
                        <div class="progress-bar" style="margin-top:8px;">
                            <div class="progress-fill ${pClass}" style="width:${usedPct}%"></div>
                        </div>
                    </div>
                `;
            }).join('')}
        `;
    }

    if (!html) html = '<p class="text-muted">Không có thông tin ổ cứng</p>';
    el.innerHTML = html;
}

function renderNetwork(data) {
    const el = document.getElementById('network-content');
    const nets = data.network || [];

    if (!nets.length) {
        el.innerHTML = '<p class="text-muted">Không có thông tin mạng</p>';
        return;
    }

    el.innerHTML = nets.map(n => {
        const hasIp = n.ips && n.ips.length;
        const statusClass = hasIp ? 'badge-success' : 'badge-muted';
        const statusText = hasIp ? 'Kết nối' : 'Không kết nối';
        return `
            <div class="component-item">
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:12px;">
                    <h3 style="margin:0;">${n.description}</h3>
                    <span class="badge ${statusClass}">${statusText}</span>
                </div>
                <div class="info-row"><span class="info-label">MAC</span><span class="info-value">${n.mac}</span></div>
                <div class="info-row"><span class="info-label">IP</span><span class="info-value">${(n.ips || []).join(', ') || 'N/A'}</span></div>
                <div class="info-row"><span class="info-label">Gateway</span><span class="info-value">${(n.gateway || []).join(', ') || 'N/A'}</span></div>
                <div class="info-row"><span class="info-label">DNS</span><span class="info-value">${(n.dns || []).join(', ') || 'N/A'}</span></div>
                <div class="info-row"><span class="info-label">DHCP</span><span class="info-value">${n.dhcp ? 'Bật' : 'Tắt'}</span></div>
            </div>
        `;
    }).join('');
}

function renderBattery(data) {
    const el = document.getElementById('battery-content');
    const bat = data.battery;

    if (!bat) {
        el.innerHTML = '<p class="text-muted">Không có pin (máy để bàn)</p>';
        return;
    }

    const charge = bat.charge_percent ?? 0;
    const wear = bat.wear_level_percent;
    const designStr = (bat.design_mwh && bat.design_mwh > 0) ? `${bat.design_mwh} mWh` : 'N/A';
    const fullStr = (bat.full_mwh && bat.full_mwh > 0) ? `${bat.full_mwh} mWh` : 'N/A';
    const secsleft = bat.secsleft;
    const maxReasonable = 86400 * 365; // 1 năm - loại POWER_TIME_UNLIMITED
    const timeLeftStr = (secsleft != null && secsleft > 0 && secsleft < maxReasonable)
        ? (secsleft < 3600 ? `~${Math.round(secsleft / 60)} phút` : `~${Math.round(secsleft / 3600)} giờ`)
        : null;

    let extraRows = '';
    if (timeLeftStr && !bat.power_plugged) {
        extraRows = `<div class="info-row"><span class="info-label">Thời gian còn lại</span><span class="info-value">${timeLeftStr}</span></div>`;
    }

    el.innerHTML = `
        <div class="gauge-wrapper">
            <div class="circular-gauge" style="--percent: ${charge}; --gauge-color: #f59e0b;">
                <div class="circular-gauge-inner">
                    <span class="gauge-value">${charge}%</span>
                    <span class="gauge-label">Pin</span>
                </div>
            </div>
            <div style="flex:1;">
                <div class="info-row"><span class="info-label">Tên pin</span><span class="info-value">${bat.name}</span></div>
                <div class="info-row"><span class="info-label">Trạng thái</span><span class="info-value">${bat.status}</span></div>
                ${bat.design_mwh > 0 ? `<div class="info-row"><span class="info-label">Dung lượng thiết kế</span><span class="info-value">${designStr}</span></div>` : ''}
                ${bat.full_mwh > 0 ? `<div class="info-row"><span class="info-label">Dung lượng đầy hiện tại</span><span class="info-value">${fullStr}</span></div>` : ''}
                ${wear != null ? `<div class="info-row"><span class="info-label">Độ chai pin</span><span class="info-value ${wear > 20 ? 'text-warning' : ''}">${wear}%</span></div>` : ''}
                ${extraRows}
            </div>
        </div>
    `;
}

function renderOS(data) {
    const el = document.getElementById('os-content');
    const os = data.os;

    if (!os) {
        el.innerHTML = '<p class="text-muted">Không có thông tin hệ điều hành</p>';
        return;
    }

    el.innerHTML = `
        <div class="info-row"><span class="info-label">Tên hệ điều hành</span><span class="info-value" style="font-family:Inter;">${os.name}</span></div>
        <div class="info-row"><span class="info-label">Phiên bản</span><span class="info-value">${os.display_version || 'N/A'}</span></div>
        <div class="info-row"><span class="info-label">Build</span><span class="info-value">${os.build}</span></div>
        <div class="info-row"><span class="info-label">Kiến trúc</span><span class="info-value">${os.arch}</span></div>
        <div class="info-row"><span class="info-label">Tên máy</span><span class="info-value">${os.node_name}</span></div>
    `;
}

// ===== Charts =====
function updateCpuChart() {
    const canvas = document.getElementById('cpu-chart');
    if (!canvas) return;

    const ctx = canvas.getContext('2d');
    const labels = cpuHistory.map((_, i) => '');
    const data = cpuHistory.map(h => h);

    if (cpuChart) cpuChart.destroy();

    cpuChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels,
            datasets: [{
                label: 'CPU %',
                data,
                borderColor: '#06b6d4',
                backgroundColor: 'rgba(6, 182, 212, 0.1)',
                fill: true,
                tension: 0.3
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { display: false } },
            scales: {
                y: { min: 0, max: 100, grid: { color: 'rgba(255,255,255,0.05)' } },
                x: { display: false }
            }
        }
    });
}

function updateRamChart() {
    const canvas = document.getElementById('ram-chart');
    if (!canvas) return;

    const ctx = canvas.getContext('2d');
    const labels = ramHistory.map((_, i) => '');
    const data = ramHistory.map(h => h);

    if (ramChart) ramChart.destroy();

    ramChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels,
            datasets: [{
                label: 'RAM %',
                data,
                borderColor: '#a855f7',
                backgroundColor: 'rgba(168, 85, 247, 0.1)',
                fill: true,
                tension: 0.3
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { display: false } },
            scales: {
                y: { min: 0, max: 100, grid: { color: 'rgba(255,255,255,0.05)' } },
                x: { display: false }
            }
        }
    });
}

function pushHistory() {
    if (!currentData) return;
    const cpuPct = currentData.cpu?.percent ?? 0;
    const ramPct = currentData.ram_summary?.percent ?? currentData.ram?.percent ?? 0;

    cpuHistory.push(cpuPct);
    ramHistory.push(ramPct);
    if (cpuHistory.length > HISTORY_LENGTH) cpuHistory.shift();
    if (ramHistory.length > HISTORY_LENGTH) ramHistory.shift();
}

// ===== Main render =====
function render(data) {
    currentData = data;
    pushHistory();

    const node = data.os?.node_name || data.os?.name || 'Unknown';
    document.getElementById('node-name').innerText = node;
    document.getElementById('last-updated').innerText = data.timestamp ? new Date(data.timestamp).toLocaleTimeString('vi-VN') : '--';

    renderCPU(data);
    renderRAM(data);
    renderGPU(data);
    renderDisk(data);
    renderNetwork(data);
    renderBattery(data);
    renderOS(data);
}

// ===== Poll =====
async function poll() {
    try {
        const res = await fetch(API);
        if (!res.ok) throw new Error('no data');
        const data = await res.json();
        render(data);
    } catch (e) {
        console.error('Poll error', e);
        document.getElementById('last-updated').innerText = 'Đang chờ dữ liệu...';
    }
}

// ===== Init =====
window.addEventListener('load', () => {
    initNavigation();
    initCopyButtons();
    initRefresh();
    poll();
    setInterval(poll, 2000);
});
