const API = '/api/detailed_stats';
let currentData = null;
let currentSection = 'cpu';
let currentProcTab = 'cpu'; // 'cpu' or 'mem'

// History for mini charts (~60 points)
const HISTORY_LENGTH = 60;
let cpuHistory = [];
let ramHistory = [];
let netSentHistory = [];
let netRecvHistory = [];
let diskReadHistory = [];
let diskWriteHistory = [];
let cpuChart = null;
let ramChart = null;
let netChart = null;
let diskIoChart = null;

// ===== Navigation =====
function initNavigation() {
    const navItems = document.querySelectorAll('.nav-item');
    const sidebar = document.getElementById('sidebar');
    const overlay = document.getElementById('sidebar-overlay');
    const menuToggle = document.getElementById('menu-toggle');

    navItems.forEach(item => {
        item.addEventListener('click', (e) => {
            e.preventDefault();
            const section = item.dataset.section;
            switchSection(section);
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

    setTimeout(() => {
        cpuChart?.resize();
        ramChart?.resize();
        netChart?.resize();
        diskIoChart?.resize();
    }, 100);
}

// ===== Process tabs =====
function initProcessTabs() {
    const group = document.getElementById('proc-tab-group');
    if (!group) return;
    group.addEventListener('click', (e) => {
        const btn = e.target.closest('.tab-btn');
        if (!btn) return;
        group.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        currentProcTab = btn.dataset.tab;
        if (currentData) renderProcesses(currentData);
    });
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

// ===== Helpers =====
function infoRow(label, value, extra = '') {
    return `<div class="info-row ${extra}"><span class="info-label">${label}</span><span class="info-value">${value ?? 'N/A'}</span></div>`;
}

function formatUptime(seconds) {
    if (!seconds || seconds <= 0) return 'N/A';
    const d = Math.floor(seconds / 86400);
    const h = Math.floor((seconds % 86400) / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const parts = [];
    if (d > 0) parts.push(`${d}d`);
    if (h > 0) parts.push(`${h}h`);
    parts.push(`${m}m`);
    return parts.join(' ');
}

function tempClass(temp) {
    if (temp == null) return '';
    if (temp >= 90) return 'text-danger';
    if (temp >= 75) return 'text-warning';
    return 'text-success';
}

function tempBadge(temp) {
    if (temp == null) return '<span class="text-muted">N/A</span>';
    const cls = tempClass(temp);
    return `<span class="${cls}">${temp} °C</span>`;
}

// ===== Render CPU =====
function renderCPU(data) {
    const el = document.getElementById('cpu-content');
    if (!data.cpu) { el.innerHTML = '<p class="text-muted">Không có thông tin CPU</p>'; return; }

    const cpu = data.cpu;
    const percent = Math.round(cpu.percent || 0);
    const perCore = cpu.per_core_percent || [];
    const perCoreFreq = cpu.per_core_freq_mhz || [];

    const archMap = { 0: 'x86', 1: 'MIPS', 2: 'Alpha', 3: 'PowerPC', 5: 'ARM', 6: 'ia64', 9: 'x64' };

    let perCoreHtml = '';
    if (perCore.length > 0) {
        perCoreHtml = `
            <div style="margin-top:20px;">
                <h3 class="subsection-title"><i class="fa-solid fa-layer-group"></i> Sử dụng từng nhân</h3>
                <div class="core-grid">
                    ${perCore.map((pct, i) => {
                        const freq = perCoreFreq[i] ? `${perCoreFreq[i]} MHz` : '';
                        const pctRounded = Math.round(pct);
                        const coreClass = pctRounded >= 90 ? 'danger' : pctRounded >= 70 ? 'warning' : '';
                        return `
                            <div class="core-item">
                                <div class="core-label">C${i}</div>
                                <div class="core-bar-wrap">
                                    <div class="core-bar ${coreClass}" style="width:${pctRounded}%"></div>
                                </div>
                                <div class="core-pct">${pctRounded}%</div>
                                ${freq ? `<div class="core-freq">${freq}</div>` : ''}
                            </div>
                        `;
                    }).join('')}
                </div>
            </div>
        `;
    }

    el.innerHTML = `
        <div class="gauge-wrapper">
            <div class="circular-gauge" style="--percent: ${percent}; --gauge-color: #06b6d4;">
                <div class="circular-gauge-inner">
                    <span class="gauge-value">${percent}%</span>
                    <span class="gauge-label">Sử dụng</span>
                </div>
            </div>
            <div style="flex:1; min-width:200px;">
                ${infoRow('Tên CPU', `<span style="font-family:Inter;">${cpu.name}</span>`)}
                ${infoRow('Nhà sản xuất', cpu.manufacturer)}
                ${infoRow('Socket', cpu.socket)}
                ${infoRow('Số nhân / Luồng', `${cpu.cores} / ${cpu.logical_processors}`)}
                ${infoRow('Kiến trúc', archMap[cpu.architecture] || cpu.architecture)}
                ${infoRow('Xung tối đa', cpu.max_clock_mhz !== 'N/A' ? `${cpu.max_clock_mhz} MHz` : 'N/A')}
                ${infoRow('Xung hiện tại', cpu.current_clock_mhz !== 'N/A' ? `${cpu.current_clock_mhz} MHz` : 'N/A')}
                ${cpu.l2_cache_kb ? infoRow('Cache L2', `${cpu.l2_cache_kb} KB`) : ''}
                ${cpu.l3_cache_kb ? infoRow('Cache L3', `${cpu.l3_cache_kb} KB`) : ''}
                ${infoRow('Nhiệt độ', tempBadge(cpu.temp_c))}
                ${cpu.virtualization != null ? infoRow('Ảo hóa (VT-x/AMD-V)', cpu.virtualization ? '<span class="text-success">Bật</span>' : '<span class="text-muted">Tắt</span>') : ''}
            </div>
        </div>
        <div class="chart-container" style="height:140px;">
            <canvas id="cpu-chart"></canvas>
        </div>
        ${perCoreHtml}
    `;

    updateCpuChart();
}

// ===== Render RAM =====
function renderRAM(data) {
    const el = document.getElementById('ram-content');
    const ram = data.ram || data.ram_summary;
    if (!ram) { el.innerHTML = '<p class="text-muted">Không có thông tin RAM</p>'; return; }

    const total = ram.total_gb ?? 0;
    const used = ram.used_gb ?? (total - (ram.available_gb ?? 0));
    const percent = Math.round(ram.percent || 0);
    const modules = ram.modules || [];

    let progressClass = percent >= 90 ? 'danger' : percent >= 75 ? 'warning' : '';

    const memTypeMap = {
        20: 'DDR', 21: 'DDR2', 24: 'DDR3', 26: 'DDR4', 34: 'DDR5', 0: 'Unknown'
    };

    el.innerHTML = `
        <div class="gauge-wrapper">
            <div class="circular-gauge" style="--percent: ${percent}; --gauge-color: #a855f7;">
                <div class="circular-gauge-inner">
                    <span class="gauge-value">${percent}%</span>
                    <span class="gauge-label">Sử dụng</span>
                </div>
            </div>
            <div style="flex:1; min-width:200px;">
                ${infoRow('Tổng dung lượng', `${total} GB`)}
                ${infoRow('Đã sử dụng', `${used} GB`)}
                ${infoRow('Còn trống', `${ram.available_gb ?? (total - used).toFixed(2)} GB`)}
                ${ram.cached_gb ? infoRow('Cache', `${ram.cached_gb} GB`) : ''}
                ${ram.num_modules ? infoRow('Số thanh RAM', `${ram.num_modules} thanh`) : ''}
                ${ram.max_speed_mhz ? infoRow('Tốc độ', `${ram.max_speed_mhz} MHz`) : ''}
                <div class="info-row" style="align-items:center;">
                    <span class="info-label">Dung lượng dùng</span>
                    <div class="progress-bar" style="flex:1;">
                        <div class="progress-fill ${progressClass}" style="width:${percent}%"></div>
                    </div>
                </div>
            </div>
        </div>
        <div class="chart-container" style="height:140px;">
            <canvas id="ram-chart"></canvas>
        </div>
        ${ram.swap_total_gb > 0 ? `
        <div class="swap-section">
            <h3 class="subsection-title"><i class="fa-solid fa-shuffle"></i> Bộ nhớ ảo (Swap/Page)</h3>
            <div class="info-row"><span class="info-label">Tổng Swap</span><span class="info-value">${ram.swap_total_gb} GB</span></div>
            <div class="info-row"><span class="info-label">Đang dùng</span><span class="info-value">${ram.swap_used_gb} GB (${ram.swap_percent}%)</span></div>
            <div class="progress-bar" style="margin-top:8px;">
                <div class="progress-fill ${ram.swap_percent >= 80 ? 'danger' : ''}" style="width:${ram.swap_percent}%"></div>
            </div>
        </div>
        ` : ''}
        ${modules.length ? `
        <div style="margin-top:24px;">
            <h3 class="subsection-title"><i class="fa-solid fa-memory"></i> Chi tiết từng thanh RAM (${modules.length} thanh)</h3>
            <div class="info-table-wrapper">
            <table class="info-table">
                <thead><tr><th>Slot</th><th>Dung lượng</th><th>Loại</th><th>Tốc độ (XMP)</th><th>Form Factor</th><th>Hãng</th><th>Part Number</th><th>Điện áp</th></tr></thead>
                <tbody>
                    ${modules.map(m => `
                        <tr>
                            <td>${m.slot || m.bank}</td>
                            <td>${m.capacity_gb} GB</td>
                            <td><span class="badge badge-accent">${m.memory_type}</span></td>
                            <td>${m.speed_mhz} MHz${m.configured_speed_mhz && m.configured_speed_mhz !== m.speed_mhz ? ` <span class="text-muted small">(${m.configured_speed_mhz})</span>` : ''}</td>
                            <td>${m.form_factor}</td>
                            <td>${m.manufacturer}</td>
                            <td class="text-muted small">${m.part_number || '-'}</td>
                            <td>${m.voltage ? m.voltage + ' mV' : '-'}</td>
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

// ===== Render GPU =====
function renderGPU(data) {
    const el = document.getElementById('gpu-content');
    const gpus = data.gpus || [];
    if (!gpus.length) { el.innerHTML = '<p class="text-muted">Không có thông tin GPU</p>'; return; }

    el.innerHTML = gpus.map((g, i) => {
        const load = g.load_percent ?? 0;
        const vramTotal = g.vram_nvml_mb ?? g.vram_gputil_mb ?? g.vram_reported_mb ?? 0;
        const vramUsed = g.vram_used_mb ?? null;
        const vramPct = (vramTotal > 0 && vramUsed != null) ? Math.round((vramUsed / vramTotal) * 100) : null;
        const vramClass = vramPct >= 90 ? 'danger' : vramPct >= 70 ? 'warning' : '';

        return `
            <div class="component-item">
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:12px;">
                    <h3 style="margin:0;"><i class="fa-solid fa-desktop"></i> GPU ${i + 1}: ${g.name}</h3>
                    <span class="badge ${g.status === 'OK' ? 'badge-success' : 'badge-muted'}">${g.status || 'N/A'}</span>
                </div>
                <div class="gauge-wrapper" style="margin-bottom:12px;">
                    <div class="circular-gauge" style="--percent: ${load}; --gauge-color: #22c55e;">
                        <div class="circular-gauge-inner">
                            <span class="gauge-value">${load}%</span>
                            <span class="gauge-label">GPU</span>
                        </div>
                    </div>
                    ${vramPct != null ? `
                    <div class="circular-gauge" style="--percent: ${vramPct}; --gauge-color: #f97316;">
                        <div class="circular-gauge-inner">
                            <span class="gauge-value">${vramPct}%</span>
                            <span class="gauge-label">VRAM</span>
                        </div>
                    </div>
                    ` : ''}
                    <div style="flex:1; min-width:160px;">
                        ${infoRow('VRAM tổng', vramTotal ? `${vramTotal} MB` : 'N/A')}
                        ${vramUsed != null ? infoRow('VRAM đã dùng', `${vramUsed} MB`) : ''}
                        ${g.vram_free_mb != null ? infoRow('VRAM trống', `${g.vram_free_mb} MB`) : ''}
                        ${infoRow('Nhiệt độ', tempBadge(g.temp_c))}
                        ${g.fan_speed_pct != null ? infoRow('Quạt', `${g.fan_speed_pct}%`) : ''}
                    </div>
                    <div style="flex:1; min-width:160px;">
                        ${g.power_draw_w != null ? infoRow('Công suất', `${g.power_draw_w} W / ${g.power_limit_w ?? '?'} W`) : ''}
                        ${g.clock_graphics_mhz != null ? infoRow('Xung GPU', `${g.clock_graphics_mhz} MHz`) : ''}
                        ${g.clock_mem_mhz != null ? infoRow('Xung VRAM', `${g.clock_mem_mhz} MHz`) : ''}
                        ${infoRow('Driver', g.driver)}
                        ${infoRow('Độ phân giải', g.resolution)}
                        ${infoRow('Video Processor', g.video_processor)}
                    </div>
                </div>
                ${vramPct != null ? `
                <div style="margin-top:8px;">
                    <span class="info-label">VRAM Usage</span>
                    <div class="progress-bar" style="margin-top:6px;">
                        <div class="progress-fill ${vramClass}" style="width:${vramPct}%"></div>
                    </div>
                    <div style="display:flex; justify-content:space-between; margin-top:4px;">
                        <span class="small text-muted">${vramUsed} MB used</span>
                        <span class="small text-muted">${vramTotal} MB total</span>
                    </div>
                </div>
                ` : ''}
            </div>
        `;
    }).join('');
}

// ===== Render Disk =====
function renderDisk(data) {
    const el = document.getElementById('disk-content');
    const disks = data.disks || { physical: [], logical: [], io: {} };
    const physical = disks.physical || [];
    const logical = disks.logical || [];
    const io = disks.io || {};

    let html = '';

    // Disk I/O speed
    if (io.read_mb_s != null || io.write_mb_s != null) {
        html += `
            <div class="io-speed-bar">
                <div class="io-speed-item">
                    <i class="fa-solid fa-arrow-down text-success"></i>
                    <span class="io-label">Đọc</span>
                    <span class="io-value">${io.read_mb_s ?? 0} MB/s</span>
                </div>
                <div class="io-speed-item">
                    <i class="fa-solid fa-arrow-up text-warning"></i>
                    <span class="io-label">Ghi</span>
                    <span class="io-value">${io.write_mb_s ?? 0} MB/s</span>
                </div>
                ${nio.total_recv_gb != null ? `
                <div class="io-speed-item">
                    <i class="fa-solid fa-database text-muted"></i>
                    <span class="io-label">Tổng nhận</span>
                    <span class="io-value">${nio.total_recv_gb} GB</span>
                </div>
                <div class="io-speed-item">
                    <i class="fa-solid fa-database text-muted"></i>
                    <span class="io-label">Tổng gửi</span>
                    <span class="io-value">${nio.total_sent_gb} GB</span>
                </div>
                ` : ''}
                ${nio.errors_in != null ? `
                <div class="io-speed-item">
                    <i class="fa-solid fa-triangle-exclamation text-danger"></i>
                    <span class="io-label">Lỗi In/Out</span>
                    <span class="io-value">${nio.errors_in} / ${nio.errors_out}</span>
                </div>
                ` : ''}
            </div>
            <div class="chart-container" style="height:120px; margin-bottom:20px;">
                <canvas id="disk-io-chart"></canvas>
            </div>
        `;
    }

    if (physical.length) {
        html += `<h3 class="subsection-title"><i class="fa-solid fa-hard-drive"></i> Ổ cứng vật lý</h3>`;
        html += physical.map(d => `
            <div class="component-item">
                ${infoRow('Model', `<span style="font-family:Inter;">${d.model}</span>`)}
                ${infoRow('Giao diện', d.interface)}
                ${infoRow('Loại phương tiện', d.media_type)}
                ${infoRow('Dung lượng', `${d.size_gb} GB`)}
                ${infoRow('Số phân vùng', d.partitions)}
                ${infoRow('Bytes/Sector', d.bytes_per_sector)}
                ${infoRow('Firmware', d.firmware)}
                ${infoRow('Serial', `<span class="small">${d.serial}</span>`)}
            </div>
        `).join('');
    }

    if (logical.length) {
        html += `<h3 class="subsection-title" style="margin-top:24px;"><i class="fa-solid fa-folder-open"></i> Ổ logic</h3>`;
        html += logical.map(ld => {
            const usedPct = ld.total_gb ? Math.round((ld.used_gb / ld.total_gb) * 100) : 0;
            const pClass = usedPct >= 90 ? 'danger' : usedPct >= 75 ? 'warning' : '';
            const volLabel = ld.volume_name ? ` (${ld.volume_name})` : '';
            return `
                <div class="component-item">
                    <div style="display:flex; justify-content:space-between; margin-bottom:8px;">
                        <strong>${ld.device_id}${volLabel}</strong>
                        <span class="badge badge-muted">${ld.filesystem}</span>
                    </div>
                    <div class="info-row">
                        <span class="info-label">Đã dùng / Tổng</span>
                        <span class="info-value">${ld.used_gb} GB / ${ld.total_gb} GB</span>
                    </div>
                    <div class="info-row">
                        <span class="info-label">Trống</span>
                        <span class="info-value">${ld.free_gb} GB (${100 - usedPct}%)</span>
                    </div>
                    <div class="progress-bar" style="margin-top:8px;">
                        <div class="progress-fill ${pClass}" style="width:${usedPct}%"></div>
                    </div>
                    <div style="display:flex; justify-content:space-between; margin-top:4px;">
                        <span class="small text-muted">${usedPct}% đã dùng</span>
                        <span class="small text-muted">${ld.free_gb} GB trống</span>
                    </div>
                </div>
            `;
        }).join('');
    }

    if (!html) html = '<p class="text-muted">Không có thông tin ổ cứng</p>';
    el.innerHTML = html;

    updateDiskIoChart();
}

// ===== Render Network =====
function renderNetwork(data) {
    const el = document.getElementById('network-content');
    const nets = data.network || [];
    const nio = data.network_io || {};

    let html = '';

    // Network I/O speed
    if (nio.sent_mb_s != null || nio.recv_mb_s != null) {
        html += `
            <div class="io-speed-bar">
                <div class="io-speed-item">
                    <i class="fa-solid fa-arrow-down text-success"></i>
                    <span class="io-label">Nhận</span>
                    <span class="io-value">${nio.recv_mb_s ?? 0} MB/s</span>
                </div>
                <div class="io-speed-item">
                    <i class="fa-solid fa-arrow-up text-warning"></i>
                    <span class="io-label">Gửi</span>
                    <span class="io-value">${nio.sent_mb_s ?? 0} MB/s</span>
                </div>
                ${nio.total_recv_gb != null ? `
                <div class="io-speed-item">
                    <i class="fa-solid fa-database text-muted"></i>
                    <span class="io-label">Tổng nhận</span>
                    <span class="io-value">${nio.total_recv_gb} GB</span>
                </div>
                <div class="io-speed-item">
                    <i class="fa-solid fa-database text-muted"></i>
                    <span class="io-label">Tổng gửi</span>
                    <span class="io-value">${nio.total_sent_gb} GB</span>
                </div>
                ` : ''}
                ${nio.errors_in != null ? `
                <div class="io-speed-item">
                    <i class="fa-solid fa-triangle-exclamation text-danger"></i>
                    <span class="io-label">Lỗi In/Out</span>
                    <span class="io-value">${nio.errors_in} / ${nio.errors_out}</span>
                </div>
                ` : ''}
            </div>
            <div class="chart-container" style="height:120px; margin-bottom:20px;">
                <canvas id="net-chart"></canvas>
            </div>
        `;
    }

    if (nets.length) {
        html += nets.map(n => {
            const hasIp = n.ips && n.ips.length;
            const statusClass = hasIp ? 'badge-success' : 'badge-muted';
            const statusText = hasIp ? 'Kết nối' : 'Không kết nối';
            return `
                <div class="component-item">
                    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:12px;">
                        <h3 style="margin:0; font-size:0.95rem;">${n.description}</h3>
                        <span class="badge ${statusClass}">${statusText}</span>
                    </div>
                    ${infoRow('MAC', n.mac)}
                    ${infoRow('IP', (n.ips || []).join(', ') || 'N/A')}
                    ${infoRow('Subnet', (n.subnet || []).join(', ') || 'N/A')}
                    ${infoRow('Gateway', (n.gateway || []).join(', ') || 'N/A')}
                    ${infoRow('DNS', (n.dns || []).join(', ') || 'N/A')}
                    ${infoRow('DHCP', n.dhcp ? '<span class="text-success">Bật</span>' : '<span class="text-muted">Tắt</span>')}
                    ${n.dhcp_server && n.dhcp_server !== 'N/A' ? infoRow('DHCP Server', n.dhcp_server) : ''}
                </div>
            `;
        }).join('');
    } else {
        html += '<p class="text-muted">Không có thông tin mạng</p>';
    }

    el.innerHTML = html;
    updateNetChart();
}

// ===== Render Battery =====
function renderBattery(data) {
    const el = document.getElementById('battery-content');
    const bat = data.battery;
    if (!bat) { el.innerHTML = '<p class="text-muted">Không có pin (máy để bàn)</p>'; return; }

    const charge = bat.charge_percent ?? 0;
    const wear = bat.wear_level_percent;
    const designStr = (bat.design_mwh && bat.design_mwh > 0) ? `${bat.design_mwh} mWh` : 'N/A';
    const fullStr = (bat.full_mwh && bat.full_mwh > 0) ? `${bat.full_mwh} mWh` : 'N/A';
    const secsleft = bat.secsleft;
    const maxReasonable = 86400 * 365;
    const timeLeftStr = (secsleft != null && secsleft > 0 && secsleft < maxReasonable)
        ? (secsleft < 3600 ? `~${Math.round(secsleft / 60)} phút` : `~${Math.round(secsleft / 3600)} giờ`)
        : null;

    // Health color
    const wearClass = wear != null ? (wear > 30 ? 'text-danger' : wear > 15 ? 'text-warning' : 'text-success') : '';
    const chargeColor = charge >= 60 ? '#22c55e' : charge >= 30 ? '#f59e0b' : '#ef4444';

    // Efficiency if both capacities known
    let efficiencyHtml = '';
    if (bat.design_mwh > 0 && bat.full_mwh > 0) {
        const eff = Math.round((bat.full_mwh / bat.design_mwh) * 100);
        const effClass = eff >= 80 ? 'text-success' : eff >= 60 ? 'text-warning' : 'text-danger';
        efficiencyHtml = infoRow('Hiệu suất pin', `<span class="${effClass}">${eff}%</span>`);
    }

    el.innerHTML = `
        <div class="gauge-wrapper">
            <div class="circular-gauge" style="--percent: ${charge}; --gauge-color: ${chargeColor};">
                <div class="circular-gauge-inner">
                    <span class="gauge-value">${charge}%</span>
                    <span class="gauge-label">Pin</span>
                </div>
            </div>
            <div style="flex:1;">
                ${infoRow('Tên pin', bat.name)}
                ${infoRow('Trạng thái', `<span class="${bat.power_plugged ? 'text-success' : 'text-warning'}">${bat.status}</span>`)}
                ${bat.design_mwh > 0 ? infoRow('Dung lượng thiết kế', designStr) : ''}
                ${bat.full_mwh > 0 ? infoRow('Dung lượng đầy hiện tại', fullStr) : ''}
                ${efficiencyHtml}
                ${wear != null ? infoRow('Độ chai pin', `<span class="${wearClass}">${wear}%</span>`) : ''}
                ${timeLeftStr && !bat.power_plugged ? infoRow('Thời gian còn lại', timeLeftStr) : ''}
            </div>
        </div>
        <div class="progress-bar" style="margin-top:16px;">
            <div class="progress-fill ${charge < 20 ? 'danger' : charge < 40 ? 'warning' : ''}" style="width:${charge}%; background: linear-gradient(90deg, ${chargeColor}, ${chargeColor}aa);"></div>
        </div>
        <div style="display:flex; justify-content:space-between; margin-top:4px;">
            <span class="small text-muted">${bat.power_plugged ? '⚡ Đang sạc' : '🔋 Đang dùng pin'}</span>
            <span class="small text-muted">${charge}%</span>
        </div>
    `;
}

// ===== Render OS =====
function renderOS(data) {
    const el = document.getElementById('os-content');
    const os = data.os;
    if (!os) { el.innerHTML = '<p class="text-muted">Không có thông tin hệ điều hành</p>'; return; }

    const uptimeStr = formatUptime(os.uptime_seconds);

    // Parse WMI date format: "20231015123456.000000+420" -> readable
    function parseWmiDate(wmiStr) {
        if (!wmiStr || wmiStr === 'N/A') return 'N/A';
        try {
            const y = wmiStr.substring(0, 4);
            const mo = wmiStr.substring(4, 6);
            const d = wmiStr.substring(6, 8);
            const h = wmiStr.substring(8, 10);
            const mi = wmiStr.substring(10, 12);
            return `${d}/${mo}/${y} ${h}:${mi}`;
        } catch { return wmiStr; }
    }

    el.innerHTML = `
        ${infoRow('Tên hệ điều hành', `<span style="font-family:Inter;">${os.name}</span>`)}
        ${infoRow('Phiên bản', os.display_version || 'N/A')}
        ${infoRow('Build', os.build)}
        ${infoRow('Kiến trúc', os.arch)}
        ${infoRow('Tên máy', os.node_name)}
        ${infoRow('Người dùng đăng ký', os.registered_user || 'N/A')}
        ${infoRow('Thời gian hoạt động', uptimeStr)}
        ${os.last_boot && os.last_boot !== 'N/A' ? infoRow('Khởi động lần cuối', parseWmiDate(os.last_boot)) : ''}
        ${os.install_date && os.install_date !== 'N/A' ? infoRow('Ngày cài đặt', parseWmiDate(os.install_date)) : ''}
        ${os.total_visible_memory_mb ? infoRow('RAM khả dụng (OS)', `${Math.round(os.total_visible_memory_mb / 1024)} MB`) : ''}
    `;
}

// ===== Render Processes =====
function renderProcesses(data) {
    const el = document.getElementById('processes-content');
    const procs = data.processes;
    if (!procs) { el.innerHTML = '<p class="text-muted">Không có dữ liệu tiến trình</p>'; return; }

    const list = currentProcTab === 'cpu' ? procs.top_cpu : procs.top_mem;
    const sortLabel = currentProcTab === 'cpu' ? 'CPU %' : 'RAM (MB)';

    el.innerHTML = `
        <div style="display:flex; align-items:center; gap:12px; margin-bottom:16px;">
            <span class="text-muted small">Tổng số tiến trình:</span>
            <span class="badge badge-accent">${procs.total}</span>
            <span class="text-muted small" style="margin-left:auto;">Sắp xếp theo ${sortLabel}</span>
        </div>
        <div class="info-table-wrapper">
        <table class="info-table proc-table">
            <thead>
                <tr>
                    <th>PID</th>
                    <th>Tên tiến trình</th>
                    <th>CPU %</th>
                    <th>RAM (MB)</th>
                    <th>Trạng thái</th>
                    <th>Người dùng</th>
                </tr>
            </thead>
            <tbody>
                ${list.map(p => {
                    const cpuClass = p.cpu_percent >= 50 ? 'text-danger' : p.cpu_percent >= 20 ? 'text-warning' : '';
                    const memClass = p.mem_mb >= 1000 ? 'text-warning' : '';
                    const statusBadge = p.status === 'running' ? 'badge-success' : 'badge-muted';
                    return `
                        <tr>
                            <td class="text-muted small">${p.pid}</td>
                            <td><strong>${p.name}</strong></td>
                            <td><span class="${cpuClass}">${p.cpu_percent}%</span></td>
                            <td><span class="${memClass}">${p.mem_mb}</span></td>
                            <td><span class="badge ${statusBadge}" style="font-size:0.75rem;">${p.status}</span></td>
                            <td class="text-muted small">${p.username || '-'}</td>
                        </tr>
                    `;
                }).join('')}
            </tbody>
        </table>
        </div>
    `;
}

// ===== Charts =====
function buildLineChart(canvas, datasets, yMax = 100) {
    const ctx = canvas.getContext('2d');
    return new Chart(ctx, {
        type: 'line',
        data: {
            labels: Array(HISTORY_LENGTH).fill(''),
            datasets
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            animation: { duration: 200 },
            plugins: { legend: { display: datasets.length > 1, labels: { color: '#94a3b8', boxWidth: 12, font: { size: 11 } } } },
            scales: {
                y: { min: 0, max: yMax, grid: { color: 'rgba(255,255,255,0.04)' }, ticks: { color: '#94a3b8', font: { size: 10 } } },
                x: { display: false }
            }
        }
    });
}

function updateCpuChart() {
    const canvas = document.getElementById('cpu-chart');
    if (!canvas) return;
    if (cpuChart) cpuChart.destroy();
    cpuChart = buildLineChart(canvas, [{
        label: 'CPU %',
        data: [...cpuHistory],
        borderColor: '#06b6d4',
        backgroundColor: 'rgba(6,182,212,0.1)',
        fill: true, tension: 0.3, pointRadius: 0,
    }]);
}

function updateRamChart() {
    const canvas = document.getElementById('ram-chart');
    if (!canvas) return;
    if (ramChart) ramChart.destroy();
    ramChart = buildLineChart(canvas, [{
        label: 'RAM %',
        data: [...ramHistory],
        borderColor: '#a855f7',
        backgroundColor: 'rgba(168,85,247,0.1)',
        fill: true, tension: 0.3, pointRadius: 0,
    }]);
}

function updateNetChart() {
    const canvas = document.getElementById('net-chart');
    if (!canvas) return;
    if (netChart) netChart.destroy();
    const maxVal = Math.max(...netSentHistory, ...netRecvHistory, 1);
    netChart = buildLineChart(canvas, [
        {
            label: 'Nhận (MB/s)',
            data: [...netRecvHistory],
            borderColor: '#22c55e',
            backgroundColor: 'rgba(34,197,94,0.1)',
            fill: true, tension: 0.3, pointRadius: 0,
        },
        {
            label: 'Gửi (MB/s)',
            data: [...netSentHistory],
            borderColor: '#f59e0b',
            backgroundColor: 'rgba(245,158,11,0.1)',
            fill: true, tension: 0.3, pointRadius: 0,
        }
    ], Math.ceil(maxVal * 1.2) || 1);
}

function updateDiskIoChart() {
    const canvas = document.getElementById('disk-io-chart');
    if (!canvas) return;
    if (diskIoChart) diskIoChart.destroy();
    const maxVal = Math.max(...diskReadHistory, ...diskWriteHistory, 1);
    diskIoChart = buildLineChart(canvas, [
        {
            label: 'Đọc (MB/s)',
            data: [...diskReadHistory],
            borderColor: '#06b6d4',
            backgroundColor: 'rgba(6,182,212,0.1)',
            fill: true, tension: 0.3, pointRadius: 0,
        },
        {
            label: 'Ghi (MB/s)',
            data: [...diskWriteHistory],
            borderColor: '#ef4444',
            backgroundColor: 'rgba(239,68,68,0.1)',
            fill: true, tension: 0.3, pointRadius: 0,
        }
    ], Math.ceil(maxVal * 1.2) || 1);
}

function pushHistory() {
    if (!currentData) return;
    const cpuPct = currentData.cpu?.percent ?? 0;
    const ramPct = currentData.ram_summary?.percent ?? currentData.ram?.percent ?? 0;
    const netSent = currentData.network_io?.sent_mb_s ?? 0;
    const netRecv = currentData.network_io?.recv_mb_s ?? 0;
    const diskRead = currentData.disks?.io?.read_mb_s ?? 0;
    const diskWrite = currentData.disks?.io?.write_mb_s ?? 0;

    const push = (arr, val) => { arr.push(val); if (arr.length > HISTORY_LENGTH) arr.shift(); };
    push(cpuHistory, cpuPct);
    push(ramHistory, ramPct);
    push(netSentHistory, netSent);
    push(netRecvHistory, netRecv);
    push(diskReadHistory, diskRead);
    push(diskWriteHistory, diskWrite);
}

// ===== Main render =====
function render(data) {
    currentData = data;
    pushHistory();

    const node = data.os?.node_name || data.os?.name || 'Unknown';
    document.getElementById('node-name').innerText = node;
    document.getElementById('last-updated').innerText = data.timestamp
        ? new Date(data.timestamp).toLocaleTimeString('vi-VN')
        : '--';

    // Uptime badge in header
    const uptimeBadge = document.getElementById('uptime-badge');
    if (uptimeBadge && data.os?.uptime_seconds) {
        uptimeBadge.textContent = '⏱ ' + formatUptime(data.os.uptime_seconds);
    }

    renderCPU(data);
    renderRAM(data);
    renderGPU(data);
    renderDisk(data);
    renderNetwork(data);
    renderBattery(data);
    renderOS(data);
    renderProcesses(data);
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
    initProcessTabs();
    initCopyButtons();
    initRefresh();
    poll();
    setInterval(poll, 3000);
});
