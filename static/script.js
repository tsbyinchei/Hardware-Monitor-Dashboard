async function updateDashboard() {
    try {
        const res = await fetch('/api/detailed_stats');
        const data = await res.json();

        // Cập nhật Header
        document.getElementById('node-name').innerText = `Máy: ${data.hw.node_name} (${data.hw.manufacturer} ${data.hw.model})`;

        // 1. Cập nhật Gauges (Biểu đồ vòng)
        document.getElementById('cpu-gauge').style.setProperty('--percent', data.cpu_percent);
        document.getElementById('cpu-gauge-val').innerText = `${data.cpu_percent}%`;

        document.getElementById('ram-gauge').style.setProperty('--percent', data.ram.percent);
        document.getElementById('ram-gauge-val').innerText = `${data.ram.percent}%`;

        // GPU Gauge (Lấy load của GPU rời đầu tiên nếu có)
        let primaryGpuLoad = data.gpus.length > 0 ? data.gpus[0].load : 0;
        document.getElementById('gpu-gauge').style.setProperty('--percent', primaryGpuLoad);
        document.getElementById('gpu-gauge-val').innerText = `${primaryGpuLoad}%`;

        // 2. Hệ điều hành & Phần cứng
        document.getElementById('os-hw-content').innerHTML = `
            <div class="detail-row"><span class="lbl">Loại máy:</span> <span class="val">${data.hw.system_family}</span></div>
            <div class="detail-row"><span class="lbl">Service Tag/Serial:</span> <span class="val text-danger">${data.hw.service_tag}</span></div>
            <div class="detail-row"><span class="lbl">Hệ điều hành:</span> <span class="val">${data.os.name}</span></div>
            <div class="detail-row"><span class="lbl">Phiên bản (Display Ver):</span> <span class="val text-success">${data.os.display_version} (Build ${data.os.build})</span></div>
            <div class="detail-row"><span class="lbl">Kiến trúc:</span> <span class="val">${data.os.arch}</span></div>
            <div class="detail-row"><span class="lbl">Mainboard:</span> <span class="val">${data.hw.board_maker} ${data.hw.board_model}</span></div>
            <div class="detail-row"><span class="lbl">BIOS:</span> <span class="val">${data.hw.bios_version}</span></div>
        `;

        // 3. Pin (Chỉ hiện nếu là Laptop)
        if (data.battery) {
            document.getElementById('battery-box').style.display = 'block';
            
            // Đánh giá tình trạng pin
            let wearColor = data.battery.wear_level > 20 ? 'text-danger' : 'text-success';
            let statusText = data.battery.status === 2 ? "Đang sạc / Cắm điện" : "Đang dùng pin";

            document.getElementById('battery-content').innerHTML = `
                <div class="detail-row"><span class="lbl">Tên Pin:</span> <span class="val">${data.battery.name}</span></div>
                <div class="detail-row"><span class="lbl">Trạng thái:</span> <span class="val">${statusText} (${data.battery.charge_percent}%)</span></div>
                <div class="detail-row"><span class="lbl">Dung lượng thiết kế:</span> <span class="val">${data.battery.design_mwh} mWh</span></div>
                <div class="detail-row"><span class="lbl">Dung lượng thực tế:</span> <span class="val">${data.battery.full_mwh} mWh</span></div>
                <div class="detail-row"><span class="lbl">Độ chai pin (Wear Level):</span> <span class="val ${wearColor}">${data.battery.wear_level}%</span></div>
            `;
        }

        // 4. Multi-GPU
        let gpuHtml = '';
        data.gpus.forEach((gpu, idx) => {
            gpuHtml += `
                <div style="${idx > 0 ? 'margin-top: 15px; padding-top: 15px; border-top: 1px solid #eee;' : ''}">
                    <h4 class="component-header">GPU ${idx}: ${gpu.name}</h4>
                    <div class="detail-row"><span class="lbl">VRAM:</span> <span class="val">${gpu.vram}</span></div>
                    <div class="detail-row"><span class="lbl">Driver:</span> <span class="val">${gpu.driver}</span></div>
                    <div class="detail-row"><span class="lbl">Độ phân giải:</span> <span class="val">${gpu.resolution}</span></div>
                    ${gpu.temp !== "N/A" ? `<div class="detail-row"><span class="lbl">Nhiệt độ:</span> <span class="val text-danger">${gpu.temp}°C</span></div>` : ''}
                </div>
            `;
        });
        document.getElementById('gpu-content').innerHTML = gpuHtml;

        // 5. Ổ cứng (Disks)
        let diskHtml = '';
        data.disks.forEach((disk, idx) => {
            diskHtml += `
                <div style="${idx > 0 ? 'margin-top: 15px; padding-top: 15px; border-top: 1px solid #eee;' : ''}">
                    <h4 class="component-header"><i class="fa-solid fa-server"></i> ${disk.model}</h4>
                    <div class="detail-row"><span class="lbl">Giao tiếp:</span> <span class="val">${disk.interface}</span></div>
                    <div class="detail-row"><span class="lbl">Dung lượng:</span> <span class="val">${disk.size} GB</span></div>
                    <div class="detail-row"><span class="lbl">Serial:</span> <span class="val">${disk.serial}</span></div>
                </div>
            `;
        });
        document.getElementById('disk-content').innerHTML = diskHtml;

    } catch (e) {
        console.error("Fetch error:", e);
    }
}

updateDashboard();
setInterval(updateDashboard, 1000);