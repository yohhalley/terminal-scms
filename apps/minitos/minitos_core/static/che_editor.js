(function () {
    "use strict";

    const root = document.getElementById("cheEditor");
    if (!root) return;
    const $ = (id) => document.getElementById(id);
    const locationId = Number(root.dataset.locationId);
    const state = { ches: [], equipmentTypes: [], assets: [], axes: [], componentTypes: [], selectedCheId: null, components: [], selectedComponentId: null };
    const FIXED_RAIL_CODES = new Set(["STS", "QC", "RMG", "ARMG", "ASC", "RGC", "ARTG"]);

    function csrfToken() {
        return document.cookie.split(";").map((value) => value.trim()).find((value) => value.startsWith("csrftoken="))?.split("=")[1] || "";
    }

    async function api(path, options = {}) {
        const response = await fetch(path, { credentials: "same-origin", ...options, headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken(), ...(options.headers || {}) } });
        if (response.status === 204) return null;
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
            const detail = data.detail || data.error || Object.entries(data).map(([key, value]) => `${key}: ${Array.isArray(value) ? value.join(", ") : value}`).join("; ");
            throw new Error(detail || `${response.status} ${response.statusText}`);
        }
        return data.results || data;
    }

    function setStatus(message, error = false) {
        $("cheStatus").textContent = message;
        $("cheStatus").style.color = error ? "#ff9999" : "";
    }

    function fillSelect(id, items, value, emptyLabel) {
        const select = $(id); select.replaceChildren();
        if (emptyLabel !== undefined) select.append(new Option(emptyLabel, ""));
        items.forEach((item) => select.append(new Option(item.label, item.id, false, String(item.id) === String(value ?? ""))));
    }

    function selectedChe() { return state.ches.find((item) => item.pk === state.selectedCheId) || null; }
    function selectedComponent() { return state.components.find((item) => item.id === state.selectedComponentId) || null; }
    function number(id, fallback = 0) { const value = Number($(id).value); return Number.isFinite(value) ? value : fallback; }
    function nullableNumber(id) { const value = $(id).value.trim(); return value === "" ? null : Number(value); }
    function escapeHtml(value) { const element = document.createElement("div"); element.textContent = value; return element.innerHTML; }

    function geographicDistance(first, second) {
        const radians = Math.PI / 180; const lat1 = first[1] * radians; const lat2 = second[1] * radians;
        const dLat = (second[1] - first[1]) * radians; const dLon = (second[0] - first[0]) * radians;
        const value = Math.sin(dLat / 2) ** 2 + Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLon / 2) ** 2;
        return 6371008.8 * 2 * Math.atan2(Math.sqrt(value), Math.sqrt(1 - value));
    }

    function axisLength(axis) {
        return (axis?.points || []).slice(1).reduce((total, point, index) => total + geographicDistance(axis.points[index], point), 0);
    }

    function railMode(type) {
        if (FIXED_RAIL_CODES.has(type?.equipment_type)) return "fixed";
        return type?.equipment_type === "RTG" ? "repositionable" : "free";
    }

    function assetAncestors(assetId) {
        const values = new Set(); let asset = state.assets.find((item) => item.id === Number(assetId));
        while (asset && !values.has(asset.id)) { values.add(asset.id); asset = state.assets.find((item) => item.id === asset.parent); }
        return values;
    }

    function relatedAssets(first, second) {
        return assetAncestors(first).has(Number(second)) || assetAncestors(second).has(Number(first));
    }

    function eligibleRailAxes() {
        const type = state.equipmentTypes.find((item) => item.id === Number($("detailCheType").value));
        const assetId = Number($("detailCheAsset").value);
        const kinds = ["crane_rail"];
        if (["STS", "QC"].includes(type?.equipment_type)) kinds.push("quay_face");
        else if (railMode(type) === "fixed") kinds.push("rail_track");
        return state.axes.filter((axis) => axis.is_enabled !== false && kinds.includes(axis.axis_kind) && relatedAssets(axis.asset, assetId));
    }

    function synchronizeRailPosition() {
        const length = Number($("detailRailPosition").max || 0);
        const low = Math.max(0, Math.min(length, number("detailRailMin")));
        const high = Math.max(low, Math.min(length, number("detailRailMax", length)));
        const position = Math.max(low, Math.min(high, number("detailRailPosition", low)));
        $("detailRailMin").value = low.toFixed(1); $("detailRailMax").value = high.toFixed(1);
        $("detailRailPosition").value = position.toFixed(1);
        $("detailRailPositionRange").min = low; $("detailRailPositionRange").max = high; $("detailRailPositionRange").value = position;
        $("detailRailLength").textContent = `Rail length: ${length.toFixed(1)} m | From end: ${(length - position).toFixed(1)} m`;
    }

    function refreshRailControls(assignment = null, preserve = false) {
        const type = state.equipmentTypes.find((item) => item.id === Number($("detailCheType").value));
        const section = $("railAssignmentSection");
        if (railMode(type) === "free" || !$("detailCheAsset").value) {
            section.classList.add("hidden");
            for (const id of ["detailCheLatitude", "detailCheLongitude", "detailCheRotation"]) $(id).readOnly = false;
            return;
        }
        section.classList.remove("hidden");
        for (const id of ["detailCheLatitude", "detailCheLongitude", "detailCheRotation"]) $(id).readOnly = true;
        const previousAxis = assignment?.axis_id || (preserve ? $("detailRailAxis").value : null);
        const axes = eligibleRailAxes();
        fillSelect("detailRailAxis", axes.map((axis) => ({ id: axis.id, label: `${axis.code} - ${axis.name}` })), previousAxis, "Select rail");
        const axis = axes.find((item) => item.id === Number($("detailRailAxis").value));
        const length = axisLength(axis);
        const priorLow = preserve ? nullableNumber("detailRailMin") : null;
        const priorHigh = preserve ? nullableNumber("detailRailMax") : null;
        const priorPosition = preserve ? nullableNumber("detailRailPosition") : null;
        $("detailRailPosition").max = length; $("detailRailMin").max = length; $("detailRailMax").max = length;
        $("detailRailMin").value = assignment?.min_position_m ?? priorLow ?? 0;
        $("detailRailMax").value = assignment?.max_position_m ?? priorHigh ?? length;
        $("detailRailPosition").value = assignment?.position_m ?? priorPosition ?? 0;
        const disabled = !axis;
        for (const id of ["detailRailPosition", "detailRailPositionRange", "detailRailMin", "detailRailMax"]) $(id).disabled = disabled;
        synchronizeRailPosition();
    }

    function renderFleet() {
        $("fleetCount").textContent = `${state.ches.length} equipment`;
        $("fleetList").innerHTML = state.ches.length ? state.ches.map((che) => `<button class="fleet-item ${che.pk === state.selectedCheId ? "active" : ""}" data-che-id="${che.pk}" type="button"><span class="indicator ${che.powered_on ? "on" : ""}"></span><span class="item-copy"><strong>${escapeHtml(che.name)}</strong><small>${escapeHtml(che.equipment_type_name || "CHE")}</small></span><span class="item-meta">${che.working ? "WK" : che.powered_on ? "IL" : "OF"}</span></button>`).join("") : '<div class="empty">No equipment</div>';
        document.querySelectorAll("[data-che-id]").forEach((button) => button.addEventListener("click", () => selectChe(Number(button.dataset.cheId))));
    }

    function resetCheForm() {
        state.selectedCheId = null; state.components = []; state.selectedComponentId = null;
        $("cheDetailForm").reset(); $("detailCheRotation").value = 0; $("detailFuel").value = 100; $("detailRunningHours").value = 0; $("detailMaintenanceHours").value = 0;
        $("cheEditorTitle").textContent = "New equipment"; $("newComponent").disabled = true;
        refreshRailControls();
        renderFleet(); renderComponents(); resetComponentForm(false);
    }

    function formatLocalDateTime(value) {
        if (!value) return "";
        const date = new Date(value); const offset = date.getTimezoneOffset() * 60000;
        return new Date(date.getTime() - offset).toISOString().slice(0, 16);
    }

    async function selectChe(id) {
        state.selectedCheId = id; state.selectedComponentId = null;
        const che = selectedChe(); if (!che) return;
        $("cheEditorTitle").textContent = che.name; $("detailCheName").value = che.name; $("detailCheType").value = che.equipment_type; $("detailCheAsset").value = che.current_asset || "";
        $("detailCheLatitude").value = che.latitude ?? ""; $("detailCheLongitude").value = che.longitude ?? ""; $("detailCheRotation").value = che.rotation ?? 0; $("detailLastMaintenance").value = formatLocalDateTime(che.last_maintenance);
        $("detailPowered").checked = che.powered_on; $("detailWorking").checked = che.working; $("detailAvailable").checked = che.is_available; $("detailDispatched").checked = che.is_dispatched; $("detailMaintenance").checked = che.is_in_maintenance; $("detailTwistlock").checked = che.twistlock_closed;
        $("detailFuel").value = che.fuel_level ?? 100; $("detailRunningHours").value = che.running_hours ?? 0; $("detailMaintenanceHours").value = che.running_hours_since_last_maintenance ?? 0;
        refreshRailControls(che.rail_assignment);
        $("newComponent").disabled = false; renderFleet(); resetComponentForm(false);
        try {
            state.components = await api(`/minitos_core/locations/${locationId}/ches/${id}/components/`);
            renderComponents();
        } catch (error) { setStatus(error.message, true); }
    }

    async function saveChe(event) {
        event.preventDefault();
        const name = $("detailCheName").value.trim(); const type = Number($("detailCheType").value);
        if (!name || !type) return setStatus("Equipment name and type are required.", true);
        const lastMaintenance = $("detailLastMaintenance").value;
        const payload = { name, equipment_type: type, current_asset: $("detailCheAsset").value || null, latitude: nullableNumber("detailCheLatitude"), longitude: nullableNumber("detailCheLongitude"), rotation: number("detailCheRotation"), powered_on: $("detailPowered").checked, working: $("detailWorking").checked, is_available: $("detailAvailable").checked, is_dispatched: $("detailDispatched").checked, is_in_maintenance: $("detailMaintenance").checked, twistlock_closed: $("detailTwistlock").checked, fuel_level: number("detailFuel", 100), running_hours: number("detailRunningHours"), running_hours_since_last_maintenance: number("detailMaintenanceHours"), last_maintenance: lastMaintenance ? new Date(lastMaintenance).toISOString() : null };
        if (!$("railAssignmentSection").classList.contains("hidden")) {
            if (!$("detailRailAxis").value) return setStatus("Select a movement rail.", true);
            payload.rail_axis = Number($("detailRailAxis").value);
            payload.rail_position_m = number("detailRailPosition");
            payload.rail_min_position_m = number("detailRailMin");
            payload.rail_max_position_m = number("detailRailMax");
        }
        const editing = selectedChe();
        try {
            const saved = await api(`/minitos_core/locations/${locationId}/ches/${editing ? `${editing.pk}/` : ""}`, { method: editing ? "PATCH" : "POST", body: JSON.stringify(payload) });
            await loadFleet(); await selectChe(saved.pk); setStatus(`${saved.name} saved.`);
        } catch (error) { setStatus(error.message, true); }
    }

    async function deleteChe() {
        const che = selectedChe(); if (!che || !window.confirm(`Delete ${che.name}?`)) return;
        try { await api(`/minitos_core/locations/${locationId}/ches/${che.pk}/`, { method: "DELETE" }); await loadFleet(); resetCheForm(); setStatus("Equipment deleted."); }
        catch (error) { setStatus(error.message, true); }
    }

    function componentDetail(component) {
        for (const key of ["engine", "battery", "energy_tank", "motion_axis", "spreader"]) if (component?.[key]) return { key, value: component[key] };
        return { key: null, value: {} };
    }

    function detailKeyForRole(role) {
        if (role === "engine") return "engine";
        if (role === "battery") return "battery";
        if (role === "fuel_tank") return "energy_tank";
        if (["drive", "hoist", "trolley", "gantry", "boom"].includes(role)) return "motion_axis";
        if (role === "spreader") return "spreader";
        return null;
    }

    function renderComponents() {
        const che = selectedChe(); $("componentSubtitle").textContent = che ? che.name : "Select equipment";
        $("componentList").innerHTML = che ? (state.components.length ? state.components.map((component) => `<button class="component-item ${component.id === state.selectedComponentId ? "active" : ""}" data-component-id="${component.id}" type="button"><span class="indicator ${component.is_active ? "on" : ""}"></span><span class="item-copy"><strong>${escapeHtml(component.name)}</strong><small>${escapeHtml(component.component_type_details?.name || component.role)}</small></span><span class="item-meta">${escapeHtml(component.role)}</span></button>`).join("") : '<div class="empty">No components</div>') : '<div class="empty">No equipment selected</div>';
        document.querySelectorAll("[data-component-id]").forEach((button) => button.addEventListener("click", () => selectComponent(Number(button.dataset.componentId))));
    }

    function resetComponentForm(show = true) {
        state.selectedComponentId = null; $("componentForm").reset(); $("componentActive").checked = true; $("componentConfiguration").value = "{}";
        fillSelect("componentParent", state.components.map((item) => ({ id: item.id, label: `${item.role} - ${item.name}` })), null, "None");
        $("componentForm").classList.toggle("hidden", !show); renderComponents();
    }

    function selectComponent(id) {
        state.selectedComponentId = id; const component = selectedComponent(); if (!component) return;
        $("componentForm").classList.remove("hidden"); $("componentName").value = component.name; $("componentType").value = component.component_type; $("componentRole").value = component.role;
        fillSelect("componentParent", state.components.filter((item) => item.id !== id).map((item) => ({ id: item.id, label: `${item.role} - ${item.name}` })), component.parent, "None");
        $("componentManufacturer").value = component.manufacturer || ""; $("componentModel").value = component.model || ""; $("componentSerial").value = component.serial_number || ""; $("componentActive").checked = component.is_active;
        $("componentConfiguration").value = JSON.stringify(componentDetail(component).value, null, 2); renderComponents();
    }

    async function saveComponent(event) {
        event.preventDefault(); const che = selectedChe(); if (!che) return;
        const name = $("componentName").value.trim(); const role = $("componentRole").value.trim(); const type = Number($("componentType").value);
        if (!name || !role || !type) return setStatus("Component name, type, and role are required.", true);
        let configuration;
        try { configuration = JSON.parse($("componentConfiguration").value || "{}"); }
        catch (error) { return setStatus(`Configuration JSON is invalid: ${error.message}`, true); }
        const editing = selectedComponent();
        const payload = { component_type: type, parent: $("componentParent").value || null, role, name, manufacturer: $("componentManufacturer").value.trim(), model: $("componentModel").value.trim(), serial_number: $("componentSerial").value.trim(), is_active: $("componentActive").checked, metadata: editing?.metadata || {} };
        const detailKey = detailKeyForRole(role); if (detailKey) payload[detailKey] = configuration;
        try {
            const path = `/minitos_core/locations/${locationId}/ches/${che.pk}/components/${editing ? `${editing.id}/` : ""}`;
            const saved = await api(path, { method: editing ? "PATCH" : "POST", body: JSON.stringify(payload) });
            state.components = await api(`/minitos_core/locations/${locationId}/ches/${che.pk}/components/`); selectComponent(saved.id); setStatus(`${saved.name} saved.`);
        } catch (error) { setStatus(error.message, true); }
    }

    async function deleteComponent() {
        const che = selectedChe(); const component = selectedComponent(); if (!che || !component || !window.confirm(`Delete ${component.name}?`)) return;
        try { await api(`/minitos_core/locations/${locationId}/ches/${che.pk}/components/${component.id}/`, { method: "DELETE" }); state.components = await api(`/minitos_core/locations/${locationId}/ches/${che.pk}/components/`); resetComponentForm(false); setStatus("Component deleted."); }
        catch (error) { setStatus(error.message, true); }
    }

    async function loadFleet() {
        state.ches = await api(`/minitos_core/locations/${locationId}/ches/`); renderFleet();
    }

    async function init() {
        try {
            const [ches, equipmentTypes, assets, axes, componentTypes] = await Promise.all([api(`/minitos_core/locations/${locationId}/ches/`), api("/minitos_core/equipment-types/"), api(`/minitos_core/locations/${locationId}/layout-assets/`), api(`/minitos_core/locations/${locationId}/linear-axes/`), api("/minitos_core/component-types/")]);
            state.ches = ches; state.equipmentTypes = equipmentTypes; state.assets = assets; state.axes = axes; state.componentTypes = componentTypes;
            fillSelect("detailCheType", equipmentTypes.map((item) => ({ id: item.id, label: `${item.name} (${item.equipment_type})` })), null, "Select type");
            fillSelect("detailCheAsset", assets.filter((item) => ["quay", "yard_block", "parking", "rail_area"].includes(item.asset_kind)).map((item) => ({ id: item.id, label: `${item.code} - ${item.name}` })), null, "Unassigned");
            fillSelect("componentType", componentTypes.map((item) => ({ id: item.id, label: item.name })), null, "Select type");
            renderFleet(); resetComponentForm(false); refreshRailControls();
        } catch (error) { setStatus(`Could not load equipment: ${error.message}`, true); }
    }

    $("newCheDetail").addEventListener("click", resetCheForm); $("cheDetailForm").addEventListener("submit", saveChe); $("deleteCheDetail").addEventListener("click", deleteChe);
    $("detailCheType").addEventListener("change", () => refreshRailControls()); $("detailCheAsset").addEventListener("change", () => refreshRailControls());
    $("detailRailAxis").addEventListener("change", () => refreshRailControls(null, true));
    $("detailRailPosition").addEventListener("input", () => { synchronizeRailPosition(); $("detailRailPositionRange").value = $("detailRailPosition").value; });
    $("detailRailPositionRange").addEventListener("input", () => { $("detailRailPosition").value = $("detailRailPositionRange").value; });
    $("detailRailMin").addEventListener("change", synchronizeRailPosition); $("detailRailMax").addEventListener("change", synchronizeRailPosition);
    $("newComponent").addEventListener("click", () => resetComponentForm(true)); $("componentForm").addEventListener("submit", saveComponent); $("deleteComponent").addEventListener("click", deleteComponent);
    if (window.lucide) lucide.createIcons();
    init();
}());
