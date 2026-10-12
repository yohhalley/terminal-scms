(function () {
    "use strict";

    const root = document.getElementById("terminalEditor");
    if (!root) return;

    const $ = (id) => document.getElementById(id);
    const locationId = Number(root.dataset.locationId || 0);
    const initialLayoutId = Number(root.dataset.layoutId || 0);
    const state = {
        location: null,
        layout: initialLayoutId ? { id: initialLayoutId } : null,
        assets: [], policies: [], axes: [], interfaces: [], nodes: [], edges: [], ches: [], scopes: [],
        equipmentTypes: [], presets: [],
        selected: null,
        tool: "select",
        draft: null,
        drawPoints: [],
        rectangleStart: null,
        blockAnchor: null,
        blockHeadingPoint: null,
        perimeterDraft: false,
        geometryEditing: false,
        draftFinalized: false,
        hoverPoint: null,
        roadSnap: null,
        roadBranchHeading: null,
        drawPointSnaps: [],
        pendingRoadInterfaceIds: new Set(),
        connectingInterfaceId: null,
        moving: null,
        configuredDrag: null,
        ignoreNextClick: false,
        createKind: null,
        clipboardAsset: null,
        duplicateMetadata: null,
        vertexMarkers: [],
    };

    const assetKinds = [
        ["land", "Land"], ["water", "Water"],
        ["quay", "Quay"], ["yard", "Yard"], ["yard_block", "Yard block"],
        ["transfer_zone", "Transfer zone"], ["parking", "Parking"],
        ["building", "Building"], ["road", "Road corridor"],
        ["rail_area", "Rail area"], ["gate_area", "Gate area"],
        ["workshop", "Workshop"], ["other", "Other"],
    ];
    const colors = {
        terminal: "#1aa69a", land: "#87966f", water: "#4e8ac7", quay: "#a8adb0",
        yard: "#c0aa70", yard_block: "#e0b94c", transfer_zone: "#e9783d",
        parking: "#6f8a91", building: "#8b776d", road: "#4d555a",
        rail_area: "#786e78", gate_area: "#5bbf72", workshop: "#9b6d55", other: "#8c979c",
    };
    const transferSides = ["top", "bottom", "left", "right"];
    const chePlacementAssetKinds = new Set(["quay", "yard_block", "parking", "rail_area"]);
    const assetCreation = {
        building: { prefix: "BLDG", label: "Building", tool: "polygon" },
        parking: { prefix: "PARK", label: "Parking", tool: "polygon" },
        yard: { prefix: "HEAP", label: "Heap Zone", tool: "polygon" },
        yard_block: { prefix: "YB", label: "Yard Block", tool: "block" },
        quay: { prefix: "QUAY", label: "Quay", tool: "block" },
        rail_area: { prefix: "RAIL", label: "Rail Area", tool: "block" },
        gate_area: { prefix: "GATE", label: "Gate Area", tool: "block" },
        transfer_zone: { prefix: "TZ", label: "Transfer Zone", tool: "polygon" },
        road: { prefix: "ROAD", label: "Road", tool: "line" },
    };
    const mapTools = window.TerminalEditorMapTools;
    let map;

    function csrfToken() {
        return document.cookie.split(";").map((v) => v.trim()).find((v) => v.startsWith("csrftoken="))?.split("=")[1] || "";
    }

    async function api(path, options = {}) {
        const response = await fetch(path, {
            credentials: "same-origin",
            ...options,
            headers: {
                "Content-Type": "application/json",
                "X-CSRFToken": csrfToken(),
                ...(options.headers || {}),
            },
        });
        if (response.status === 204) return null;
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
            const detail = data.detail || data.error || Object.entries(data).map(([key, value]) => `${key}: ${Array.isArray(value) ? value.join(", ") : value}`).join("; ");
            throw new Error(detail || `${response.status} ${response.statusText}`);
        }
        return data.results || data;
    }

    function endpoint(name, id = "") {
        return `/minitos_core/locations/${locationId}/${name}/${id ? `${id}/` : ""}`;
    }

    function setStatus(message, error = false) {
        $("status").textContent = message;
        $("status").style.color = error ? "#ff9999" : "";
    }

    function number(id, fallback = 0) {
        const value = Number($(id)?.value);
        return Number.isFinite(value) ? value : fallback;
    }

    function nullableNumber(id) {
        const value = String($(id)?.value ?? "").trim();
        return value === "" ? null : Number(value);
    }

    function deepCopy(value) {
        return value == null ? value : JSON.parse(JSON.stringify(value));
    }

    function option(value, label, selected = false) {
        const el = document.createElement("option");
        el.value = value;
        el.textContent = label;
        el.selected = selected;
        return el;
    }

    function fillSelect(id, items, value, emptyLabel) {
        const select = $(id);
        if (!select) return;
        select.replaceChildren();
        if (emptyLabel !== undefined) select.append(option("", emptyLabel));
        items.forEach((item) => select.append(option(item.id, item.label, String(item.id) === String(value ?? ""))));
    }

    function normalizeGeometry(value) {
        if (!value || !value.type || !Array.isArray(value.coordinates)) return null;
        return deepCopy(value);
    }

    function entityGeometry(type, entity) {
        if (!entity) return null;
        if (type === "asset" || type === "interface") return normalizeGeometry(entity.geometry);
        if (type === "axis") return { type: "LineString", coordinates: deepCopy(entity.points || []) };
        if (type === "node") return normalizeGeometry(entity.point);
        if (type === "edge") {
            const first = state.nodes.find((item) => item.id === entity.from_node)?.point?.coordinates;
            const second = state.nodes.find((item) => item.id === entity.to_node)?.point?.coordinates;
            return first && second ? { type: "LineString", coordinates: [first, second] } : null;
        }
        if (type === "che" && entity.longitude != null && entity.latitude != null) {
            return { type: "Point", coordinates: [Number(entity.longitude), Number(entity.latitude)] };
        }
        return null;
    }

    function geometryCoordinates(geometry) {
        if (!geometry) return [];
        if (geometry.type === "Point") return [geometry.coordinates];
        if (geometry.type === "LineString") return geometry.coordinates;
        if (geometry.type === "Polygon") return geometry.coordinates[0].slice(0, -1);
        return [];
    }

    function geometryCenter(geometry) {
        const points = geometryCoordinates(geometry);
        if (!points.length) return null;
        const sum = points.reduce((value, point) => [value[0] + Number(point[0]), value[1] + Number(point[1])], [0, 0]);
        return [sum[0] / points.length, sum[1] / points.length];
    }

    function geometryFromPoints(type, points) {
        if (type === "Point") return points.length ? { type, coordinates: points[0] } : null;
        if (type === "LineString") return points.length >= 2 ? { type, coordinates: points } : null;
        if (type === "Polygon" && points.length >= 3) return { type, coordinates: [[...points, points[0]]] };
        return null;
    }

    function midpoint(a, b) {
        return [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
    }

    function offsetLonLat(origin, eastM, northM) {
        const latRad = origin[1] * Math.PI / 180;
        return [
            origin[0] + eastM / (111320 * Math.max(Math.cos(latRad), 0.01)),
            origin[1] + northM / 110574,
        ];
    }

    function haversine(a, b) {
        const radians = Math.PI / 180;
        const dLat = (b[1] - a[1]) * radians;
        const dLon = (b[0] - a[0]) * radians;
        const lat1 = a[1] * radians;
        const lat2 = b[1] * radians;
        const h = Math.sin(dLat / 2) ** 2 + Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLon / 2) ** 2;
        return 6371008.8 * 2 * Math.atan2(Math.sqrt(h), Math.sqrt(1 - h));
    }

    function polylineLength(points) {
        return points.slice(1).reduce((sum, point, i) => sum + haversine(points[i], point), 0);
    }

    function longestSegment(geometry) {
        const points = geometryCoordinates(geometry);
        if (points.length < 2) return [];
        let best = [points[0], points[1]];
        let length = haversine(best[0], best[1]);
        const limit = geometry.type === "Polygon" ? points.length : points.length - 1;
        for (let i = 0; i < limit; i += 1) {
            const pair = [points[i], points[(i + 1) % points.length]];
            const candidate = haversine(pair[0], pair[1]);
            if (candidate > length) { best = pair; length = candidate; }
        }
        return deepCopy(best);
    }

    function setupMap() {
        const center = [
            Number(state.location?.map_origin_longitude ?? state.location?.longitude ?? 0),
            Number(state.location?.map_origin_latitude ?? state.location?.latitude ?? 0),
        ];
        map = new maplibregl.Map({
            container: "map",
            center,
            zoom: Number(state.location?.default_zoom || 15),
            style: basemapStyle($("basemapStyle")?.value || "streets"),
        });
        map.addControl(new maplibregl.NavigationControl({ showCompass: true }), "bottom-right");
        map.doubleClickZoom.disable();
        map.on("load", () => { restoreEditorLayers(); fitTerminal(false); applyMapCursor(); });
        map.on("mousemove", onMapMove);
        map.on("click", onMapClick);
        map.on("dblclick", onMapDoubleClick);
        map.on("contextmenu", onMapContextMenu);
        map.on("mousedown", beginMove);
        map.on("mouseup", endMove);
        map.on("error", (event) => console.error("Terminal map error", event.error));
        map.on("idle", () => {
            if (!map.getSource("assets")) return;
            const canvas = $("map");
            for (const [name, layers] of Object.entries({
                renderedAssets: ["asset-outline"], renderedPerimeters: ["perimeter-outline"],
                renderedYardCells: ["yard-cell-outline"], renderedTransferPoints: ["interface-point"],
                renderedCheFootprints: ["che-footprint"],
            })) canvas.dataset[name] = String(map.queryRenderedFeatures({ layers }).length);
            canvas.dataset.sourceAssets = String(map.querySourceFeatures("assets").length);
        });
    }

    function emptyCollection() { return { type: "FeatureCollection", features: [] }; }

    function basemapStyle(name) {
        return mapTools.basemapStyle(name);
    }

    function restoreEditorLayers() {
        try {
            if (!map.getSource("assets")) addMapLayers();
            refreshMap();
            applyMapCursor();
        } catch (error) {
            setStatus(`Could not render terminal layout: ${error.message}`, true);
            console.error("Terminal layout rendering failed", error);
        }
    }

    function changeBasemap() {
        if (!map) return;
        mapTools.replaceMapStyle(map, basemapStyle($("basemapStyle").value), () => {
            clearMarkers();
            restoreEditorLayers();
        });
    }

    function addMapLayer(layer) {
        if (layer.type === "symbol") {
            // OpenFreeMap does not provide MapLibre's default Open Sans glyphs.
            layer.layout = { "text-font": ["Noto Sans Regular"], ...layer.layout };
        }
        map.addLayer(layer);
    }

    function addMapLayers() {
        ["assets", "perimeters", "yard-grid", "berth-sides", "linear-markers", "axes", "edges", "nodes", "interface-geometries", "interfaces", "ches", "draft", "draw-guide", "asset-guide", "block-guide"].forEach((id) => map.addSource(id, { type: "geojson", data: emptyCollection() }));
        addMapLayer({ id: "asset-fill", type: "fill", source: "assets", filter: ["all", ["==", ["geometry-type"], "Polygon"], ["!=", ["get", "perimeter"], true]], layout: { "fill-sort-key": ["get", "surfaceOrder"] }, paint: { "fill-color": ["get", "color"], "fill-opacity": ["case", ["boolean", ["get", "selected"], false], 0.56, 0.38] } });
        addMapLayer({ id: "yard-cell-fill", type: "fill", source: "yard-grid", filter: ["==", ["get", "role"], "yard_cell"], paint: { "fill-color": "#263e48", "fill-opacity": 0.3 } });
        addMapLayer({ id: "yard-cell-outline", type: "line", source: "yard-grid", filter: ["==", ["get", "role"], "yard_cell"], paint: { "line-color": "#fff7c2", "line-width": 1.25 } });
        addMapLayer({ id: "yard-position", type: "circle", source: "yard-grid", filter: ["==", ["get", "role"], "yard_position"], minzoom: 18, paint: { "circle-radius": 2, "circle-color": "#fff7c2" } });
        addMapLayer({ id: "yard-position-label", type: "symbol", source: "yard-grid", filter: ["==", ["get", "role"], "yard_position"], minzoom: 20, layout: { "text-field": ["get", "label"], "text-size": 10 }, paint: { "text-color": "#fff", "text-halo-color": "#263e48", "text-halo-width": 2 } });
        addMapLayer({ id: "yard-grid-label", type: "symbol", source: "yard-grid", filter: ["==", ["get", "role"], "yard_label"], layout: { "text-field": ["get", "label"], "text-size": 11, "text-allow-overlap": false }, paint: { "text-color": "#fff", "text-halo-color": "#263e48", "text-halo-width": 2 } });
        addMapLayer({ id: "asset-outline-casing", type: "line", source: "assets", filter: ["!=", ["get", "perimeter"], true], paint: { "line-color": "#172c34", "line-width": 5, "line-opacity": 0.7 } });
        addMapLayer({ id: "asset-outline", type: "line", source: "assets", filter: ["!=", ["get", "perimeter"], true], paint: { "line-color": ["case", ["boolean", ["get", "selected"], false], "#fff176", ["get", "color"]], "line-width": ["case", ["boolean", ["get", "selected"], false], 4, 2.5] } });
        addMapLayer({ id: "perimeter-casing", type: "line", source: "perimeters", paint: { "line-color": "#122c35", "line-width": 6 } });
        addMapLayer({ id: "perimeter-outline", type: "line", source: "perimeters", paint: { "line-color": "#40e0cb", "line-width": 3 } });
        addMapLayer({ id: "asset-label", type: "symbol", source: "assets", filter: ["all", ["==", ["geometry-type"], "Polygon"], ["!=", ["get", "perimeter"], true]], layout: { "text-field": ["get", "label"], "text-size": 12 }, paint: { "text-color": "#fff", "text-halo-color": "#182026", "text-halo-width": 2 } });
        addMapLayer({ id: "berth-water-side", type: "line", source: "berth-sides", paint: { "line-color": "#2f9ed1", "line-width": 6, "line-dasharray": [3, 1] } });
        addMapLayer({ id: "berth-water-label", type: "symbol", source: "berth-sides", layout: { "symbol-placement": "line-center", "text-field": "WATER", "text-size": 11, "text-allow-overlap": true }, paint: { "text-color": "#e7f8ff", "text-halo-color": "#176283", "text-halo-width": 2 } });
        addMapLayer({ id: "linear-marker-point", type: "circle", source: "linear-markers", paint: { "circle-radius": 5, "circle-color": "#f2c94c", "circle-stroke-color": "#192126", "circle-stroke-width": 1.5 } });
        addMapLayer({ id: "linear-marker-label", type: "symbol", source: "linear-markers", layout: { "text-field": ["get", "label"], "text-size": 11, "text-offset": [0, 1.35], "text-allow-overlap": true }, paint: { "text-color": "#fff", "text-halo-color": "#192126", "text-halo-width": 2 } });
        addMapLayer({ id: "route-edge", type: "line", source: "edges", paint: { "line-color": ["case", ["==", ["get", "generated"], true], "#55a4d8", "#f28d52"], "line-width": 2, "line-dasharray": [2, 2] } });
        addMapLayer({ id: "axis-line", type: "line", source: "axes", paint: { "line-color": ["case", ["boolean", ["get", "selected"], false], "#fff176", ["get", "color"]], "line-width": ["case", ["boolean", ["get", "selected"], false], 6, 4] } });
        addMapLayer({ id: "axis-label", type: "symbol", source: "axes", layout: { "symbol-placement": "line-center", "text-field": ["get", "label"], "text-size": 10, "text-allow-overlap": false }, paint: { "text-color": "#fff", "text-halo-color": "#192126", "text-halo-width": 2 } });
        addMapLayer({ id: "interface-line", type: "line", source: "interface-geometries", paint: { "line-color": "#e9783d", "line-width": 4, "line-dasharray": [2, 1] } });
        addMapLayer({ id: "interface-label", type: "symbol", source: "interface-geometries", layout: { "symbol-placement": "line-center", "text-field": ["get", "label"], "text-size": 10 }, paint: { "text-color": "#fff", "text-halo-color": "#7c321d", "text-halo-width": 2 } });
        addMapLayer({ id: "interface-point", type: "circle", source: "interfaces", paint: { "circle-radius": 6, "circle-color": "#e9783d", "circle-stroke-color": "#fff", "circle-stroke-width": 1.5 } });
        addMapLayer({ id: "interface-position-label", type: "symbol", source: "interfaces", filter: ["==", ["get", "ownerSelected"], true], layout: { "text-field": ["get", "label"], "text-size": 11, "text-offset": [0, 1.35], "text-allow-overlap": false }, paint: { "text-color": "#fff", "text-halo-color": "#7c321d", "text-halo-width": 2 } });
        addMapLayer({ id: "route-node", type: "circle", source: "nodes", paint: { "circle-radius": ["case", ["in", ["get", "aggregate"], ["literal", ["asset", "interface"]]], 6, ["boolean", ["get", "generated"], false], 3, 5], "circle-color": ["case", ["boolean", ["get", "selected"], false], "#fff176", ["==", ["get", "aggregate"], "interface"], "#e9783d", "#1fb7a5"], "circle-stroke-color": "#16353a", "circle-stroke-width": 1 } });
        addMapLayer({ id: "che-footprint", type: "fill", source: "ches", filter: ["==", ["get", "role"], "footprint"], paint: { "fill-color": ["case", ["boolean", ["get", "powered"], false], "#5bbf72", "#667278"], "fill-opacity": 0.9 } });
        addMapLayer({ id: "che-outline-casing", type: "line", source: "ches", filter: ["==", ["get", "role"], "footprint"], paint: { "line-color": "#142027", "line-width": 5 } });
        addMapLayer({ id: "che-outline", type: "line", source: "ches", filter: ["==", ["get", "role"], "footprint"], paint: { "line-color": ["case", ["boolean", ["get", "selected"], false], "#fff176", "#e8efed"], "line-width": ["case", ["boolean", ["get", "selected"], false], 3, 1.5] } });
        addMapLayer({ id: "che-heading-casing", type: "line", source: "ches", filter: ["==", ["get", "role"], "heading"], layout: { "line-cap": "round" }, paint: { "line-color": "#142027", "line-width": 5 } });
        addMapLayer({ id: "che-heading", type: "line", source: "ches", filter: ["==", ["get", "role"], "heading"], paint: { "line-color": "#fff", "line-width": 2 } });
        addMapLayer({ id: "che-arrowhead", type: "fill", source: "ches", filter: ["==", ["get", "role"], "arrowhead"], paint: { "fill-color": "#fff", "fill-outline-color": "#142027" } });
        addMapLayer({ id: "che-label", type: "symbol", source: "ches", filter: ["==", ["get", "role"], "label"], layout: { "text-field": ["get", "label"], "text-size": 12, "text-offset": [0, 1.5] }, paint: { "text-color": "#fff", "text-halo-color": "#142027", "text-halo-width": 2 } });
        addMapLayer({ id: "draft-fill", type: "fill", source: "draft", filter: ["all", ["==", ["geometry-type"], "Polygon"], ["!=", ["get", "perimeter"], true]], paint: { "fill-color": ["get", "color"], "fill-opacity": ["case", ["boolean", ["get", "finalized"], false], 0.32, 0.2] } });
        addMapLayer({ id: "draft-line", type: "line", source: "draft", paint: { "line-color": ["case", ["boolean", ["get", "finalized"], false], ["get", "color"], "#fff176"], "line-width": ["case", ["boolean", ["get", "finalized"], false], 2, 4], "line-dasharray": [2, 1] } });
        addMapLayer({ id: "draft-point", type: "circle", source: "draft", filter: ["==", ["geometry-type"], "Point"], paint: { "circle-radius": ["case", ["boolean", ["get", "finalized"], false], 5, 7], "circle-color": ["get", "color"], "circle-stroke-color": "#fff", "circle-stroke-width": 2 } });
        addMapLayer({ id: "draw-guide-line", type: "line", source: "draw-guide", filter: ["==", ["geometry-type"], "LineString"], paint: { "line-color": "#fff176", "line-width": 2, "line-dasharray": [2, 1] } });
        addMapLayer({ id: "draw-guide-point", type: "circle", source: "draw-guide", filter: ["==", ["geometry-type"], "Point"], paint: { "circle-radius": 6, "circle-color": "#f2c94c", "circle-stroke-color": "#fff", "circle-stroke-width": 2 } });
        addMapLayer({ id: "asset-guide-line", type: "line", source: "asset-guide", filter: ["all", ["==", ["geometry-type"], "LineString"], ["!=", ["get", "role"], "yard_grid"]], paint: { "line-color": ["match", ["get", "role"], "water", "#2f9ed1", "gate_in", "#5bbf72", "gate_out", "#e9783d", "rail_track", "#8f73a0", "#e9783d"], "line-width": 4 } });
        addMapLayer({ id: "asset-guide-cell-fill", type: "fill", source: "asset-guide", filter: ["==", ["get", "role"], "yard_cell"], paint: { "fill-color": "#263e48", "fill-opacity": 0.3 } });
        addMapLayer({ id: "asset-guide-cell-outline", type: "line", source: "asset-guide", filter: ["==", ["get", "role"], "yard_cell"], paint: { "line-color": "#fff7c2", "line-width": 1.25 } });
        addMapLayer({ id: "asset-guide-point", type: "circle", source: "asset-guide", filter: ["all", ["==", ["geometry-type"], "Point"], ["!", ["in", ["get", "role"], ["literal", ["yard_label", "yard_position"]]]]], paint: { "circle-radius": 5, "circle-color": ["case", ["==", ["get", "role"], "transfer_position"], "#e9783d", "#f2c94c"], "circle-stroke-color": "#192126", "circle-stroke-width": 1.5 } });
        addMapLayer({ id: "asset-guide-point-label", type: "symbol", source: "asset-guide", filter: ["all", ["==", ["geometry-type"], "Point"], ["!=", ["get", "role"], "yard_position"]], layout: { "text-field": ["get", "label"], "text-size": 11, "text-offset": [0, 1.35], "text-allow-overlap": false }, paint: { "text-color": "#fff", "text-halo-color": "#192126", "text-halo-width": 2 } });
        addMapLayer({ id: "asset-guide-position-label", type: "symbol", source: "asset-guide", filter: ["==", ["get", "role"], "yard_position"], minzoom: 20, layout: { "text-field": ["get", "label"], "text-size": 10 }, paint: { "text-color": "#fff", "text-halo-color": "#263e48", "text-halo-width": 2 } });
        addMapLayer({ id: "asset-guide-line-label", type: "symbol", source: "asset-guide", filter: ["==", ["geometry-type"], "LineString"], layout: { "symbol-placement": "line-center", "text-field": ["get", "label"], "text-size": 10, "text-allow-overlap": true }, paint: { "text-color": "#fff", "text-halo-color": "#192126", "text-halo-width": 2 } });
        addMapLayer({ id: "block-guide-line", type: "line", source: "block-guide", filter: ["==", ["get", "role"], "heading"], paint: { "line-color": "#f2c94c", "line-width": 3 } });
        addMapLayer({ id: "block-guide-arrow", type: "fill", source: "block-guide", filter: ["==", ["get", "role"], "arrowhead"], paint: { "fill-color": "#f2c94c", "fill-opacity": 0.95 } });
        addMapLayer({ id: "block-guide-anchor", type: "circle", source: "block-guide", filter: ["==", ["get", "role"], "anchor"], paint: { "circle-radius": 7, "circle-color": "#1fb7a5", "circle-stroke-color": "#ffffff", "circle-stroke-width": 2 } });
        // Footprint previews must not cover saved cells, bollards, or transfer points.
        map.moveLayer("draft-fill", "yard-cell-fill");
        map.moveLayer("route-node", "interface-point");
    }

    function mapFeature(type, entity, geometry, properties = {}) {
        const id = entity.id ?? entity.pk;
        return { type: "Feature", id: `${type}-${id}`, geometry, properties: { entityType: type, entityId: id, ...properties } };
    }

    function berthWaterGeometry(asset) {
        const berth = asset.berth_segments?.[0];
        const axis = asset.linear_axes?.find((item) => item.id === berth?.quay_face_axis);
        const points = axis?.points || [];
        if (points.length < 2) return null;
        const first = points[0];
        const last = points[points.length - 1];
        const meanLat = (first[1] + last[1]) / 2 * Math.PI / 180;
        const east = (last[0] - first[0]) * 111320 * Math.cos(meanLat);
        const north = (last[1] - first[1]) * 110574;
        const length = Math.hypot(east, north) || 1;
        const sign = berth.water_side === "left" ? 1 : -1;
        const offsetEast = -north / length * 8 * sign;
        const offsetNorth = east / length * 8 * sign;
        return { type: "LineString", coordinates: points.map((point) => offsetLonLat(point, offsetEast, offsetNorth)) };
    }

    function refreshMap() {
        if (!map?.getSource("assets")) return;
        const selected = state.selected;
        const activelySelected = !state.draftFinalized;
        const context = mapTools.savedAssetFeatures(state.assets, colors, activelySelected ? selected : null);
        const yardGrid = state.assets.filter((item) => item.asset_kind === "yard_block" && item.storage_grid).flatMap((item) => {
            const grid = item.storage_grid;
            const anchor = [Number(grid.longitude), Number(grid.latitude)];
            return mapTools.yardGridGuides(anchor, Number(grid.heading_deg ?? item.heading_deg), grid).features.map((feature) => ({
                ...feature,
                properties: { ...feature.properties, entityType: "asset", entityId: item.id },
            }));
        });
        const berthSides = state.assets.map((item) => {
            const geometry = berthWaterGeometry(item);
            return geometry ? mapFeature("berth-side", item, geometry) : null;
        }).filter(Boolean);
        const markers = state.axes.flatMap((axis) => (axis.markers || []).filter((marker) => marker.marker_kind === "bollard").map((marker) => mapFeature("linear-marker", marker, { type: "Point", coordinates: [marker.longitude, marker.latitude] }, { label: marker.name || marker.number || marker.sequence })));
        const axes = state.axes.filter((item) => (item.points || []).length > 1).map((item) => {
            const role = item.metadata?.facility_role;
            const color = role === "gate_in" ? "#5bbf72" : role === "gate_out" ? "#e9783d" : role === "quay_service" ? "#4e8ac7" : role === "quay_crane" ? "#f2c94c" : role === "quay_backreach" ? "#e9783d" : item.axis_kind.includes("rail") ? "#8f73a0" : item.axis_kind === "quay_face" ? "#f2c94c" : "#314d60";
            const selectedDirectly = selected?.type === "axis" && selected.id === item.id;
            const selectedRoadAsset = selected?.type === "asset" && selected.id === item.asset
                && item.axis_kind === "road_centerline";
            return mapFeature("axis", item, { type: "LineString", coordinates: item.points }, {
                color,
                label: item.name || item.code,
                selected: activelySelected && (selectedDirectly || selectedRoadAsset),
            });
        });
        const interfaceGeometries = state.interfaces.flatMap((item) => mapTools.transferLaneGeometries(item).map((geometry, lane) => mapFeature("interface", item, geometry, { label: `${item.name || item.code} L${lane + 1}` })));
        const nodeById = new Map(state.nodes.map((node) => [node.id, node]));
        const edges = state.edges.filter((edge) => !["asset_interface", "interface_position"].includes(edge.metadata?.connector_kind)).map((edge) => {
            const from = nodeById.get(edge.from_node)?.point?.coordinates;
            const to = nodeById.get(edge.to_node)?.point?.coordinates;
            return from && to ? mapFeature("edge", edge, { type: "LineString", coordinates: [from, to] }, { generated: edge.is_generated }) : null;
        }).filter(Boolean);
        const nodes = mapTools.visibleRouteNodes(state.nodes).map((node) => mapFeature("node", node, node.point, {
            generated: node.is_generated,
            aggregate: node.metadata?.aggregate_kind || "",
            selected: selected?.type === "node" && selected.id === node.id,
        }));
        const interfaces = state.interfaces.flatMap((item) => {
            const ownerSelected = activelySelected && selected?.type === "asset"
                && selected.id === Number(item.asset);
            return (item.positions || []).filter((p) => p.longitude != null && p.latitude != null).map((position) => mapFeature("interface-position", position, { type: "Point", coordinates: [position.longitude, position.latitude] }, { entityType: "interface", entityId: item.id, label: position.code, ownerSelected }));
        });
        map.getSource("assets").setData(context.assets);
        map.getSource("perimeters").setData(context.perimeters);
        map.getSource("yard-grid").setData({ type: "FeatureCollection", features: yardGrid });
        map.getSource("berth-sides").setData({ type: "FeatureCollection", features: berthSides });
        map.getSource("linear-markers").setData({ type: "FeatureCollection", features: markers });
        map.getSource("axes").setData({ type: "FeatureCollection", features: axes });
        map.getSource("interface-geometries").setData({ type: "FeatureCollection", features: interfaceGeometries });
        map.getSource("edges").setData({ type: "FeatureCollection", features: edges });
        map.getSource("nodes").setData({ type: "FeatureCollection", features: nodes });
        map.getSource("interfaces").setData({ type: "FeatureCollection", features: interfaces });
        $("map").dataset.savedAssets = String(context.assets.features.length);
        $("map").dataset.yardCells = String(yardGrid.filter((feature) => feature.properties.role === "yard_cell").length);
        $("map").dataset.transferPoints = String(interfaces.length);
        refreshDraft();
    }

    function currentConfiguredSettings() {
        const spacings = $("bollardSpacings").value.split(",").map((value) => Number(value.trim())).filter((value) => value > 0);
        const kind = $("assetKind").value;
        if (kind === "quay") return {
            bollard_count: number("bollardCount", 15), default_bollard_spacing_m: number("bollardSpacing", 20), bollard_spacings: spacings,
            start_margin_m: number("berthStartMargin"), end_margin_m: number("berthEndMargin"), width_m: number("quayWidth", 25),
            water_side: $("waterSide").value, bollard_direction: $("bollardDirection").value,
            service_lane_count: number("quayServiceLanes", 2), crane_lane_count: number("quayCraneLanes", 2), backreach_lane_count: number("quayBackreachLanes", 1),
            lane_width_m: number("quayLaneWidth", 3.5), lane_spacing_m: number("quayLaneSpacing", 0.5),
        };
        if (kind === "gate_area") return {
            inbound_lane_count: number("gateInLanes", 2), outbound_lane_count: number("gateOutLanes", 2), lane_length_m: number("gateLaneLength", 60),
            lane_width_m: number("gateLaneWidth", 3.5), lane_spacing_m: number("gateLaneSpacing", 0.5),
        };
        if (kind === "rail_area") return {
            track_count: number("railTrackCount", 2), track_length_m: number("railTrackLength", 400), track_spacing_m: number("railTrackSpacing", 5),
        };
        return null;
    }

    function configuredGeometry(anchor, heading) {
        const kind = $("assetKind").value;
        if (kind === "yard_block") return mapTools.yardFootprint(anchor, heading, yardGridPayload());
        return mapTools.configuredAssetGeometry(kind, anchor, heading, currentConfiguredSettings());
    }

    function refreshDraft(rebuildHandles = true) {
        if (!map?.getSource("draft")) return;
        const chePreview = refreshCheMap();
        const draftColor = activePane() === "infrastructure" ? colors[$("assetKind").value] || colors.other : colors.terminal;
        const feature = state.draft && !chePreview ? [{ type: "Feature", geometry: state.draft, properties: { finalized: state.draftFinalized, color: draftColor, perimeter: state.perimeterDraft } }] : [];
        map.getSource("draft").setData({ type: "FeatureCollection", features: feature });
        const drawingGuide = mapTools.drawingGuide(state.tool, state.drawPoints, state.hoverPoint);
        if (state.roadSnap) drawingGuide.features.push({
            type: "Feature",
            properties: { role: "snap", label: state.roadSnap.axis_code },
            geometry: { type: "Point", coordinates: state.roadSnap.point },
        });
        map.getSource("draw-guide").setData(drawingGuide);
        let assetGuide = emptyCollection();
        const kind = $("assetKind").value;
        if (state.blockAnchor && ["quay", "gate_area", "rail_area"].includes(kind)) {
            assetGuide = mapTools.configuredAssetGuides(kind, state.blockAnchor, number("assetHeading"), currentConfiguredSettings());
            if (kind !== "gate_area") {
                const generator = currentConfiguredSettings();
                assetGuide.features.push(...mapTools.yardTransferGuides(
                    mapTools.assetTransferAnchor(kind, state.blockAnchor, number("assetHeading"), generator),
                    number("assetHeading"), mapTools.assetTransferGrid(kind, generator), currentYardTransferSettings(),
                ).features);
            }
        }
        if (state.blockAnchor && kind === "yard_block") {
            const grid = yardGridPayload();
            assetGuide = {
                type: "FeatureCollection",
                features: [
                    ...mapTools.yardGridGuides(state.blockAnchor, number("assetHeading"), grid).features,
                    ...mapTools.yardTransferGuides(state.blockAnchor, number("assetHeading"), grid, currentYardTransferSettings()).features,
                ],
            };
        }
        map.getSource("asset-guide").setData(assetGuide);
        let guide = emptyCollection();
        if (state.blockAnchor) {
            if (state.draft || state.blockHeadingPoint) {
                let length = 40;
                if (kind === "yard_block") {
                    const dimensions = mapTools.yardDimensions(yardGridPayload());
                    length = $("bayHeadingAxis").value === "true" ? dimensions.bayLength : dimensions.rowWidth;
                } else if (kind === "quay") length = mapTools.quayLength(currentConfiguredSettings());
                else if (kind === "gate_area") length = number("gateLaneLength", 60);
                else if (kind === "rail_area") length = number("railTrackLength", 400);
                guide = mapTools.headingGuide(state.blockAnchor, number("assetHeading"), length);
            } else guide.features.push({ type: "Feature", properties: { role: "anchor" }, geometry: { type: "Point", coordinates: state.blockAnchor } });
        }
        map.getSource("block-guide").setData(guide);
        if (rebuildHandles) renderVertexMarkers();
    }

    function refreshCheMap() {
        let preview = emptyCollection();
        $("cheRotation").disabled = false;
        const editing = state.selected?.type === "che" ? selectedEntity() : null;
        if (activePane() === "equipment" && state.draft?.type === "Point" && (!state.selected || editing) && Number($("cheType").value)) {
            const type = state.equipmentTypes.find((item) => item.id === Number($("cheType").value));
            let data = {
                pk: editing?.pk ?? editing?.id, name: $("cheName").value,
                equipment_type_details: editing?.equipment_type === Number($("cheType").value) ? editing.equipment_type_details : null,
                longitude: state.draft.coordinates[0], latitude: state.draft.coordinates[1],
                rotation: number("cheRotation"), powered_on: $("chePowered").checked,
            };
            const assetId = Number($("cheAsset").value) || null;
            const sameAssignment = editing && editing.current_asset === assetId && editing.equipment_type === type?.id;
            const motion = sameAssignment && editing.rail_motion ? editing.rail_motion : mapTools.editorRailMotion({
                cheId: editing?.pk ?? editing?.id, equipmentType: type, assetId, assets: state.assets, axes: state.axes, scopes: state.scopes,
            });
            data = mapTools.alignEquipment(data, motion, !sameAssignment);
            if (data.rail_axis_id != null) {
                state.draft.coordinates = [data.longitude, data.latitude];
                $("cheLatitude").value = data.latitude.toFixed(7);
                $("cheLongitude").value = data.longitude.toFixed(7);
                $("cheRotation").value = data.rotation.toFixed(1);
                $("cheRotation").disabled = true;
            }
            preview = mapTools.equipmentFeatures(data, type, { selected: true, preview: true });
        }
        const features = state.ches.flatMap((che) => {
            const id = che.pk ?? che.id;
            if (preview.features.length && editing && id === (editing.pk ?? editing.id)) return [];
            const type = state.equipmentTypes.find((item) => item.id === che.equipment_type);
            const motion = che.rail_motion || mapTools.editorRailMotion({ cheId: id, equipmentType: type, assetId: che.current_asset,
                assets: state.assets, axes: state.axes, scopes: state.scopes });
            return mapTools.equipmentFeatures(mapTools.alignEquipment(che, motion), type, { selected: state.selected?.type === "che" && state.selected.id === id }).features;
        });
        features.push(...preview.features);
        map.getSource("ches").setData({ type: "FeatureCollection", features });
        $("map").dataset.cheFootprints = String(features.filter((feature) => feature.properties.role === "footprint").length);
        return preview.features.length > 0;
    }

    function clearMarkers() {
        state.vertexMarkers.forEach((marker) => marker.remove());
        state.vertexMarkers = [];
    }

    function renderVertexMarkers() {
        clearMarkers();
        if (!state.draft || !state.geometryEditing || state.draftFinalized || !["select", "move"].includes(state.tool)) return;
        const selected = selectedEntity();
        if (state.selected?.type === "edge" || (state.selected?.type === "node" && selected?.is_generated)) return;
        const type = state.draft.type;
        const points = geometryCoordinates(state.draft);
        if (!points.length) return;
        points.forEach((point, index) => {
            const element = document.createElement("div");
            element.className = "vertex";
            const marker = new maplibregl.Marker({ element, draggable: true }).setLngLat(point).addTo(map);
            marker.on("drag", () => updateDraftPoint(index, marker.getLngLat().toArray(), false));
            marker.on("dragend", () => updateDraftPoint(index, marker.getLngLat().toArray()));
            state.vertexMarkers.push(marker);
        });
        if (type !== "Point") {
            const segmentCount = type === "Polygon" ? points.length : points.length - 1;
            for (let i = 0; i < segmentCount; i += 1) {
                const element = document.createElement("button");
                element.className = "vertex midpoint";
                element.type = "button";
                element.title = "Add vertex";
                const next = (i + 1) % points.length;
                const marker = new maplibregl.Marker({ element }).setLngLat(midpoint(points[i], points[next])).addTo(map);
                element.addEventListener("click", (event) => {
                    event.stopPropagation();
                    const updated = geometryCoordinates(state.draft);
                    updated.splice(i + 1, 0, midpoint(updated[i], updated[next]));
                    state.draft = geometryFromPoints(type, updated);
                    refreshDraft();
                });
                state.vertexMarkers.push(marker);
            }
        }
    }

    function updateDraftPoint(index, coordinate, rebuildHandles = true) {
        state.draft = mapTools.replaceGeometryPoint(state.draft, index, coordinate);
        if (state.selected?.type === "che") {
            $("cheLongitude").value = coordinate[0].toFixed(7);
            $("cheLatitude").value = coordinate[1].toFixed(7);
        }
        refreshDraft(rebuildHandles);
    }

    function onMapMove(event) {
        $("coordinates").textContent = `Lat ${event.lngLat.lat.toFixed(7)}, Lon ${event.lngLat.lng.toFixed(7)}`;
        const rawPoint = event.lngLat.toArray();
        state.roadSnap = roadDrawingSnap(rawPoint);
        let point = state.roadSnap?.point || rawPoint;
        if (state.tool === "line" && state.drawPoints.length === 1 && state.roadBranchHeading != null && event.originalEvent?.shiftKey) {
            point = mapTools.perpendicularPoint(state.drawPoints[0], rawPoint, state.roadBranchHeading);
            state.roadSnap = null;
        }
        if (state.tool === "block" && state.blockAnchor) {
            const heading = mapTools.bearingDegrees(state.blockAnchor, point);
            $("assetHeading").value = heading.toFixed(1);
            state.blockHeadingPoint = point;
            state.draft = configuredGeometry(state.blockAnchor, heading);
            refreshDraft(false);
        }
        if (state.tool === "rectangle" && state.rectangleStart) {
            const a = state.rectangleStart;
            const b = point;
            state.draft = { type: "Polygon", coordinates: [[[a[0], a[1]], [b[0], a[1]], b, [a[0], b[1]], [a[0], a[1]]]] };
            state.hoverPoint = point;
            refreshDraft(false);
        }
        if (["polygon", "line"].includes(state.tool) && (state.drawPoints.length || state.roadSnap)) { state.hoverPoint = point; refreshDraft(false); }
        if (state.moving) moveSelected(point);
        let hovering = false;
        if (state.tool === "select" && map.isStyleLoaded()) {
            const layers = selectableMapLayers();
            hovering = layers.length > 0 && map.queryRenderedFeatures(event.point, { layers }).length > 0;
        }
        applyMapCursor(hovering);
    }

    function onMapClick(event) {
        if (state.ignoreNextClick) { state.ignoreNextClick = false; return; }
        const rawPoint = event.lngLat.toArray();
        state.roadSnap = roadDrawingSnap(rawPoint);
        let point = state.roadSnap?.point || rawPoint;
        if (state.tool === "line" && state.drawPoints.length === 1 && state.roadBranchHeading != null && event.originalEvent?.shiftKey) {
            point = mapTools.perpendicularPoint(state.drawPoints[0], rawPoint, state.roadBranchHeading);
            state.roadSnap = null;
        }
        if (state.tool === "connect") {
            const feature = map.queryRenderedFeatures(event.point, { layers: ["axis-line"] })
                .find((item) => state.axes.find((axis) => axis.id === Number(item.properties.entityId))?.axis_kind === "road_centerline");
            if (!feature) return setStatus("Click a road centerline to connect the selected interface.", true);
            connectInterfaceToRoad(state.connectingInterfaceId, Number(feature.properties.entityId));
            return;
        }
        if (state.tool === "block") {
            const kind = $("assetKind").value;
            if (activePane() !== "infrastructure" || !["yard_block", "quay", "gate_area", "rail_area"].includes(kind)) {
                setStatus("The configured asset tool is available for yard blocks, quays, gates, and rail areas.", true);
                return;
            }
            if (!state.blockAnchor) {
                state.blockAnchor = point;
                state.blockHeadingPoint = null;
                state.draft = null;
                state.geometryEditing = true;
                state.draftFinalized = false;
                $("assetHeading").value = "0.0";
                refreshDraft();
                applyMapCursor();
                setStatus("Anchor placed. Move the pointer and click to set the asset heading.");
            } else {
                const heading = mapTools.bearingDegrees(state.blockAnchor, point);
                $("assetHeading").value = heading.toFixed(1);
                state.blockHeadingPoint = point;
                state.draft = configuredGeometry(state.blockAnchor, heading);
                finalizeDraft(`${kind.replaceAll("_", " ")} ready at ${heading.toFixed(1)} degrees true north.`);
            }
            return;
        }
        if (state.tool === "rectangle") {
            if (!state.rectangleStart) { state.rectangleStart = point; state.drawPoints = [point]; state.geometryEditing = true; state.draftFinalized = false; refreshDraft(); setStatus("Choose the opposite rectangle corner."); }
            else finalizeDraft("Rectangle ready.");
            return;
        }
        if (state.tool === "polygon" || state.tool === "line") {
            const completingBranch = state.tool === "line" && state.drawPoints.length === 1 && state.roadBranchHeading != null;
            if (state.tool === "line" && state.drawPoints.length === 0) state.roadBranchHeading = state.roadSnap?.heading_deg ?? null;
            const previousLength = state.drawPoints.length;
            state.drawPoints = mapTools.appendDistinctPoint(state.drawPoints, point);
            if (state.drawPoints.length > previousLength) {
                state.drawPointSnaps.push(state.tool === "line" && state.roadSnap ? { ...state.roadSnap } : null);
                rebuildPendingRoadInterfaces();
            }
            if (completingBranch) state.roadBranchHeading = null;
            state.draft = geometryFromPoints(state.tool === "polygon" ? "Polygon" : "LineString", state.drawPoints);
            state.geometryEditing = true; state.draftFinalized = false; state.hoverPoint = null;
            refreshDraft();
            const snapLabel = state.roadSnap?.axis_code || state.roadSnap?.interface_code;
            const snapMessage = snapLabel ? ` Snapped to ${snapLabel}.` : "";
            setStatus(`${state.drawPoints.length} point${state.drawPoints.length === 1 ? "" : "s"}.${snapMessage} Right-click, double-click, or press Enter to finish. Backspace removes the last point.`);
            return;
        }
        if (state.tool === "point") {
            state.draft = { type: "Point", coordinates: point };
            state.geometryEditing = true; state.draftFinalized = false;
            if (activePane() === "equipment") { $("cheLongitude").value = point[0].toFixed(7); $("cheLatitude").value = point[1].toFixed(7); }
            finalizeDraft("Point ready."); return;
        }
        if (state.tool === "move") return;
        const features = map.queryRenderedFeatures(event.point, { layers: selectableMapLayers() });
        const feature = features.find((item) => item.properties.entityId != null);
        if (!feature) { selectEntity(null); return; }
        selectEntity(selectionForMapFeature(feature));
    }

    function selectionForMapFeature(feature) {
        const selection = { type: feature.properties.entityType, id: Number(feature.properties.entityId) };
        if (selection.type !== "axis") return selection;
        const axis = state.axes.find((item) => item.id === selection.id);
        if (axis?.axis_kind !== "road_centerline" || !axis.asset) return selection;
        const roadAsset = state.assets.find((item) => item.id === axis.asset && item.asset_kind === "road");
        return roadAsset ? { type: "asset", id: roadAsset.id } : selection;
    }

    function onMapDoubleClick(event) {
        if (["polygon", "line"].includes(state.tool)) {
            event.preventDefault();
            finishDrawing();
            return;
        }
        if (state.tool !== "select") return;
        const feature = map.queryRenderedFeatures(event.point, { layers: ["axis-line"] })
            .find((item) => state.axes.find((axis) => axis.id === Number(item.properties.entityId))?.axis_kind === "road_centerline");
        if (!feature) return;
        const axis = state.axes.find((item) => item.id === Number(feature.properties.entityId));
        const inserted = mapTools.insertRoadVertex(axis.points, event.lngLat.toArray());
        if (!inserted) return;
        event.preventDefault();
        selectEntity({ type: "axis", id: axis.id });
        state.draft = { type: "LineString", coordinates: inserted.points };
        state.geometryEditing = true;
        state.draftFinalized = false;
        refreshDraft();
        setStatus(inserted.inserted ? "Road node added. Drag it if needed, then save the axis." : "Road node selected. Drag it if needed, then save the axis.");
    }

    async function onMapContextMenu(event) {
        event.preventDefault?.();
        event.originalEvent?.preventDefault?.();
        if (!["polygon", "line"].includes(state.tool)) return;
        const saveRoad = state.tool === "line" && $("assetKind")?.value === "road" && activePane() === "infrastructure";
        if (!finishDrawing()) return;
        if (saveRoad) await saveAsset({ preventDefault() {} });
    }

    function selectableMapLayers() {
        return ["che-footprint", "che-outline", "che-heading", "che-arrowhead", "che-label", "route-node", "interface-point", "interface-line", "axis-line", "route-edge", "asset-label", "asset-fill", "asset-outline", "perimeter-outline", "yard-cell-outline"].filter((id) => map.getLayer(id));
    }

    function beginMove(event) {
        if (state.tool === "block" && activePane() === "infrastructure" && !state.blockAnchor) {
            const point = event.lngLat.toArray();
            state.blockAnchor = point;
            state.blockHeadingPoint = point;
            state.configuredDrag = { start: point };
            state.geometryEditing = true;
            state.draftFinalized = false;
            state.draft = configuredGeometry(point, 0);
            $("assetHeading").value = "0.0";
            map.dragPan.disable();
            refreshDraft(false);
            setStatus("Drag to set the true-north orientation, then release.");
            return;
        }
        if (state.tool !== "move" || !state.draft || !state.geometryEditing) return;
        state.moving = { start: event.lngLat.toArray(), geometry: deepCopy(state.draft) };
        map.dragPan.disable();
        applyMapCursor();
    }

    function moveSelected(point) {
        const dx = point[0] - state.moving.start[0];
        const dy = point[1] - state.moving.start[1];
        const geometry = state.moving.geometry;
        const move = (coordinate) => [coordinate[0] + dx, coordinate[1] + dy];
        if (geometry.type === "Point") state.draft = { type: "Point", coordinates: move(geometry.coordinates) };
        if (geometry.type === "LineString") state.draft = { type: "LineString", coordinates: geometry.coordinates.map(move) };
        if (geometry.type === "Polygon") state.draft = { type: "Polygon", coordinates: [geometry.coordinates[0].map(move)] };
        const assetKind = state.selected?.type === "asset" ? selectedEntity()?.asset_kind : $("assetKind")?.value;
        if (["yard_block", "quay", "gate_area", "rail_area"].includes(assetKind)) {
            state.blockAnchor = geometryCoordinates(state.draft)[0] || state.blockAnchor;
        }
        if (activePane() === "equipment" && state.draft.type === "Point") {
            $("cheLongitude").value = state.draft.coordinates[0].toFixed(7);
            $("cheLatitude").value = state.draft.coordinates[1].toFixed(7);
        }
        refreshDraft(false);
    }

    function endMove(event) {
        if (state.configuredDrag) {
            const point = event?.lngLat?.toArray?.() || state.blockHeadingPoint || state.blockAnchor;
            const distance = haversine(state.blockAnchor, point);
            state.configuredDrag = null;
            map.dragPan.enable();
            state.ignoreNextClick = true;
            if (distance < 0.5) {
                setStatus("Corner placed. Move the pointer and click to set the asset orientation.");
                return;
            }
            const heading = mapTools.bearingDegrees(state.blockAnchor, point);
            $("assetHeading").value = heading.toFixed(1);
            state.blockHeadingPoint = point;
            state.draft = configuredGeometry(state.blockAnchor, heading);
            finalizeDraft(`${$("assetKind").value.replaceAll("_", " ")} ready at ${heading.toFixed(1)} degrees true north.`);
            return;
        }
        if (!state.moving) return;
        state.moving = null;
        map.dragPan.enable();
        setTool("select");
        refreshDraft();
        setStatus("Geometry moved. Save to persist the change.");
    }

    function finishDrawing() {
        if (state.tool === "polygon" && state.drawPoints.length < 3) { setStatus("A polygon needs at least three points.", true); return false; }
        if (state.tool === "line" && state.drawPoints.length < 2) { setStatus("A line needs at least two points.", true); return false; }
        if (state.tool === "rectangle" && !state.draft) { setStatus("Choose the opposite rectangle corner first.", true); return false; }
        if (state.tool === "polygon") state.draft = geometryFromPoints("Polygon", state.drawPoints);
        if (state.tool === "line") state.draft = geometryFromPoints("LineString", state.drawPoints);
        if (!state.draft) { setStatus("There is no geometry to finish.", true); return false; }
        finalizeDraft("Geometry ready.");
        return true;
    }

    function rebuildPendingRoadInterfaces() {
        state.pendingRoadInterfaceIds = new Set(state.drawPointSnaps.map((snap) => snap?.interface_id).filter(Boolean));
    }

    function removeLastDrawPoint() {
        if (!["polygon", "line"].includes(state.tool) || !state.drawPoints.length) return false;
        state.drawPoints = state.drawPoints.slice(0, -1);
        state.drawPointSnaps = state.drawPointSnaps.slice(0, state.drawPoints.length);
        rebuildPendingRoadInterfaces();
        state.roadSnap = null;
        state.hoverPoint = null;
        state.draft = geometryFromPoints(state.tool === "polygon" ? "Polygon" : "LineString", state.drawPoints);
        state.roadBranchHeading = state.tool === "line" && state.drawPoints.length === 1
            ? state.drawPointSnaps[0]?.heading_deg ?? null : null;
        refreshDraft();
        setStatus(state.drawPoints.length
            ? `Last point removed. ${state.drawPoints.length} point${state.drawPoints.length === 1 ? "" : "s"} remain.`
            : "Last point removed. Click the map to start again.");
        return true;
    }

    function finalizeDraft(message) {
        state.drawPoints = [];
        state.rectangleStart = null;
        state.hoverPoint = null;
        state.roadSnap = null;
        state.roadBranchHeading = null;
        state.drawPointSnaps = [];
        state.geometryEditing = false;
        state.draftFinalized = true;
        state.createKind = null;
        setTool("select");
        refreshCreationToolButtons();
        clearMarkers();
        refreshMap();
        $("toolCallout").classList.add("ready");
        $("toolName").textContent = "Ready to save";
        $("drawHint").textContent = "Geometry is finished. Save the definition or select it again to continue editing.";
        $("selectionSubtitle").textContent = "Geometry ready to save";
        if (activePane() === "infrastructure" && state.draft) openAssetContextPanel(
            ["yard_block", "quay", "gate_area", "rail_area", "road", "transfer_zone"].includes($("assetKind").value) ? "layout" : "general",
        );
        setStatus(`${message} Save the definition to persist it.`);
    }

    function clearDrawing() {
        state.draft = null; state.drawPoints = []; state.rectangleStart = null; state.blockAnchor = null; state.blockHeadingPoint = null;
        state.hoverPoint = null; state.geometryEditing = false; state.draftFinalized = false;
        state.roadSnap = null; state.roadBranchHeading = null; state.drawPointSnaps = []; state.pendingRoadInterfaceIds.clear(); state.connectingInterfaceId = null;
        state.createKind = null; refreshCreationToolButtons();
        if (!state.selected) closeAssetContextPanel();
        clearMarkers(); refreshDraft(); setStatus("Draft geometry cleared.");
    }

    function setTool(tool) {
        const changed = state.tool !== tool;
        state.tool = tool;
        if (tool !== "line") state.roadSnap = null;
        document.querySelectorAll("[data-tool]").forEach((button) => button.classList.toggle("active", button.dataset.tool === tool));
        const names = { select: "Select", connect: "Connect interface", polygon: "Polygon", rectangle: "Rectangle", line: "Line", point: "Point", block: "Configured asset", move: "Move" };
        const hints = { select: "Click an object; double-click a road to add a draggable node.", connect: "Click a road centerline to connect the selected transfer interface.", polygon: "Click vertices; right-click or Enter to finish, Backspace to undo.", rectangle: "Click two opposite corners.", line: "Click nodes; right-click or Enter to finish, Backspace to undo. Hold Shift for a perpendicular branch.", point: "Click a map location.", block: "Click the growth corner, then point and click to set its true-north heading.", move: "Drag the selected geometry to a new position." };
        $("toolCallout").classList.remove("ready");
        $("toolName").textContent = names[tool] || "Map tool";
        $("drawHint").textContent = hints[tool] || "";
        if (tool === "block" && changed) {
            state.draft = null;
            state.blockAnchor = null;
            state.blockHeadingPoint = null;
            state.geometryEditing = true;
            state.draftFinalized = false;
        }
        if (["polygon", "line", "rectangle"].includes(tool) && changed) {
            state.draft = null;
            state.drawPoints = [];
            state.drawPointSnaps = [];
            state.geometryEditing = true;
            state.draftFinalized = false;
        } else if (!["polygon", "line"].includes(tool)) state.drawPoints = [];
        if (tool !== "rectangle") state.rectangleStart = null;
        if (!["polygon", "line", "rectangle"].includes(tool)) state.hoverPoint = null;
        applyMapCursor();
        refreshDraft();
    }

    function applyMapCursor(hovering = false) {
        if (!map || !mapTools) return;
        mapTools.applyCursor(map.getCanvas(), state.tool, { hovering, hasAnchor: Boolean(state.blockAnchor), dragging: Boolean(state.moving) });
    }

    function activePane() {
        return document.querySelector(".tab.active")?.dataset.pane || "terminal";
    }

    function setContextTab(name) {
        const panel = $("assetContextPanel");
        if (!panel) return;
        panel.dataset.tab = name;
        document.querySelectorAll("[data-context-tab]").forEach((button) => {
            button.classList.toggle("active", button.dataset.contextTab === name);
        });
    }

    function openAssetContextPanel(tab = "general") {
        const panel = $("assetContextPanel");
        if (!panel) return;
        const editing = state.selected?.type === "asset" ? selectedEntity() : null;
        const kind = editing?.asset_kind || $("assetKind").value;
        $("contextPanelTitle").textContent = $("assetName").value || assetCreation[kind]?.label || "Asset";
        $("contextPanelSubtitle").textContent = editing ? `${editing.code} / ${editing.asset_kind_display || editing.asset_kind}` : "New unsaved asset";
        const hasLayout = ["yard_block", "quay", "gate_area", "rail_area", "road", "transfer_zone"].includes(kind);
        const hasTransfers = ["yard_block", "quay", "rail_area"].includes(kind);
        const layoutTab = document.querySelector('[data-context-tab="layout"]');
        const transferTab = document.querySelector('[data-context-tab="transfers"]');
        if (layoutTab) layoutTab.disabled = !hasLayout;
        if (transferTab) transferTab.disabled = !hasTransfers;
        const requested = tab === "transfers" && !hasTransfers ? (hasLayout ? "layout" : "general")
            : tab === "layout" && !hasLayout ? "general" : tab;
        setContextTab(requested);
        panel.classList.remove("hidden");
    }

    function closeAssetContextPanel() {
        $("assetContextPanel")?.classList.add("hidden");
    }

    function setProjectDrawer(open) {
        root.classList.toggle("drawer-open", open);
        $("toggleLibrary")?.setAttribute("aria-expanded", String(open));
    }

    function nextAssetIdentity(kind) {
        const definition = assetCreation[kind] || { prefix: "ASSET", label: "Asset" };
        const expression = new RegExp(`^${definition.prefix}-(\\d+)$`, "i");
        const used = new Set(state.assets.map((item) => String(item.code || "").toUpperCase()));
        let sequence = 1;
        while (used.has(`${definition.prefix}-${String(sequence).padStart(2, "0")}`.toUpperCase())) sequence += 1;
        const suffix = String(sequence).padStart(2, "0");
        return { code: `${definition.prefix}-${suffix}`, name: `${definition.label} ${suffix}` };
    }

    function defaultTransferSplits() {
        const bayUsesHeading = $("bayHeadingAxis").value === "true";
        const leftRightMaximum = number(bayUsesHeading ? "maxRows" : "maxBays", 1);
        const topBottomMaximum = number(bayUsesHeading ? "maxBays" : "maxRows", 1);
        $("transferLrSplit").value = leftRightMaximum > 1 ? Math.floor(leftRightMaximum / 2) : "";
        $("transferTbSplit").value = topBottomMaximum > 1 ? Math.floor(topBottomMaximum / 2) : "";
    }

    function refreshCreationToolButtons() {
        document.querySelectorAll("[data-create-kind]").forEach((button) => {
            button.classList.toggle("active", button.dataset.createKind === state.createKind);
        });
    }

    function startAssetCreation(kind) {
        const definition = assetCreation[kind];
        if (!definition) return;
        state.selected = null;
        state.draft = null;
        state.perimeterDraft = false;
        state.blockAnchor = null;
        state.blockHeadingPoint = null;
        state.geometryEditing = true;
        state.draftFinalized = false;
        state.pendingRoadInterfaceIds.clear();
        state.drawPointSnaps = [];
        state.ignoreNextClick = false;
        state.duplicateMetadata = null;
        resetAssetControls();
        $("assetKind").value = kind;
        const identity = nextAssetIdentity(kind);
        $("assetCode").value = identity.code;
        $("assetName").value = identity.name;
        defaultTransferSplits();
        updateAssetSections();
        activatePane("infrastructure");
        closeAssetContextPanel();
        state.createKind = kind;
        setTool(definition.tool);
        refreshCreationToolButtons();
        $("toolName").textContent = `Create ${definition.label}`;
        $("drawHint").textContent = definition.tool === "block"
            ? "Press at the growth corner, drag toward true north orientation, and release."
            : definition.tool === "line"
                ? "Click road nodes; right-click to finish and save, Enter to finish, Backspace to undo. Existing roads and transfer points are magnetic."
                : "Click boundary points; right-click or Enter to finish, Backspace to undo.";
        renderInspector(null);
        refreshLists();
        refreshMap();
        setStatus(`${definition.label}: draw the geometry on the map.`);
    }

    function translatedGeometry(geometry, eastM = 12, northM = -12) {
        const center = geometryCenter(geometry);
        if (!center) return deepCopy(geometry);
        const destination = offsetLonLat(center, eastM, northM);
        const shift = (point) => [
            Number(point[0]) + destination[0] - center[0],
            Number(point[1]) + destination[1] - center[1],
        ];
        if (geometry.type === "Point") return { type: "Point", coordinates: shift(geometry.coordinates) };
        if (geometry.type === "LineString") return { type: "LineString", coordinates: geometry.coordinates.map(shift) };
        if (geometry.type === "Polygon") return { type: "Polygon", coordinates: geometry.coordinates.map((ring) => ring.map(shift)) };
        return deepCopy(geometry);
    }

    function copySelectedAsset() {
        const asset = state.selected?.type === "asset" ? selectedEntity() : null;
        if (!asset || asset.asset_kind === "terminal") return setStatus("Select an infrastructure asset to copy.", true);
        state.clipboardAsset = deepCopy(asset);
        setStatus(`${asset.code} copied. Press Ctrl+V or Cmd+V to place a duplicate.`);
    }

    function pasteCopiedAsset() {
        const source = state.clipboardAsset;
        if (!source) return setStatus("Copy an infrastructure asset first.", true);
        resetAssetControls();
        populateAssetForm(source);
        const identity = nextAssetIdentity(source.asset_kind);
        $("assetCode").value = identity.code;
        $("assetName").value = `${source.name} copy`;
        state.selected = null;
        state.createKind = null;
        state.duplicateMetadata = deepCopy(source.metadata || {});
        state.draft = translatedGeometry(source.geometry);
        state.blockAnchor = ["yard_block", "quay", "gate_area", "rail_area"].includes(source.asset_kind)
            ? geometryCoordinates(state.draft)[0] || null : null;
        state.blockHeadingPoint = state.blockAnchor;
        state.geometryEditing = true;
        state.draftFinalized = false;
        activatePane("infrastructure");
        setTool("move");
        renderInspector(null);
        refreshLists();
        refreshMap();
        openAssetContextPanel("general");
        setStatus(`${identity.code} duplicated 12 m southeast. Drag it to the desired position, then save it.`);
    }

    function onEditorKeyDown(event) {
        const tag = String(event.target?.tagName || "").toLowerCase();
        if (["input", "textarea", "select"].includes(tag) || event.target?.isContentEditable) return;
        if (!event.ctrlKey && !event.metaKey && !event.altKey && ["polygon", "line"].includes(state.tool)) {
            if (event.key === "Enter") {
                event.preventDefault();
                finishDrawing();
                return;
            }
            if (event.key === "Backspace") {
                event.preventDefault();
                removeLastDrawPoint();
                return;
            }
        }
        if (!(event.ctrlKey || event.metaKey) || event.altKey) return;
        if (event.key.toLowerCase() === "c") {
            event.preventDefault();
            copySelectedAsset();
        }
        if (event.key.toLowerCase() === "v") {
            event.preventDefault();
            pasteCopiedAsset();
        }
    }

    function roadDrawingSnap(point) {
        if (state.tool !== "line") return null;
        const routingRoad = activePane() === "routing" && $("axisKind")?.value === "road_centerline";
        const infrastructureRoad = activePane() === "infrastructure" && $("assetKind")?.value === "road";
        if (!routingRoad && !infrastructureRoad) return null;
        const optionalSnap = (name, ...args) => typeof mapTools?.[name] === "function"
            ? mapTools[name](...args)
            : null;
        const editingAxisId = state.selected?.type === "axis" ? state.selected.id : null;
        const draftStart = state.drawPoints.length > 1 && haversine(point, state.drawPoints[0]) <= 8
            ? { point: state.drawPoints[0], distance_m: haversine(point, state.drawPoints[0]), axis_code: "road start" }
            : null;
        const vertex = optionalSnap("roadVertexSnap", point, state.axes, 8, editingAxisId);
        const segment = optionalSnap("roadSegmentSnap", point, state.axes, 8, editingAxisId);
        const transfer = optionalSnap("interfaceConnectionSnap", point, state.interfaces, 8);
        const explicitNodes = [draftStart, vertex, transfer].filter(Boolean)
            .sort((first, second) => first.distance_m - second.distance_m);
        return explicitNodes[0] || segment || null;
    }

    async function connectInterfaceToRoad(interfaceId, roadAxisId) {
        const item = state.interfaces.find((value) => value.id === Number(interfaceId));
        const road = state.axes.find((value) => value.id === Number(roadAxisId) && value.axis_kind === "road_centerline");
        if (!item || !road) return setStatus("Select a transfer interface and a road centerline.", true);
        try {
            await api(endpoint("transfer-interfaces", item.id), { method: "PATCH", body: JSON.stringify({ road_axis: road.id }) });
            state.connectingInterfaceId = null;
            await loadAll();
            selectEntity({ type: "interface", id: item.id });
            setTool("select");
            setStatus(`${item.code} connected to ${road.code}; routing links recompiled.`);
        } catch (error) { setStatus(error.message, true); }
    }

    async function connectPendingInterfaces(road) {
        if (!road || !state.pendingRoadInterfaceIds.size) return;
        const ids = [...state.pendingRoadInterfaceIds];
        for (const interfaceId of ids) {
            await api(endpoint("transfer-interfaces", interfaceId), { method: "PATCH", body: JSON.stringify({ road_axis: road.id }) });
        }
        state.pendingRoadInterfaceIds.clear();
    }

    function beginInterfaceConnection() {
        if (state.selected?.type !== "interface") return setStatus("Select a transfer interface first.", true);
        state.connectingInterfaceId = state.selected.id;
        setTool("connect");
    }

    async function disconnectSelectedInterface() {
        if (state.selected?.type !== "interface") return setStatus("Select a connected transfer interface first.", true);
        const item = selectedEntity();
        try {
            await api(endpoint("transfer-interfaces", item.id), { method: "PATCH", body: JSON.stringify({ road_axis: null }) });
            await loadAll();
            selectEntity({ type: "interface", id: item.id });
            setStatus(`${item.code} disconnected from its road.`);
        } catch (error) { setStatus(error.message, true); }
    }

    function activatePane(name) {
        document.querySelectorAll(".tab").forEach((tab) => tab.classList.toggle("active", tab.dataset.pane === name));
        document.querySelectorAll("[data-pane-panel]").forEach((pane) => pane.classList.toggle("active", pane.dataset.panePanel === name));
    }

    function selectedEntity() {
        const selection = state.selected;
        if (!selection) return null;
        const collection = { asset: state.assets, policy: state.policies, axis: state.axes, interface: state.interfaces, node: state.nodes, edge: state.edges, che: state.ches, scope: state.scopes }[selection.type] || [];
        return collection.find((item) => Number(item.pk ?? item.id) === selection.id) || null;
    }

    function selectEntity(selection) {
        state.selected = selection;
        if (selection) setProjectDrawer(false);
        state.createKind = null;
        refreshCreationToolButtons();
        const entity = selectedEntity();
        state.draft = entityGeometry(selection?.type, entity);
        state.geometryEditing = Boolean(state.draft);
        state.draftFinalized = false;
        state.hoverPoint = null;
        state.roadSnap = null;
        state.perimeterDraft = selection?.type === "asset" && entity?.asset_kind === "terminal";
        if (selection?.type === "asset" && ["yard_block", "quay", "gate_area", "rail_area"].includes(entity?.asset_kind)) {
            state.blockAnchor = geometryCoordinates(state.draft)[0] || null;
            state.blockHeadingPoint = state.blockAnchor;
        } else {
            state.blockAnchor = null;
            state.blockHeadingPoint = null;
        }
        if (selection?.type === "asset" && entity?.asset_kind === "terminal") { activatePane("terminal"); populatePerimeterForm(entity); closeAssetContextPanel(); }
        else if (selection?.type === "asset") {
            activatePane("infrastructure");
            populateAssetForm(entity);
            openAssetContextPanel(entity?.asset_kind === "road" ? "layout" : "general");
        }
        else closeAssetContextPanel();
        if (["policy", "axis", "node", "interface", "edge"].includes(selection?.type)) activatePane("routing");
        if (selection?.type === "policy") populatePolicyForm(entity);
        if (selection?.type === "axis") populateAxisForm(entity);
        if (selection?.type === "node") populateNodeForm(entity);
        if (selection?.type === "interface") populateInterfaceForm(entity);
        if (selection?.type === "edge") populateEdgeForm(entity);
        if (["che", "scope"].includes(selection?.type)) activatePane("equipment");
        if (selection?.type === "che") populateCheForm(entity);
        if (selection?.type === "scope") populateScopeForm(entity);
        renderInspector(entity);
        refreshLists(); refreshMap();
    }

    function renderInspector(entity) {
        $("selectionTitle").textContent = entity?.name || entity?.code || "Terminal summary";
        $("selectionSubtitle").textContent = state.selected ? state.selected.type.replace("_", " ") : "Current layout";
        const omit = new Set(["geometry", "points", "point", "metadata", "linear_axes", "transfer_interfaces", "berth_segments", "components", "equipment_type_details", "rail_motion", "positions", "markers", "requirements"]);
        const rows = entity ? Object.entries(entity).filter(([key, value]) => !omit.has(key) && value != null && typeof value !== "object").slice(0, 14) : [["Object", "None"]];
        $("propertyTable").querySelector("tbody").innerHTML = rows.map(([key, value]) => `<tr><th>${escapeHtml(key.replaceAll("_", " "))}</th><td>${escapeHtml(String(value))}</td></tr>`).join("");
    }

    function escapeHtml(value) {
        const entities = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
        return String(value ?? "").replace(/[&<>"']/g, (character) => entities[character]);
    }

    function listItem(type, item, subtitle, color) {
        const id = Number(item.pk ?? item.id);
        return `<button class="list-item ${!state.draftFinalized && state.selected?.type === type && state.selected.id === id ? "active" : ""}" data-select-type="${type}" data-select-id="${id}" type="button"><span class="swatch" style="background:${color}"></span><span class="item-main"><strong>${escapeHtml(item.code || item.name)}</strong><small>${escapeHtml(subtitle)}</small></span><span class="count">${item.position_count || item.generated_edge_count || ""}</span></button>`;
    }

    function refreshLists() {
        const perimeters = state.assets.filter((item) => item.asset_kind === "terminal");
        const infrastructure = state.assets.filter((item) => item.asset_kind !== "terminal");
        if ($("perimeterList")) $("perimeterList").innerHTML = perimeters.length ? perimeters.map((item) => listItem("asset", item, "Terminal boundary", colors.terminal)).join("") : '<div class="empty">No terminal perimeter</div>';
        $("assetList").innerHTML = infrastructure.length ? infrastructure.map((item) => listItem("asset", item, item.asset_kind_display || item.asset_kind, colors[item.asset_kind] || colors.other)).join("") : '<div class="empty">No infrastructure</div>';
        $("policyList").innerHTML = state.policies.length ? state.policies.map((item) => listItem("policy", item, `${item.allowed_che_types.length || "all"} CHE type${item.allowed_che_types.length === 1 ? "" : "s"}`, "#5bbf72")).join("") : '<div class="empty">No policies</div>';
        $("axisList").innerHTML = state.axes.length ? state.axes.map((item) => listItem("axis", item, item.axis_kind.replaceAll("_", " "), "#4e8ac7")).join("") : '<div class="empty">No axes</div>';
        const manualNodes = state.nodes.filter((item) => !item.is_generated);
        $("nodeList").innerHTML = manualNodes.length ? manualNodes.map((item) => listItem("node", item, item.node_kind.replaceAll("_", " "), "#1fb7a5")).join("") : '<div class="empty">No manual nodes</div>';
        $("interfaceList").innerHTML = state.interfaces.length ? state.interfaces.map((item) => {
            const road = state.axes.find((axis) => axis.id === item.road_axis);
            const subtitle = `${item.interface_kind.replaceAll("_", " ")}${road ? ` / road ${road.code}` : ""}`;
            return listItem("interface", item, subtitle, "#e9783d");
        }).join("") : '<div class="empty">No interfaces</div>';
        const manualEdges = state.edges.filter((item) => !item.is_generated);
        $("edgeList").innerHTML = manualEdges.length ? manualEdges.map((item) => listItem("edge", item, `${item.edge_kind.replaceAll("_", " ")} / ${item.movement_mode.replaceAll("_", " ")}`, "#f28d52")).join("") : '<div class="empty">No manual connections</div>';
        $("cheList").innerHTML = state.ches.length ? state.ches.map((item) => listItem("che", item, item.equipment_type_name || "CHE", item.powered_on ? "#5bbf72" : "#667278")).join("") : '<div class="empty">No CHE</div>';
        $("scopeList").innerHTML = state.scopes.length ? state.scopes.map((item) => {
            const che = state.ches.find((value) => value.pk === item.che);
            const equipmentType = state.equipmentTypes.find((value) => value.id === item.equipment_type);
            const asset = state.assets.find((value) => value.id === item.asset);
            const axis = state.axes.find((value) => value.id === item.axis);
            return listItem("scope", { ...item, code: che?.name || equipmentType?.name || `Scope ${item.id}` }, asset?.code || axis?.code || "Unassigned", "#f2c94c");
        }).join("") : '<div class="empty">No operating scopes</div>';
        document.querySelectorAll("[data-select-type]").forEach((button) => button.addEventListener("click", () => selectEntity({ type: button.dataset.selectType, id: Number(button.dataset.selectId) })));
        $("assetCount").textContent = state.assets.length;
        $("axisCount").textContent = state.axes.length;
        $("nodeCount").textContent = state.nodes.length;
        $("edgeCount").textContent = state.edges.length;
        if ($("connectInterface")) $("connectInterface").disabled = state.selected?.type !== "interface";
        if ($("disconnectInterface")) $("disconnectInterface").disabled = state.selected?.type !== "interface" || !selectedEntity()?.road_axis;
    }

    function refreshOptions() {
        fillSelect("assetPreset", state.presets.map((item) => ({ id: item.id, label: item.name })), $("assetPreset").value, "None");
        fillSelect("assetParent", state.assets.map((item) => ({ id: item.id, label: `${item.code} - ${item.name}` })), $("assetParent").value, "None");
        ["assetPolicy", "axisPolicy", "interfacePolicy", "edgePolicy"].forEach((id) => fillSelect(id, state.policies.map((item) => ({ id: item.id, label: `${item.code} - ${item.name}` })), $(id).value, "Inherit / none"));
        ["axisAsset", "interfaceAsset"].forEach((id) => fillSelect(id, state.assets.map((item) => ({ id: item.id, label: `${item.code} - ${item.name}` })), $(id).value, "Select asset"));
        fillSelect("interfaceGrid", state.assets.filter((item) => item.storage_grid).map((item) => ({ id: item.storage_grid.id, label: item.code })), $("interfaceGrid").value, "None");
        fillSelect("interfaceAxis", state.axes.map((item) => ({ id: item.id, label: `${item.code} - ${item.name}` })), $("interfaceAxis").value, "None");
        fillSelect("interfaceRoadAxis", state.axes.filter((item) => item.axis_kind === "road_centerline").map((item) => ({ id: item.id, label: `${item.code} - ${item.name}` })), $("interfaceRoadAxis").value, "None");
        fillSelect("interfaceChe", state.ches.map((item) => ({ id: item.pk ?? item.id, label: item.name })), $("interfaceChe").value, "None");
        const nodeItems = mapTools.routingEndpointOptions(state.nodes, state.axes);
        fillSelect("edgeFrom", nodeItems, $("edgeFrom").value, "Select node");
        fillSelect("edgeTo", nodeItems, $("edgeTo").value, "Select node");
        fillSelect("cheType", state.equipmentTypes.map((item) => ({ id: item.id, label: `${item.name} (${item.equipment_type})` })), $("cheType").value, "Select type");
        fillSelect("cheAsset", state.assets.filter((item) => chePlacementAssetKinds.has(item.asset_kind)).map((item) => ({ id: item.id, label: `${item.code} - ${item.name}` })), $("cheAsset").value, "Select an area");
        fillSelect("policyCheTypes", state.equipmentTypes.map((item) => ({ id: item.id, label: `${item.name} (${item.equipment_type})` })), null);
        fillSelect("scopeAsset", state.assets.map((item) => ({ id: item.id, label: `${item.code} - ${item.name}` })), $("scopeAsset").value, "None");
        fillSelect("scopeAxis", state.axes.map((item) => ({ id: item.id, label: `${item.code} - ${item.name}` })), $("scopeAxis").value, "None");
        refreshServiceLaneOptions($("scopeServiceLane")?.value);
        refreshScopeSubjectOptions();
    }

    function refreshServiceLaneOptions(value = $("scopeServiceLane")?.value) {
        if (!$("scopeServiceLane")) return;
        const assetId = Number($("scopeAsset")?.value || 0);
        const lanes = state.axes.filter((axis) => axis.axis_kind === "road_centerline" && (!assetId || axis.asset === assetId));
        fillSelect("scopeServiceLane", lanes.map((item) => ({ id: item.id, label: `${item.code} - ${item.name}` })), value, "None");
    }

    function refreshScopeSubjectOptions(value = $("scopeSubject")?.value) {
        if (!$("scopeSubject")) return;
        const items = $("scopeSubjectKind").value === "che"
            ? state.ches.map((item) => ({ id: item.pk, label: `${item.name} - ${item.equipment_type_name || "CHE"}` }))
            : state.equipmentTypes.map((item) => ({ id: item.id, label: `${item.name} (${item.equipment_type})` }));
        fillSelect("scopeSubject", items, value, "Select equipment");
    }

    function resetPerimeterForm() {
        state.selected = null; state.draft = null; state.perimeterDraft = true;
        state.geometryEditing = true; state.draftFinalized = false;
        state.blockAnchor = null; state.blockHeadingPoint = null;
        $("perimeterForm").reset();
        $("perimeterCode").value = "TERMINAL";
        $("perimeterName").value = `${state.location?.name || "Terminal"} perimeter`;
        $("perimeterElevation").value = 0;
        $("perimeterVisible").checked = true; $("perimeterActive").checked = true;
        renderInspector(null); refreshLists(); refreshMap(); setTool("polygon");
        setStatus("Draw the terminal's outer perimeter, then save it.");
    }

    function populatePerimeterForm(asset) {
        if (!asset || !$("perimeterForm")) return;
        state.perimeterDraft = true;
        $("perimeterCode").value = asset.code; $("perimeterName").value = asset.name;
        $("perimeterElevation").value = asset.elevation_m;
        $("perimeterVisible").checked = asset.is_visible; $("perimeterActive").checked = asset.is_active;
    }

    async function savePerimeter(event) {
        event.preventDefault();
        const editing = state.selected?.type === "asset" && selectedEntity()?.asset_kind === "terminal" ? selectedEntity() : null;
        const code = $("perimeterCode").value.trim();
        const name = $("perimeterName").value.trim();
        if (!code || !name) {
            const control = !code ? $("perimeterCode") : $("perimeterName");
            setStatus(`Enter a perimeter ${!code ? "code" : "name"}.`, true); control.focus(); return;
        }
        if (!state.perimeterDraft || state.draft?.type !== "Polygon") return setStatus("Draw or select a polygon for the terminal perimeter.", true);
        const payload = {
            code, name, asset_kind: "terminal", geometry: state.draft,
            heading_deg: 0, elevation_m: number("perimeterElevation"),
            is_visible: $("perimeterVisible").checked, is_active: $("perimeterActive").checked,
            metadata: editing?.metadata || {},
        };
        try {
            const saved = await api(endpoint("layout-assets", editing?.id), { method: editing ? "PATCH" : "POST", body: JSON.stringify(payload) });
            await loadAll();
            const origin = [Number(state.location?.map_origin_longitude), Number(state.location?.map_origin_latitude)];
            if (origin.every(Number.isFinite)) map?.easeTo?.({ center: origin, duration: 450 });
            selectEntity(null); setTool("select"); setStatus(`${saved.name} saved, centered, and the boundary editor is closed.`);
        } catch (error) { setStatus(error.message, true); }
    }

    function deletePerimeter() {
        if (state.selected?.type !== "asset" || selectedEntity()?.asset_kind !== "terminal") return setStatus("Select a terminal perimeter first.", true);
        deleteSelected("asset", state.assets, "layout-assets");
    }

    function transferId(side, suffix) {
        return `transfer${side[0].toUpperCase()}${side.slice(1)}${suffix}`;
    }

    function currentYardTransferSettings() {
        const sides = {};
        transferSides.forEach((side) => {
            sides[side] = {
                enabled: $(transferId(side, "Enabled")).checked,
                interface_kind: $(transferId(side, "Kind")).value,
                offset_m: number(transferId(side, "Offset"), 2),
                position_mode: $(transferId(side, "PositionMode")).value,
                position_count: number(transferId(side, "Count"), 1),
                lane_count: number(transferId(side, "Lanes"), 1),
                lane_spacing_m: number(transferId(side, "LaneSpacing"), 3.5),
            };
        });
        return {
            sides,
            left_right_split: nullableNumber("transferLrSplit"),
            left_right_lower_side: $("transferLrLowerSide").value,
            top_bottom_split: nullableNumber("transferTbSplit"),
            top_bottom_lower_side: $("transferTbLowerSide").value,
        };
    }

    function populateYardTransferSettings(settings = {}) {
        transferSides.forEach((side) => {
            const definition = settings.sides?.[side] || {};
            $(transferId(side, "Enabled")).checked = Boolean(definition.enabled);
            $(transferId(side, "Kind")).value = definition.interface_kind || "vehicle_lane";
            $(transferId(side, "Offset")).value = definition.offset_m ?? 2;
            $(transferId(side, "PositionMode")).value = definition.position_mode || "edge";
            $(transferId(side, "Count")).value = definition.position_count || 1;
            $(transferId(side, "Lanes")).value = definition.lane_count || 1;
            $(transferId(side, "LaneSpacing")).value = definition.lane_spacing_m ?? 3.5;
        });
        $("transferLrSplit").value = settings.left_right_split ?? "";
        $("transferLrLowerSide").value = settings.left_right_lower_side || "left";
        $("transferTbSplit").value = settings.top_bottom_split ?? "";
        $("transferTbLowerSide").value = settings.top_bottom_lower_side || "bottom";
        updateTransferControls();
    }

    function yardTransferSettingsForAsset(asset) {
        if (asset.metadata?.editor_transfers) return asset.metadata.editor_transfers;
        const legacy = (asset.transfer_interfaces || []).find((item) => item.code === `${asset.code}.TP`);
        if (!legacy) return {};
        const legacySide = { start: "bottom", end: "top", left: "left", right: "right", internal: "left" }[legacy.side] || "left";
        return {
            sides: {
                [legacySide]: {
                    enabled: true,
                    interface_kind: legacy.interface_kind,
                    offset_m: Number(legacy.metadata?.offset_m || 0),
                    position_mode: "custom",
                    position_count: Math.max(1, Number(legacy.positions?.length || legacy.capacity || 1)),
                },
            },
        };
    }

    function updateTransferControls() {
        transferSides.forEach((side) => {
            const enabled = $(transferId(side, "Enabled")).checked;
            const custom = $(transferId(side, "PositionMode")).value === "custom";
            ["Kind", "Offset", "PositionMode", "Lanes", "LaneSpacing"].forEach((suffix) => { $(transferId(side, suffix)).disabled = !enabled; });
            $(transferId(side, "PositionMode")).querySelector('option[value="edge"]').disabled = $("assetKind").value !== "yard_block";
            if ($("assetKind").value !== "yard_block") $(transferId(side, "PositionMode")).value = "custom";
            $(transferId(side, "Count")).disabled = !enabled || (!custom && $("assetKind").value === "yard_block");
        });
        const bayUsesHeading = $("bayHeadingAxis").value === "true";
        const leftRightAxis = bayUsesHeading ? "row" : "bay";
        const topBottomAxis = bayUsesHeading ? "bay" : "row";
        const leftRightMaximum = number(leftRightAxis === "row" ? "maxRows" : "maxBays", 1);
        const topBottomMaximum = number(topBottomAxis === "row" ? "maxRows" : "maxBays", 1);
        $("transferLrSplitLabel").textContent = `Left/right ${leftRightAxis} split (1-${Math.max(1, leftRightMaximum - 1)})`;
        $("transferTbSplitLabel").textContent = `Top/bottom ${topBottomAxis} split (1-${Math.max(1, topBottomMaximum - 1)})`;
        $("transferLrSplit").max = Math.max(1, leftRightMaximum - 1);
        $("transferTbSplit").max = Math.max(1, topBottomMaximum - 1);
        const leftRightEnabled = $("transferLeftEnabled").checked && $("transferRightEnabled").checked;
        const topBottomEnabled = $("transferTopEnabled").checked && $("transferBottomEnabled").checked;
        $("transferLrSplit").disabled = !leftRightEnabled;
        $("transferLrLowerSide").disabled = !leftRightEnabled;
        $("transferTbSplit").disabled = !topBottomEnabled;
        $("transferTbLowerSide").disabled = !topBottomEnabled;
    }

    function resetAssetControls() {
        $("assetForm").reset();
        $("assetKind").value = "yard_block"; $("assetHeading").value = 0; $("assetElevation").value = 0;
        $("assetVisible").checked = true; $("assetActive").checked = true;
        $("gridKind").value = "automated"; $("slotHeading").value = 0; $("bayHeadingAxis").value = "false";
        $("maxBays").value = 10; $("maxRows").value = 6; $("maxTier").value = 5;
        $("pileLength").value = 6.058; $("pileWidth").value = 2.438; $("baySpacing").value = 0.4; $("rowSpacing").value = 0.4;
        $("initialBay").value = "1"; $("initialRow").value = "1";
        $("bayNaming").value = "numbers"; $("rowNaming").value = "numbers";
        $("bayOrder").value = "forward"; $("rowOrder").value = "forward";
        ["padTop", "padBottom", "padLeft", "padRight"].forEach((id) => { $(id).value = 0; });
        $("roadLanes").value = 2; $("roadDirection").value = "both"; $("roadCapacity").value = 2; $("roadWidth").value = 7; $("roadSpeed").value = 25;
        $("quayServiceLanes").value = 2; $("quayCraneLanes").value = 2; $("quayBackreachLanes").value = 1; $("quayLaneWidth").value = 3.5; $("quayLaneSpacing").value = 0.5;
        $("standaloneTransferKind").value = "ground_buffer"; $("standaloneTransferPositions").value = 4; $("standaloneTransferLanes").value = 1;
        $("standaloneTransferLaneSpacing").value = 3.5; $("standaloneTransferPositionSpacing").value = 6.5;
        $("standaloneCargoMayRest").checked = true; $("standaloneRequiresVehicle").checked = false;
        populateYardTransferSettings();
        defaultTransferSplits();
        updateAssetSections();
    }

    function resetAssetForm() {
        startAssetCreation("yard_block");
    }

    function populateAssetForm(asset) {
        if (!asset) return;
        $("assetCode").value = asset.code; $("assetName").value = asset.name; $("assetKind").value = asset.asset_kind;
        $("assetParent").value = asset.parent || ""; $("assetPolicy").value = asset.access_policy || ""; $("assetHeading").value = asset.heading_deg; $("assetElevation").value = asset.elevation_m;
        $("assetVisible").checked = asset.is_visible; $("assetActive").checked = asset.is_active;
        const grid = asset.storage_grid;
        if (grid) {
            const map = { gridKind: "grid_kind", slotHeading: "slot_heading_deg", maxBays: "max_bays", maxRows: "max_rows", maxTier: "max_tier", pileLength: "pile_length_m", pileWidth: "pile_width_m", baySpacing: "bay_spacing_m", rowSpacing: "row_spacing_m", initialBay: "initial_bay", initialRow: "initial_row", bayNaming: "bay_naming", rowNaming: "row_naming", bayOrder: "bay_order", rowOrder: "row_order", padTop: "padding_top_m", padBottom: "padding_bottom_m", padLeft: "padding_left_m", padRight: "padding_right_m" };
            Object.entries(map).forEach(([id, key]) => { if (grid[key] != null) $(id).value = grid[key]; });
            $("bayHeadingAxis").value = String(grid.bay_use_heading_axis);
        }
        const berth = asset.berth_segments?.[0];
        const generator = asset.metadata?.editor_generator || {};
        if (berth) {
            $("waterSide").value = berth.water_side; $("bollardDirection").value = berth.bollard_direction;
            $("bollardCount").value = berth.bollards?.length || 15;
            const metadata = asset.linear_axes?.find((axis) => axis.id === berth.quay_face_axis)?.metadata?.bollards || {};
            $("bollardSpacing").value = metadata.default_spacing_m || 20; $("berthStartMargin").value = metadata.start_margin_m || 0; $("berthEndMargin").value = metadata.end_margin_m || 0;
            $("bollardSpacings").value = (metadata.spacings || []).join(", ");
        }
        const generatorFields = {
            quayWidth: "width_m", quayServiceLanes: "service_lane_count", quayCraneLanes: "crane_lane_count", quayBackreachLanes: "backreach_lane_count", quayLaneWidth: "lane_width_m", quayLaneSpacing: "lane_spacing_m",
            gateInLanes: "inbound_lane_count", gateOutLanes: "outbound_lane_count", gateLaneLength: "lane_length_m",
            gateLaneWidth: "lane_width_m", gateLaneSpacing: "lane_spacing_m", railTrackCount: "track_count", railTrackLength: "track_length_m", railTrackSpacing: "track_spacing_m",
        };
        Object.entries(generatorFields).forEach(([id, key]) => { if (generator[key] != null) $(id).value = generator[key]; });
        const road = state.axes.find((axis) => axis.asset === asset.id && axis.axis_kind === "road_centerline");
        if (road) {
            $("roadLanes").value = road.lane_count;
            $("roadDirection").value = road.direction;
            $("roadCapacity").value = road.capacity;
            $("roadWidth").value = road.width_m ?? "";
            $("roadSpeed").value = road.speed_limit_kph ?? "";
        }
        const standalone = asset.transfer_interfaces?.find((item) => item.metadata?.generated_group === "standalone_transfer_zone");
        const standaloneSettings = asset.metadata?.editor_transfer_zone || {};
        if (asset.asset_kind === "transfer_zone") {
            $("standaloneTransferKind").value = standalone?.interface_kind || standaloneSettings.interface_kind || "ground_buffer";
            $("standaloneTransferPositions").value = standaloneSettings.position_count || Math.max(1, Number(standalone?.positions?.length || 4) / Math.max(1, Number(standalone?.lane_count || 1)));
            $("standaloneTransferLanes").value = standalone?.lane_count || standaloneSettings.lane_count || 1;
            $("standaloneTransferLaneSpacing").value = standalone?.lane_spacing_m || standaloneSettings.lane_spacing_m || 3.5;
            $("standaloneTransferPositionSpacing").value = standalone?.position_spacing_m || standaloneSettings.position_spacing_m || 6.5;
            $("standaloneCargoMayRest").checked = standalone?.cargo_may_rest ?? standaloneSettings.cargo_may_rest ?? true;
            $("standaloneRequiresVehicle").checked = standalone?.requires_vehicle_present ?? standaloneSettings.requires_vehicle_present ?? false;
        }
        populateYardTransferSettings(yardTransferSettingsForAsset(asset));
        updateAssetSections();
    }

    function updateAssetSections() {
        $("yardFields").classList.toggle("hidden", $("assetKind").value !== "yard_block");
        $("quayFields").classList.toggle("hidden", $("assetKind").value !== "quay");
        $("gateFields").classList.toggle("hidden", $("assetKind").value !== "gate_area");
        $("railFields").classList.toggle("hidden", $("assetKind").value !== "rail_area");
        $("roadFields").classList.toggle("hidden", $("assetKind").value !== "road");
        $("standaloneTransferFields").classList.toggle("hidden", $("assetKind").value !== "transfer_zone");
        $("transferFields").classList.toggle("hidden", !["yard_block", "quay", "rail_area"].includes($("assetKind").value));
        $("transferSplits").classList.toggle("hidden", $("assetKind").value !== "yard_block");
        updateTransferControls();
        if (!$("assetContextPanel").classList.contains("hidden")) openAssetContextPanel($("assetContextPanel").dataset.tab || "general");
    }

    function yardGridPayload() {
        const first = state.blockAnchor || geometryCoordinates(state.draft)[0] || [state.location.map_origin_longitude, state.location.map_origin_latitude];
        return {
            grid_kind: $("gridKind").value, name: $("assetName").value, max_bays: number("maxBays", 1), max_rows: number("maxRows", 1), max_tier: number("maxTier", 1),
            latitude: first[1], longitude: first[0], heading_deg: number("assetHeading"), slot_heading_deg: number("slotHeading"),
            pile_length_m: number("pileLength", 6.058), pile_width_m: number("pileWidth", 2.438), pile_height_m: 2.591,
            initial_bay: $("initialBay").value || "1", initial_row: $("initialRow").value || "1", bay_order: $("bayOrder").value, row_order: $("rowOrder").value,
            bay_naming: $("bayNaming").value, row_naming: $("rowNaming").value, bay_use_heading_axis: $("bayHeadingAxis").value === "true",
            padding_top_m: number("padTop"), padding_bottom_m: number("padBottom"), padding_left_m: number("padLeft"), padding_right_m: number("padRight"),
            bay_spacing_m: number("baySpacing"), row_spacing_m: number("rowSpacing"), is_heapzone: $("gridKind").value === "heap",
        };
    }

    function regenerateConfiguredGeometry() {
        if (!["yard_block", "quay", "gate_area", "rail_area"].includes($("assetKind").value) || (!state.draft && !state.blockAnchor)) return;
        const origin = state.blockAnchor || geometryCoordinates(state.draft)[0];
        if (!origin) return;
        state.blockAnchor = origin;
        state.draft = configuredGeometry(origin, number("assetHeading"));
        state.geometryEditing = true;
        state.draftFinalized = false;
        refreshDraft();
    }

    function validateYardTransfers() {
        if ($("assetKind").value !== "yard_block") return true;
        const settings = currentYardTransferSettings();
        const grid = yardGridPayload();
        const frame = mapTools.yardFrame(grid);
        const pairs = [
            {
                sides: ["left", "right"], value: settings.left_right_split, control: "transferLrSplit",
                maximum: Number(frame.bayUsesHeading ? grid.max_rows : grid.max_bays), label: frame.bayUsesHeading ? "row" : "bay",
            },
            {
                sides: ["top", "bottom"], value: settings.top_bottom_split, control: "transferTbSplit",
                maximum: Number(frame.bayUsesHeading ? grid.max_bays : grid.max_rows), label: frame.bayUsesHeading ? "bay" : "row",
            },
        ];
        for (const pair of pairs) {
            if (!pair.sides.every((side) => settings.sides[side].enabled)) continue;
            if (pair.maximum < 2) {
                setStatus(`At least two ${pair.label}s are required to split routing between ${pair.sides.join(" and ")}.`, true);
                return false;
            }
            if (!Number.isInteger(pair.value) || pair.value < 1 || pair.value >= pair.maximum) {
                setStatus(`Choose a ${pair.label} split from 1 to ${pair.maximum - 1} when both ${pair.sides.join(" and ")} interfaces are enabled.`, true);
                $(pair.control).focus();
                return false;
            }
        }
        return true;
    }

    function validateAssetForm() {
        const required = [
            ["assetCode", "Enter an asset code."],
            ["assetName", "Enter an asset name."],
            ["assetKind", "Select an asset type."],
        ];
        for (const [id, message] of required) {
            const control = $(id);
            if (control?.value.trim()) continue;
            setStatus(message, true);
            control.focus();
            return false;
        }
        if (!state.draft) {
            setStatus(["yard_block", "quay", "gate_area", "rail_area"].includes($("assetKind").value) ? "Place the asset corner and heading before saving." : "Draw the asset geometry first.", true);
            return false;
        }
        if ($("assetKind").value === "gate_area" && number("gateInLanes") + number("gateOutLanes") < 1) {
            setStatus("A gate requires at least one inbound or outbound lane.", true);
            return false;
        }
        return validateYardTransfers();
    }

    async function saveAsset(event) {
        event.preventDefault();
        if (!validateAssetForm()) return;
        const editing = state.selected?.type === "asset" ? selectedEntity() : null;
        const generator = currentConfiguredSettings();
        const metadata = { ...(editing?.metadata || state.duplicateMetadata || {}) };
        if (generator) metadata.editor_generator = { kind: $("assetKind").value, ...generator };
        else delete metadata.editor_generator;
        if (["yard_block", "quay", "rail_area"].includes($("assetKind").value)) metadata.editor_transfers = currentYardTransferSettings();
        else delete metadata.editor_transfers;
        if ($("assetKind").value === "transfer_zone") metadata.editor_transfer_zone = {
            interface_kind: $("standaloneTransferKind").value,
            position_count: number("standaloneTransferPositions", 4),
            lane_count: number("standaloneTransferLanes", 1),
            lane_spacing_m: number("standaloneTransferLaneSpacing", 3.5),
            position_spacing_m: number("standaloneTransferPositionSpacing", 6.5),
            cargo_may_rest: $("standaloneCargoMayRest").checked,
            requires_vehicle_present: $("standaloneRequiresVehicle").checked,
        };
        else delete metadata.editor_transfer_zone;
        const payload = {
            parent: $("assetParent").value || null, access_policy: $("assetPolicy").value || null, code: $("assetCode").value.trim(), name: $("assetName").value.trim(), asset_kind: $("assetKind").value,
            geometry: state.draft, heading_deg: number("assetHeading"), elevation_m: number("assetElevation"), is_visible: $("assetVisible").checked, is_active: $("assetActive").checked, metadata,
        };
        if (payload.asset_kind === "yard_block") payload.storage_grid_settings = yardGridPayload();
        try {
            const method = editing ? "PATCH" : "POST";
            const saved = await api(endpoint("layout-assets", editing?.id), { method, body: JSON.stringify(payload) });
            setStatus(`${saved.code} saved. Compiling related topology...`);
            if (saved.asset_kind === "quay") {
                await ensureQuay(saved);
                await ensureGeneratedAxes(saved, quayAxisSpecifications(saved), "quay_lanes");
            }
            if (saved.asset_kind === "gate_area") await ensureGeneratedAxes(saved, gateAxisSpecifications(saved), "gate_lanes");
            if (saved.asset_kind === "rail_area") await ensureGeneratedAxes(saved, railAxisSpecifications(saved), "rail_tracks");
            if (saved.asset_kind === "road") {
                const road = await ensureRoadAxis(saved);
                await connectPendingInterfaces(road);
            }
            if (saved.asset_kind === "transfer_zone") await ensureStandaloneTransfer(saved);
            if (!editing && !["quay", "gate_area", "rail_area", "road"].includes(saved.asset_kind)) await applyPresetComposition(saved);
            if (["yard_block", "quay", "rail_area"].includes(saved.asset_kind)) await ensureYardTransfers(saved, saved.metadata?.editor_transfers);
            await loadAll();
            state.duplicateMetadata = null;
            selectEntity({ type: "asset", id: saved.id }); setTool("select");
            setStatus(`${saved.code} saved and selected.`);
        } catch (error) { setStatus(error.message, true); }
    }

    async function ensureStandaloneTransfer(asset) {
        const current = state.interfaces.find((item) => item.asset === asset.id && item.metadata?.generated_group === "standalone_transfer_zone");
        const settings = asset.metadata?.editor_transfer_zone || {};
        const points = longestSegment(asset.geometry);
        if (points.length < 2) throw new Error("A transfer zone requires a polygon with a usable edge.");
        const positionCount = Math.max(1, Number(settings.position_count || 4));
        const laneCount = Math.max(1, Number(settings.lane_count || 1));
        const interfaceKind = settings.interface_kind || "ground_buffer";
        const payload = {
            asset: asset.id,
            road_axis: current?.road_axis || null,
            access_policy: asset.access_policy || null,
            code: `${asset.code}.TP`,
            name: `${asset.name} transfer`,
            interface_kind: interfaceKind,
            side: "internal",
            geometry: { type: "LineString", coordinates: points },
            capacity: positionCount * laneCount,
            lane_count: laneCount,
            lane_spacing_m: Number(settings.lane_spacing_m || 3.5),
            position_spacing_m: Number(settings.position_spacing_m || 6.5),
            cargo_may_rest: settings.cargo_may_rest ?? interfaceKind !== "vehicle_lane",
            requires_vehicle_present: settings.requires_vehicle_present ?? interfaceKind === "vehicle_lane",
            is_enabled: true,
            position_count: positionCount,
            metadata: { ...(current?.metadata || {}), generated_by: "asset_editor", generated_group: "standalone_transfer_zone" },
        };
        await api(endpoint("transfer-interfaces", current?.id), {
            method: current ? "PATCH" : "POST",
            body: JSON.stringify(payload),
        });
    }

    async function ensureQuay(asset) {
        const ring = asset.geometry?.type === "Polygon" ? asset.geometry.coordinates?.[0] || [] : [];
        const points = asset.metadata?.editor_generator && ring.length >= 2 ? [ring[0], ring[1]] : longestSegment(asset.geometry);
        if (points.length < 2) throw new Error("A quay requires a polygon or line with two distinct points.");
        const existingAxis = state.axes.find((axis) => axis.asset === asset.id && axis.axis_kind === "quay_face");
        const axisPayload = { asset: asset.id, code: `${asset.code}.FACE`, name: `${asset.name} quay face`, axis_kind: "quay_face", points, direction: "both", lane_count: 1, capacity: 1, is_enabled: true, metadata: { ...(existingAxis?.metadata || {}), generated_by: "asset_editor", generated_group: "quay_face" } };
        const axis = await api(endpoint("linear-axes", existingAxis?.id), { method: existingAxis ? "PATCH" : "POST", body: JSON.stringify(axisPayload) });
        const length = polylineLength(points);
        const existingBerth = asset.berth_segments?.[0];
        const spacings = $("bollardSpacings").value.split(",").map((value) => Number(value.trim())).filter((value) => value > 0);
        const berthPayload = {
            quay_asset: asset.id, quay_face_axis: axis.id, code: `${asset.code}.BERTH`, name: `${asset.name} berth`, start_chainage_m: 0, end_chainage_m: length,
            water_side: $("waterSide").value, bollard_direction: $("bollardDirection").value, is_active: true,
            bollard_count: number("bollardCount", 15), default_bollard_spacing_m: number("bollardSpacing", 20), start_margin_m: number("berthStartMargin"), end_margin_m: number("berthEndMargin"),
        };
        if (spacings.length) berthPayload.bollard_spacings = spacings;
        await api(endpoint("berth-segments", existingBerth?.id), { method: existingBerth ? "PATCH" : "POST", body: JSON.stringify(berthPayload) });
        const staleAxes = state.axes.filter((item) => item.asset === asset.id && item.id !== axis.id && item.axis_kind === "quay_face" && item.metadata?.generated_by === "asset_editor");
        for (const staleAxis of staleAxes) await api(endpoint("linear-axes", staleAxis.id), { method: "DELETE" });
    }

    async function ensureRoadAxis(asset) {
        const points = asset.geometry?.type === "LineString" ? deepCopy(asset.geometry.coordinates) : longestSegment(asset.geometry);
        if (points.length < 2) throw new Error("A road requires a line with at least two nodes.");
        const existing = state.axes.find((axis) => axis.asset === asset.id && axis.axis_kind === "road_centerline" && axis.metadata?.generated_by === "asset_editor");
        const payload = {
            asset: asset.id,
            access_policy: existing?.access_policy || asset.access_policy || null,
            code: existing?.code || `${asset.code}.ROAD`,
            name: existing?.name || `${asset.name} road`,
            axis_kind: "road_centerline",
            points,
            direction: $("roadDirection").value,
            lane_count: number("roadLanes", 2),
            capacity: number("roadCapacity", 2),
            width_m: nullableNumber("roadWidth"),
            speed_limit_kph: nullableNumber("roadSpeed"),
            is_enabled: true,
            metadata: { ...(existing?.metadata || {}), generated_by: "asset_editor", facility_role: "road" },
        };
        return api(endpoint("linear-axes", existing?.id), {
            method: existing ? "PATCH" : "POST",
            body: JSON.stringify(payload),
        });
    }

    function quayAxisSpecifications(asset) {
        return mapTools.configuredAssetGuides("quay", geometryCoordinates(asset.geometry)[0], asset.heading_deg, asset.metadata.editor_generator).features
            .filter((feature) => feature.geometry.type === "LineString" && String(feature.properties.role).startsWith("quay_"))
            .map((feature) => ({
                code: `${asset.code}.${feature.properties.role.replace("quay_", "").toUpperCase()}.${String(feature.properties.index).padStart(2, "0")}`,
                name: `${asset.name} ${feature.properties.label.toLowerCase()} lane`, axis_kind: "road_centerline", points: feature.geometry.coordinates,
                direction: "both", width_m: Number(asset.metadata.editor_generator.lane_width_m || 3.5), role: feature.properties.role,
            }));
    }

    function gateAxisSpecifications(asset) {
        return mapTools.configuredAssetGuides("gate_area", geometryCoordinates(asset.geometry)[0], asset.heading_deg, asset.metadata.editor_generator).features.filter((feature) => feature.geometry.type === "LineString").map((feature) => ({
            code: `${asset.code}.${feature.properties.role === "gate_in" ? "IN" : "OUT"}.${String(feature.properties.index).padStart(2, "0")}`,
            name: `${asset.name} ${feature.properties.label} lane`, axis_kind: "road_centerline", points: feature.geometry.coordinates,
            direction: feature.properties.role === "gate_in" ? "forward" : "reverse", width_m: Number(asset.metadata.editor_generator.lane_width_m || 3.5), role: feature.properties.role,
        }));
    }

    function railAxisSpecifications(asset) {
        return mapTools.configuredAssetGuides("rail_area", geometryCoordinates(asset.geometry)[0], asset.heading_deg, asset.metadata.editor_generator).features.map((feature) => ({
            code: `${asset.code}.TRACK.${String(feature.properties.index).padStart(2, "0")}`,
            name: `${asset.name} track ${feature.properties.index}`, axis_kind: "rail_track", points: feature.geometry.coordinates,
            direction: "both", width_m: null, role: "rail_track",
        }));
    }

    async function ensureGeneratedAxes(asset, specifications, generatedGroup) {
        const existing = state.axes.filter((axis) => axis.asset === asset.id && axis.metadata?.generated_by === "asset_editor");
        const expectedCodes = new Set(specifications.map((item) => item.code));
        const belongsToGroup = (axis) => {
            if (axis.metadata?.generated_group === generatedGroup) return true;
            const role = String(axis.metadata?.facility_role || "");
            if (generatedGroup === "gate_lanes") return ["gate_in", "gate_out"].includes(role);
            if (generatedGroup === "rail_tracks") return role === "rail_track";
            if (generatedGroup === "quay_lanes") return role.startsWith("quay_");
            return false;
        };
        for (const specification of specifications) {
            const axis = existing.find((item) => item.code === specification.code);
            const payload = {
                asset: asset.id, code: specification.code, name: specification.name, axis_kind: specification.axis_kind, points: specification.points,
                direction: specification.direction, lane_count: 1, capacity: 1, width_m: specification.width_m, is_enabled: true,
                metadata: { ...(axis?.metadata || {}), generated_by: "asset_editor", generated_group: generatedGroup, facility_role: specification.role },
            };
            await api(endpoint("linear-axes", axis?.id), { method: axis ? "PATCH" : "POST", body: JSON.stringify(payload) });
        }
        for (const axis of existing.filter((item) => belongsToGroup(item) && !expectedCodes.has(item.code))) await api(endpoint("linear-axes", axis.id), { method: "DELETE" });
    }

    async function ensureYardTransfers(asset, settings) {
        const existing = state.interfaces.filter((item) => item.asset === asset.id && ((item.metadata?.generated_by === "asset_editor" && item.metadata?.editor_side) || item.code === `${asset.code}.TP`));
        if (!settings || !["yard_block", "quay", "rail_area"].includes(asset.asset_kind)) {
            for (const item of existing) await api(endpoint("transfer-interfaces", item.id), { method: "DELETE" });
            return;
        }
        const isYard = asset.asset_kind === "yard_block";
        const grid = isYard ? asset.storage_grid : mapTools.assetTransferGrid(asset.asset_kind, asset.metadata?.editor_generator);
        const anchor = isYard ? [Number(grid.longitude), Number(grid.latitude)] : mapTools.assetTransferAnchor(asset.asset_kind, geometryCoordinates(asset.geometry)[0], asset.heading_deg, asset.metadata?.editor_generator);
        const ranges = isYard ? mapTools.yardTransferRanges(grid, settings) : {};
        const usedIds = new Set();
        const sideValues = { top: "end", bottom: "start", left: "left", right: "right" };
        for (const side of transferSides) {
            const definition = settings.sides?.[side];
            if (!definition?.enabled) continue;
            const current = existing.find((item) => item.metadata?.editor_side === side || (item.code === `${asset.code}.TP` && ({ start: "bottom", end: "top", left: "left", right: "right", internal: "left" }[item.side] || "left") === side));
            if (current) usedIds.add(current.id);
            const points = mapTools.yardTransferLine(anchor, Number(grid.heading_deg ?? asset.heading_deg), grid, side, definition.offset_m);
            const positionCount = definition.position_mode === "custom"
                ? Math.max(1, Number(definition.position_count || 1))
                : mapTools.yardTransferDefaultCount(grid, side);
            const payload = {
                asset: asset.id,
                storage_grid: isYard ? grid.id : null,
                axis: current?.axis || null,
                road_axis: current?.road_axis || null,
                access_policy: asset.access_policy || null,
                code: `${asset.code}.TP.${side.toUpperCase()}`,
                name: `${asset.name} ${side} transfer`,
                interface_kind: definition.interface_kind,
                side: sideValues[side],
                geometry: { type: "LineString", coordinates: points },
                ...ranges[side],
                capacity: positionCount * Math.max(1, Number(definition.lane_count || 1)),
                lane_count: Math.max(1, Number(definition.lane_count || 1)),
                lane_spacing_m: Number(definition.lane_spacing_m || 3.5),
                cargo_may_rest: definition.interface_kind === "ground_buffer",
                requires_vehicle_present: definition.interface_kind === "vehicle_lane",
                is_enabled: true,
                position_count: positionCount,
                metadata: { ...(current?.metadata || {}), generated_by: "asset_editor", editor_side: side, offset_m: Number(definition.offset_m || 0), position_mode: definition.position_mode },
            };
            await api(endpoint("transfer-interfaces", current?.id), { method: current ? "PATCH" : "POST", body: JSON.stringify(payload) });
        }
        for (const item of existing.filter((value) => !usedIds.has(value.id))) await api(endpoint("transfer-interfaces", item.id), { method: "DELETE" });
    }

    async function applyPresetComposition(asset) {
        const preset = state.presets.find((item) => String(item.id) === $("assetPreset").value);
        const composition = preset?.definition?.composition;
        if (!composition) return;
        const points = asset.geometry?.type === "LineString" ? deepCopy(asset.geometry.coordinates) : longestSegment(asset.geometry);
        if (composition === "road" || composition === "rail") {
            await api(endpoint("linear-axes"), { method: "POST", body: JSON.stringify({ asset: asset.id, code: `${asset.code}.01`, name: `${asset.name} axis`, axis_kind: composition === "road" ? "road_centerline" : "rail_track", points, direction: "both", lane_count: composition === "road" ? 2 : 1, capacity: 1, is_enabled: true }) });
        }
        if (["asc_block", "rtg_block"].includes(composition)) {
            const axisKind = composition === "asc_block" ? "crane_rail" : "transfer_lane";
            const railPoints = composition === "asc_block" ? mapTools.yardCraneRail([asset.storage_grid.longitude, asset.storage_grid.latitude], asset.heading_deg, asset.storage_grid, asset.metadata?.editor_transfers) : points;
            await api(endpoint("linear-axes"), { method: "POST", body: JSON.stringify({ asset: asset.id, code: `${asset.code}.${composition === "asc_block" ? "RAIL" : "LANE"}`, name: `${asset.name} ${axisKind.replaceAll("_", " ")}`, axis_kind: axisKind, points: railPoints, direction: "both", lane_count: 1, capacity: 1, is_enabled: true }) });
        }
    }

    async function deleteSelected(type, collection, endpointName) {
        if (state.selected?.type !== type) return;
        const entity = selectedEntity();
        if (!entity || !window.confirm(`Delete ${entity.code || entity.name}?`)) return;
        try { await api(endpoint(endpointName, entity.pk ?? entity.id), { method: "DELETE" }); selectEntity(null); await loadAll(); setStatus("Deleted."); }
        catch (error) { setStatus(error.message, true); }
    }

    function resetPolicyForm() { state.selected = null; $("policyForm").reset(); $("policyLoaded").checked = true; $("policyEmpty").checked = true; refreshLists(); refreshMap(); }
    function populatePolicyForm(policy) { if (!policy) return; $("policyCode").value = policy.code; $("policyName").value = policy.name; $("policyLoaded").checked = policy.allow_loaded; $("policyEmpty").checked = policy.allow_empty; $("policyMaxWidth").value = policy.max_width_m ?? ""; $("policyMaxHeight").value = policy.max_height_m ?? ""; $("policyMaxWeight").value = policy.max_weight_t ?? ""; Array.from($("policyCheTypes").options).forEach((item) => { item.selected = policy.allowed_che_types.includes(Number(item.value)); }); }

    async function savePolicy(event) {
        event.preventDefault();
        const editing = state.selected?.type === "policy" ? selectedEntity() : null;
        const payload = { code: $("policyCode").value.trim(), name: $("policyName").value.trim(), allowed_che_types: Array.from($("policyCheTypes").selectedOptions).map((item) => Number(item.value)), allow_loaded: $("policyLoaded").checked, allow_empty: $("policyEmpty").checked, max_width_m: nullableNumber("policyMaxWidth"), max_height_m: nullableNumber("policyMaxHeight"), max_weight_t: nullableNumber("policyMaxWeight"), rules: editing?.rules || {} };
        try { const saved = await api(endpoint("access-policies", editing?.id), { method: editing ? "PATCH" : "POST", body: JSON.stringify(payload) }); await loadAll(); selectEntity({ type: "policy", id: saved.id }); setStatus(`${saved.code} saved.`); }
        catch (error) { setStatus(error.message, true); }
    }

    function resetAxisForm() { state.selected = null; state.draft = null; state.geometryEditing = true; state.draftFinalized = false; state.pendingRoadInterfaceIds.clear(); state.roadBranchHeading = null; $("axisForm").reset(); $("axisDirection").value = "both"; $("axisLanes").value = 1; $("axisCapacity").value = 1; $("axisEnabled").checked = true; setTool("line"); refreshLists(); refreshMap(); }
    function populateAxisForm(axis) { if (!axis) return; $("axisAsset").value = axis.asset; $("axisPolicy").value = axis.access_policy || ""; $("axisCode").value = axis.code; $("axisName").value = axis.name; $("axisKind").value = axis.axis_kind; $("axisDirection").value = axis.direction; $("axisLanes").value = axis.lane_count; $("axisCapacity").value = axis.capacity; $("axisWidth").value = axis.width_m ?? ""; $("axisSpeed").value = axis.speed_limit_kph ?? ""; $("axisEnabled").checked = axis.is_enabled; }

    async function saveAxis(event) {
        event.preventDefault();
        if (state.draft?.type !== "LineString") return setStatus("Draw an axis line first.", true);
        const editing = state.selected?.type === "axis" ? selectedEntity() : null;
        const payload = { asset: Number($("axisAsset").value), access_policy: $("axisPolicy").value || null, code: $("axisCode").value.trim(), name: $("axisName").value.trim(), axis_kind: $("axisKind").value, points: state.draft.coordinates, direction: $("axisDirection").value, lane_count: number("axisLanes", 1), capacity: number("axisCapacity", 1), width_m: nullableNumber("axisWidth"), speed_limit_kph: nullableNumber("axisSpeed"), is_enabled: $("axisEnabled").checked, metadata: editing?.metadata || {} };
        try { const saved = await api(endpoint("linear-axes", editing?.id), { method: editing ? "PATCH" : "POST", body: JSON.stringify(payload) }); if (saved.axis_kind === "road_centerline") await connectPendingInterfaces(saved); await loadAll(); selectEntity({ type: "axis", id: saved.id }); setStatus(`${saved.code} saved; route edges recompiled.`); }
        catch (error) { setStatus(error.message, true); }
    }

    function resetInterfaceForm() { state.selected = null; state.draft = null; state.geometryEditing = true; state.draftFinalized = false; $("interfaceForm").reset(); $("interfaceCapacity").value = 1; $("interfacePositions").value = 1; $("interfaceLanes").value = 1; $("interfaceLaneSpacing").value = 3.5; $("interfaceReference").value = "fixed"; updateInterfaceReferenceControls(); setTool("line"); refreshLists(); refreshMap(); }
    function populateInterfaceForm(item) {
        if (!item) return;
        const values = { interfaceAsset: item.asset, interfaceGrid: item.storage_grid || "", interfaceAxis: item.axis || "", interfaceRoadAxis: item.road_axis || "", interfacePolicy: item.access_policy || "",
            interfaceCode: item.code, interfaceName: item.name, interfaceKind: item.interface_kind, interfaceSide: item.side, interfaceCapacity: item.capacity,
            interfacePositions: Math.max(1, ...(item.positions || []).map((p) => p.position_index || p.sequence || 1)),
            interfaceLanes: item.lane_count || 1, interfaceLaneSpacing: item.lane_spacing_m ?? 3.5, interfaceReference: item.position_reference || "fixed",
            interfaceChe: item.reference_che || "", interfaceForward: item.longitudinal_offset_m || 0, interfaceLateral: item.lateral_offset_m || 0,
            interfacePositionSpacing: item.position_spacing_m ?? 6.5 };
        Object.entries(values).forEach(([id, value]) => { $(id).value = value; });
        $("cargoMayRest").checked = item.cargo_may_rest; $("requiresVehicle").checked = item.requires_vehicle_present;
        updateInterfaceReferenceControls();
    }

    async function saveInterface(event) {
        event.preventDefault();
        if (!state.draft) return setStatus("Draw a point or line for the interface.", true);
        const editing = state.selected?.type === "interface" ? selectedEntity() : null;
        const payload = { asset: Number($("interfaceAsset").value), storage_grid: $("interfaceGrid").value || null, axis: $("interfaceAxis").value || null, road_axis: $("interfaceRoadAxis").value || null, access_policy: $("interfacePolicy").value || null, code: $("interfaceCode").value.trim(), name: $("interfaceName").value.trim(), interface_kind: $("interfaceKind").value, side: $("interfaceSide").value, geometry: state.draft, capacity: number("interfaceCapacity", 1), cargo_may_rest: $("cargoMayRest").checked, requires_vehicle_present: $("requiresVehicle").checked, is_enabled: true, position_count: number("interfacePositions", 1), metadata: editing?.metadata || {} };
        Object.assign(payload, {
            lane_count: number("interfaceLanes", 1), lane_spacing_m: number("interfaceLaneSpacing", 3.5),
            position_reference: $("interfaceReference").value,
            reference_che: $("interfaceReference").value === "che" ? $("interfaceChe").value || null : null,
            longitudinal_offset_m: number("interfaceForward"), lateral_offset_m: number("interfaceLateral"),
            position_spacing_m: number("interfacePositionSpacing", 6.5),
        });
        try { const saved = await api(endpoint("transfer-interfaces", editing?.id), { method: editing ? "PATCH" : "POST", body: JSON.stringify(payload) }); await loadAll(); selectEntity({ type: "interface", id: saved.id }); setStatus(`${saved.code} positions and route nodes synchronized.`); }
        catch (error) { setStatus(error.message, true); }
    }

    function updateInterfaceReferenceControls() {
        const moving = $("interfaceReference").value === "che";
        ["interfaceChe", "interfaceForward", "interfaceLateral", "interfacePositionSpacing"].forEach((id) => { $(id).disabled = !moving; });
    }

    function resetNodeForm() {
        state.selected = null; state.draft = null; $("nodeForm").reset();
        state.geometryEditing = true; state.draftFinalized = false;
        $("saveNode").disabled = false; $("deleteNode").disabled = false;
        ["nodeCode", "nodeName", "nodeKind"].forEach((id) => { $(id).disabled = false; });
        setTool("point"); renderInspector(null); refreshLists(); refreshMap();
    }

    function populateNodeForm(node) {
        if (!node) return;
        $("nodeCode").value = node.code; $("nodeName").value = node.name; $("nodeKind").value = node.node_kind;
        const locked = Boolean(node.is_generated);
        $("saveNode").disabled = locked; $("deleteNode").disabled = locked;
        ["nodeCode", "nodeName", "nodeKind"].forEach((id) => { $(id).disabled = locked; });
        if (locked) setStatus("Generated nodes are managed by their movement axis.");
    }

    async function saveNode(event) {
        event.preventDefault();
        if (state.draft?.type !== "Point") return setStatus("Place a point for the route node.", true);
        const editing = state.selected?.type === "node" ? selectedEntity() : null;
        if (editing?.is_generated) return setStatus("Generated nodes are managed by their movement axis.", true);
        const payload = { code: $("nodeCode").value.trim(), name: $("nodeName").value.trim(), node_kind: $("nodeKind").value, point: state.draft, asset: editing?.asset || null, operational_position: editing?.operational_position || null, is_enabled: editing?.is_enabled ?? true, metadata: editing?.metadata || {} };
        try { const saved = await api(endpoint("route-nodes", editing?.id), { method: editing ? "PATCH" : "POST", body: JSON.stringify(payload) }); await loadAll(); selectEntity({ type: "node", id: saved.id }); setStatus(`${saved.code} saved.`); }
        catch (error) { setStatus(error.message, true); }
    }

    function resetEdgeForm() {
        state.selected = null; state.draft = null; $("edgeForm").reset(); $("edgeCost").value = 1; $("edgeCapacity").value = 1;
        $("edgeForm").querySelectorAll("input, select, button").forEach((element) => { element.disabled = false; });
        renderInspector(null); refreshLists(); refreshMap();
    }

    function populateEdgeForm(edge) {
        if (!edge) return;
        $("edgeCode").value = edge.code; $("edgeFrom").value = edge.from_node; $("edgeTo").value = edge.to_node;
        $("edgeKind").value = edge.edge_kind; $("edgeMode").value = edge.movement_mode; $("edgePolicy").value = edge.access_policy || "";
        $("edgeCost").value = edge.base_cost; $("edgeCapacity").value = edge.capacity;
        const locked = Boolean(edge.is_generated);
        $("edgeForm").querySelectorAll("input, select, button").forEach((element) => { element.disabled = locked; });
        if (locked) setStatus("Generated connections are managed by their movement axis.");
    }

    async function saveEdge(event) {
        event.preventDefault();
        const editing = state.selected?.type === "edge" ? selectedEntity() : null;
        if (editing?.is_generated) return setStatus("Generated connections are managed by their movement axis.", true);
        const from = state.nodes.find((item) => item.id === Number($("edgeFrom").value));
        const to = state.nodes.find((item) => item.id === Number($("edgeTo").value));
        const distance = from && to ? haversine(from.point.coordinates, to.point.coordinates) : 0;
        const requirements = (editing?.requirements || []).map((item) => ({ component_type: item.component_type, role: item.role, minimum_quantity: item.minimum_quantity }));
        const payload = { from_node: from?.id, to_node: to?.id, edge_kind: $("edgeKind").value, movement_mode: $("edgeMode").value, access_policy: $("edgePolicy").value || null, distance_m: distance, capacity: number("edgeCapacity", 1), base_cost: number("edgeCost", 1), is_enabled: editing?.is_enabled ?? true, metadata: editing?.metadata || {}, requirements };
        try { const saved = await api(endpoint("route-edges", editing?.id), { method: editing ? "PATCH" : "POST", body: JSON.stringify(payload) }); await loadAll(); selectEntity({ type: "edge", id: saved.id }); setStatus("Routing connection saved."); }
        catch (error) { setStatus(error.message, true); }
    }

    function resetCheForm() { state.selected = null; state.draft = null; state.geometryEditing = true; state.draftFinalized = false; $("cheForm").reset(); $("cheRotation").value = 0; setTool("point"); refreshLists(); refreshMap(); }
    function populateCheForm(che) { if (!che) return; $("cheType").value = che.equipment_type; $("cheAsset").value = che.current_asset || ""; $("cheName").value = che.name; $("cheLatitude").value = che.latitude ?? ""; $("cheLongitude").value = che.longitude ?? ""; $("cheRotation").value = che.rotation || 0; $("chePowered").checked = che.powered_on; $("cheWorking").checked = che.working; $("cheAvailable").checked = che.is_available; $("cheDispatched").checked = che.is_dispatched; $("cheMaintenance").checked = che.is_in_maintenance; }

    function placeCheInAssignedArea() {
        if (state.selected?.type === "che") return;
        const asset = state.assets.find((item) => item.id === Number($("cheAsset").value));
        if (asset && !chePlacementAssetKinds.has(asset.asset_kind)) {
            return setStatus("Assign equipment to a quay, yard block, parking area, or rail area.", true);
        }
        const center = geometryCenter(asset?.geometry);
        if (!center) return;
        state.draft = { type: "Point", coordinates: center };
        $("cheLongitude").value = center[0].toFixed(7); $("cheLatitude").value = center[1].toFixed(7);
        refreshDraft(); setStatus(`Equipment placed at the center of ${asset.name}. Click the map to refine its position.`);
    }

    async function saveChe(event) {
        event.preventDefault();
        const equipmentType = Number($("cheType").value);
        const currentAsset = Number($("cheAsset").value);
        const name = $("cheName").value.trim();
        if (!equipmentType || !currentAsset || !name) {
            const control = !equipmentType ? $("cheType") : !currentAsset ? $("cheAsset") : $("cheName");
            setStatus(!equipmentType ? "Select an equipment type." : !currentAsset ? "Assign the equipment to a terminal area." : "Enter an equipment name.", true);
            control.focus();
            return;
        }
        const assignedAsset = state.assets.find((item) => item.id === currentAsset);
        if (!assignedAsset || !chePlacementAssetKinds.has(assignedAsset.asset_kind)) {
            setStatus("Assign equipment to a quay, yard block, parking area, or rail area.", true);
            $("cheAsset").focus();
            return;
        }
        const editing = state.selected?.type === "che" ? selectedEntity() : null;
        const point = state.draft?.type === "Point" ? state.draft.coordinates : [nullableNumber("cheLongitude"), nullableNumber("cheLatitude")];
        if (!Number.isFinite(point[0]) || !Number.isFinite(point[1])) return setStatus("Place the equipment on the map or enter valid coordinates.", true);
        const payload = { equipment_type: equipmentType, current_asset: currentAsset, name, longitude: point[0], latitude: point[1], rotation: number("cheRotation"), powered_on: $("chePowered").checked, working: $("cheWorking").checked, is_available: $("cheAvailable").checked, is_dispatched: $("cheDispatched").checked, is_in_maintenance: $("cheMaintenance").checked };
        try { const saved = await api(endpoint("ches", editing?.pk), { method: editing ? "PATCH" : "POST", body: JSON.stringify(payload) }); await loadAll(); selectEntity({ type: "che", id: saved.pk }); setStatus(`${saved.name} saved.`); }
        catch (error) { setStatus(error.message, true); }
    }

    function resetScopeForm() {
        state.selected = null;
        $("scopeForm").reset();
        $("scopeSubjectKind").value = "che";
        $("scopeEnabled").checked = true;
        refreshServiceLaneOptions(null);
        refreshScopeSubjectOptions(null);
        renderInspector(null); refreshLists(); refreshMap();
    }

    function populateScopeForm(scope) {
        if (!scope) return;
        $("scopeSubjectKind").value = scope.che ? "che" : "equipment_type";
        refreshScopeSubjectOptions(scope.che || scope.equipment_type);
        $("scopeAsset").value = scope.asset || "";
        $("scopeAxis").value = scope.axis || "";
        refreshServiceLaneOptions(scope.service_lane_axis || "");
        $("scopeMinChainage").value = scope.min_chainage_m ?? "";
        $("scopeMaxChainage").value = scope.max_chainage_m ?? "";
        $("scopeBayStart").value = scope.bay_start ?? "";
        $("scopeBayEnd").value = scope.bay_end ?? "";
        $("scopeRowStart").value = scope.row_start ?? "";
        $("scopeRowEnd").value = scope.row_end ?? "";
        $("scopeReposition").checked = scope.can_reposition;
        $("scopeEnabled").checked = scope.is_enabled;
    }

    async function saveScope(event) {
        event.preventDefault();
        const editing = state.selected?.type === "scope" ? selectedEntity() : null;
        const subject = Number($("scopeSubject").value);
        const payload = {
            che: $("scopeSubjectKind").value === "che" ? subject : null,
            equipment_type: $("scopeSubjectKind").value === "equipment_type" ? subject : null,
            asset: $("scopeAsset").value || null,
            axis: $("scopeAxis").value || null,
            service_lane_axis: $("scopeServiceLane").value || null,
            min_chainage_m: nullableNumber("scopeMinChainage"), max_chainage_m: nullableNumber("scopeMaxChainage"),
            bay_start: nullableNumber("scopeBayStart"), bay_end: nullableNumber("scopeBayEnd"),
            row_start: nullableNumber("scopeRowStart"), row_end: nullableNumber("scopeRowEnd"),
            can_reposition: $("scopeReposition").checked, is_enabled: $("scopeEnabled").checked,
        };
        try {
            const saved = await api(endpoint("che-operating-scopes", editing?.id), { method: editing ? "PATCH" : "POST", body: JSON.stringify(payload) });
            await loadAll(); selectEntity({ type: "scope", id: saved.id }); setStatus("Equipment operating scope saved.");
        } catch (error) { setStatus(error.message, true); }
    }

    function applyPresetDefaults() {
        const preset = state.presets.find((item) => String(item.id) === $("assetPreset").value);
        if (!preset) return;
        $("assetKind").value = preset.root_asset_kind;
        const defaults = preset.definition?.editor_defaults || {};
        const mapping = { heading_deg: "assetHeading", max_bays: "maxBays", max_rows: "maxRows", max_tier: "maxTier", grid_kind: "gridKind", bay_spacing_m: "baySpacing", row_spacing_m: "rowSpacing", bollard_count: "bollardCount", default_bollard_spacing_m: "bollardSpacing" };
        Object.entries(mapping).forEach(([key, id]) => { if (defaults[key] != null) $(id).value = defaults[key]; });
        updateAssetSections();
        if (["yard_block", "quay", "gate_area", "rail_area"].includes(preset.root_asset_kind)) setTool("block");
        else if (preset.root_asset_kind === "road") setTool("line");
        else setTool("polygon");
    }

    function handleAssetKindChange() {
        updateAssetSections();
        if (state.selected?.type === "asset") return;
        state.pendingRoadInterfaceIds.clear();
        state.roadBranchHeading = null;
        state.draft = null;
        state.blockAnchor = null;
        state.blockHeadingPoint = null;
        const identity = nextAssetIdentity($("assetKind").value);
        $("assetCode").value = identity.code;
        $("assetName").value = identity.name;
        defaultTransferSplits();
        if (["yard_block", "quay", "gate_area", "rail_area"].includes($("assetKind").value)) setTool("block");
        else if ($("assetKind").value === "road") setTool("line");
        else setTool("polygon");
    }

    function handleTransferSettingChange() {
        updateTransferControls();
        regenerateConfiguredGeometry();
    }

    async function validateLayout() {
        if (!state.layout?.id) return;
        try {
            const report = await api(`${endpoint("layout-versions", state.layout.id)}validate/`);
            const items = [...(report.errors || []).map((item) => ({ ...item, message: item.message || String(item), className: "error" })), ...(report.warnings || []).map((item) => ({ ...item, message: item.message || String(item), className: "" }))];
            if (!items.length) items.push({ message: "Layout topology is valid.", className: "ok" });
            $("validationReport").innerHTML = items.map(validationItemHtml).join("");
            $("validationReport").querySelectorAll("[data-validation-node-id]").forEach((button) => {
                button.addEventListener("click", () => {
                    const node = state.nodes.find((item) => item.id === Number(button.dataset.validationNodeId));
                    if (!node) return;
                    selectEntity({ type: "node", id: node.id });
                    setStatus(`${node.code} selected. Inspect the highlighted point and connect its road group to the main network.`);
                });
            });
            setStatus(report.is_valid ? "Layout validation passed." : "Layout requires corrections.", !report.is_valid);
            return report;
        } catch (error) { setStatus(error.message, true); }
    }

    function validationItemHtml(item) {
        const title = item.object ? `<strong>${escapeHtml(String(item.object))}</strong>` : "";
        const hint = item.hint ? `<p class="report-hint">${escapeHtml(item.hint)}</p>` : "";
        const groups = (item.disconnected_components || []).map((component, index) => {
            const assets = component.asset_codes?.length ? `Assets: ${component.asset_codes.join(", ")}` : "No assigned asset";
            const nodes = (component.nodes || []).map((node) => {
                const context = [node.name, node.asset_code].filter(Boolean).join(" / ");
                return `<button class="validation-node" type="button" data-validation-node-id="${Number(node.id)}"><b>${escapeHtml(node.code)}</b><span>${escapeHtml(context)}</span></button>`;
            }).join("");
            const omitted = component.omitted_node_count ? `<small>+ ${component.omitted_node_count} additional nodes</small>` : "";
            return `<details class="report-group"><summary>Group ${index + 1}: ${component.node_count} node(s) / ${escapeHtml(assets)}</summary><div class="validation-nodes">${nodes}${omitted}</div></details>`;
        }).join("");
        return `<div class="report-item ${item.className || ""}">${title}<p>${escapeHtml(item.message)}</p>${hint}${groups}</div>`;
    }

    async function publishLayout() {
        const report = await validateLayout();
        if (!report?.is_valid || !window.confirm("Publish this layout version as the operational topology?")) return;
        try { state.layout = await api(`${endpoint("layout-versions", state.layout.id)}publish/`, { method: "POST", body: "{}" }); $("layoutStatus").textContent = state.layout.status; setStatus(`Layout v${state.layout.version} published.`); }
        catch (error) { setStatus(error.message, true); }
    }

    function fitTerminal(animate = true) {
        const coordinates = state.assets.flatMap((item) => geometryCoordinates(item.geometry));
        if (!coordinates.length) return;
        const bounds = coordinates.reduce((value, coordinate) => value.extend(coordinate), new maplibregl.LngLatBounds(coordinates[0], coordinates[0]));
        map.fitBounds(bounds, { padding: 60, maxZoom: 19, duration: animate ? 500 : 0 });
    }

    async function loadAll() {
        if (!locationId) return;
        try {
            const [location, layouts, assets, policies, axes, interfaces, nodes, edges, ches, scopes, equipmentTypes, presets] = await Promise.all([
                api(`/minitos_core/api/location/${locationId}/`), api(endpoint("layout-versions")), api(endpoint("layout-assets")), api(endpoint("access-policies")), api(endpoint("linear-axes")), api(endpoint("transfer-interfaces")), api(endpoint("route-nodes")), api(endpoint("route-edges")), api(endpoint("ches")), api(endpoint("che-operating-scopes")), api("/minitos_core/equipment-types/"), api("/minitos_core/layout-presets/"),
            ]);
            state.location = location; state.layout = layouts.find((item) => item.is_current) || layouts[0] || state.layout;
            state.assets = assets; state.policies = policies; state.axes = axes; state.interfaces = interfaces; state.nodes = nodes; state.edges = edges; state.ches = ches; state.scopes = scopes; state.equipmentTypes = equipmentTypes; state.presets = presets;
            $("layoutStatus") && ($("layoutStatus").textContent = state.layout?.status || "draft");
            populateLocationForm();
            refreshLists(); refreshOptions(); refreshMap();
        } catch (error) { setStatus(`Could not load editor data: ${error.message}`, true); }
    }

    function populateLocationForm() {
        if (!state.location || !$("locationForm")) return;
        $("locationName").value = state.location.name || "";
        $("originLat").value = state.location.map_origin_latitude ?? state.location.latitude ?? "";
        $("originLon").value = state.location.map_origin_longitude ?? state.location.longitude ?? "";
        $("mapWidth").value = state.location.map_width_m ?? 1000;
        $("mapHeight").value = state.location.map_height_m ?? 1000;
        $("mapUnitsPerMeter").value = state.location.map_units_per_meter ?? 1;
        $("locationZoom").value = state.location.default_zoom ?? 16;
        $("locationAngle").value = state.location.angle ?? 0;
        $("locationTimezone").value = state.location.timezone || "UTC";
    }

    async function saveLocation(event) {
        event.preventDefault();
        const name = $("locationName").value.trim();
        const timezone = $("locationTimezone").value.trim();
        if (!name || !$("originLat").value.trim() || !$("originLon").value.trim() || !timezone) return setStatus("Name, origin coordinates, and timezone are required.", true);
        const originLat = number("originLat"); const originLon = number("originLon");
        const payload = { name, location_type: state.location?.location_type || "TL", latitude: state.location?.latitude ?? originLat, longitude: state.location?.longitude ?? originLon, map_origin_latitude: originLat, map_origin_longitude: originLon, map_width_m: number("mapWidth", 1000), map_height_m: number("mapHeight", 1000), map_units_per_meter: number("mapUnitsPerMeter", 1), default_zoom: number("locationZoom", 16), angle: number("locationAngle"), timezone };
        try {
            if (!locationId) {
                const location = await api("/minitos_core/api/location/", { method: "POST", body: JSON.stringify(payload) });
                window.location.assign(`/terminal-editor/${location.id}/`); return;
            }
            state.location = await api(`/minitos_core/api/location/${locationId}/save/`, { method: "PUT", body: JSON.stringify(payload) });
            map?.easeTo({ center: [originLon, originLat], zoom: payload.default_zoom, duration: 450 });
            setStatus("Location and map frame saved.");
        }
        catch (error) { setStatus(error.message, true); }
    }

    function bindEvents() {
        $("contextPanelBody").appendChild($("assetForm"));
        $("toggleLibrary").addEventListener("click", () => setProjectDrawer(!root.classList.contains("drawer-open")));
        $("drawerScrim").addEventListener("click", () => setProjectDrawer(false));
        $("closeContextPanel").addEventListener("click", closeAssetContextPanel);
        document.querySelectorAll("[data-context-tab]").forEach((button) => button.addEventListener("click", () => setContextTab(button.dataset.contextTab)));
        document.querySelectorAll("[data-create-kind]").forEach((button) => button.addEventListener("click", () => startAssetCreation(button.dataset.createKind)));
        document.addEventListener("keydown", onEditorKeyDown);
        $("interfaceReference").addEventListener("change", updateInterfaceReferenceControls);
        const resizer = $("panelResizer");
        const resizePanel = (width) => {
            const available = window.innerWidth - (window.innerWidth > 850 ? 310 : 0) - 240;
            const value = Math.max(230, Math.min(640, available, width));
            $("terminalEditor").style.setProperty("--sidebar", `${value}px`);
            resizer.setAttribute("aria-valuenow", Math.round(value));
            map?.resize();
        };
        let resizing = false;
        resizer.addEventListener("pointerdown", (event) => { resizing = true; resizer.setPointerCapture(event.pointerId); event.preventDefault(); });
        resizer.addEventListener("pointermove", (event) => { if (resizing) resizePanel(event.clientX); });
        resizer.addEventListener("pointerup", () => { resizing = false; });
        resizer.addEventListener("pointercancel", () => { resizing = false; });
        resizer.addEventListener("keydown", (event) => {
            if (!["ArrowLeft", "ArrowRight"].includes(event.key)) return;
            event.preventDefault(); resizePanel(resizer.getBoundingClientRect().left + 4 + (event.key === "ArrowRight" ? 20 : -20));
        });
        if (window.ResizeObserver) new window.ResizeObserver(() => map?.resize()).observe($("map"));
        document.querySelectorAll(".tab").forEach((tab) => tab.addEventListener("click", () => { activatePane(tab.dataset.pane); refreshDraft(); }));
        document.querySelectorAll("[data-tool]").forEach((button) => button.addEventListener("click", () => {
            state.createKind = null; refreshCreationToolButtons(); setTool(button.dataset.tool);
        }));
        $("finishDrawing").addEventListener("click", finishDrawing); $("clearDrawing").addEventListener("click", clearDrawing); $("fitMap").addEventListener("click", () => fitTerminal());
        $("connectInterface").addEventListener("click", beginInterfaceConnection); $("disconnectInterface").addEventListener("click", disconnectSelectedInterface);
        $("newPerimeter")?.addEventListener("click", resetPerimeterForm); $("newAsset").addEventListener("click", resetAssetForm); $("newPolicy").addEventListener("click", resetPolicyForm); $("newAxis").addEventListener("click", resetAxisForm); $("newNode").addEventListener("click", resetNodeForm); $("newInterface").addEventListener("click", resetInterfaceForm); $("newEdge").addEventListener("click", resetEdgeForm); $("newChe").addEventListener("click", resetCheForm); $("newScope").addEventListener("click", resetScopeForm);
        $("perimeterForm")?.addEventListener("submit", savePerimeter); $("assetForm").addEventListener("submit", saveAsset); $("policyForm").addEventListener("submit", savePolicy); $("axisForm").addEventListener("submit", saveAxis); $("nodeForm").addEventListener("submit", saveNode); $("interfaceForm").addEventListener("submit", saveInterface); $("edgeForm").addEventListener("submit", saveEdge); $("cheForm").addEventListener("submit", saveChe); $("scopeForm").addEventListener("submit", saveScope);
        $("deletePerimeter")?.addEventListener("click", deletePerimeter); $("deleteAsset").addEventListener("click", () => deleteSelected("asset", state.assets, "layout-assets")); $("deletePolicy").addEventListener("click", () => deleteSelected("policy", state.policies, "access-policies")); $("deleteAxis").addEventListener("click", () => deleteSelected("axis", state.axes, "linear-axes")); $("deleteNode").addEventListener("click", () => deleteSelected("node", state.nodes, "route-nodes")); $("deleteInterface").addEventListener("click", () => deleteSelected("interface", state.interfaces, "transfer-interfaces")); $("deleteEdge").addEventListener("click", () => deleteSelected("edge", state.edges, "route-edges")); $("deleteChe").addEventListener("click", () => deleteSelected("che", state.ches, "ches")); $("deleteScope").addEventListener("click", () => deleteSelected("scope", state.scopes, "che-operating-scopes"));
        $("validateLayout").addEventListener("click", validateLayout); $("publishLayout").addEventListener("click", publishLayout); $("assetKind").addEventListener("change", handleAssetKindChange); $("assetPreset").addEventListener("change", applyPresetDefaults); $("basemapStyle").addEventListener("change", changeBasemap);
        $("assetName").addEventListener("input", () => {
            if (!$("assetContextPanel").classList.contains("hidden")) $("contextPanelTitle").textContent = $("assetName").value || "Asset";
        });
        $("scopeSubjectKind").addEventListener("change", () => refreshScopeSubjectOptions(null));
        $("scopeAsset").addEventListener("change", () => refreshServiceLaneOptions(null));
        $("cheAsset").addEventListener("change", placeCheInAssignedArea);
        ["cheType", "cheRotation", "cheName", "chePowered"].forEach((id) => {
            $(id).addEventListener("input", () => refreshDraft(false));
            $(id).addEventListener("change", () => refreshDraft(false));
        });
        ["cheLatitude", "cheLongitude"].forEach((id) => $(id).addEventListener("change", () => {
            if ($("cheLatitude").value === "" || $("cheLongitude").value === "") return;
            const point = [Number($("cheLongitude").value), Number($("cheLatitude").value)];
            if (!point.every(Number.isFinite) || Math.abs(point[0]) > 180 || Math.abs(point[1]) > 90) return;
            state.draft = { type: "Point", coordinates: point };
            refreshDraft();
        }));
        ["assetHeading", "slotHeading", "maxBays", "maxRows", "pileLength", "pileWidth", "baySpacing", "rowSpacing", "bayHeadingAxis", "padTop", "padBottom", "padLeft", "padRight", "bollardCount", "bollardSpacing", "quayWidth", "waterSide", "bollardDirection", "berthStartMargin", "berthEndMargin", "bollardSpacings", "quayServiceLanes", "quayCraneLanes", "quayBackreachLanes", "quayLaneWidth", "quayLaneSpacing", "gateInLanes", "gateOutLanes", "gateLaneLength", "gateLaneWidth", "gateLaneSpacing", "railTrackCount", "railTrackLength", "railTrackSpacing"].forEach((id) => $(id).addEventListener("change", regenerateConfiguredGeometry));
        $("bayHeadingAxis").addEventListener("change", updateTransferControls);
        ["initialBay", "initialRow", "bayNaming", "rowNaming", "bayOrder", "rowOrder", "maxTier"].forEach((id) => $(id).addEventListener("change", regenerateConfiguredGeometry));
        transferSides.forEach((side) => ["Enabled", "Kind", "Offset", "PositionMode", "Count", "Lanes", "LaneSpacing"].forEach((suffix) => $(transferId(side, suffix)).addEventListener("change", handleTransferSettingChange)));
        ["transferLrSplit", "transferLrLowerSide", "transferTbSplit", "transferTbLowerSide"].forEach((id) => $(id).addEventListener("change", handleTransferSettingChange));
        $("locationForm")?.addEventListener("submit", saveLocation);
    }

    function initializeSelects() {
        fillSelect("assetKind", assetKinds.map(([id, label]) => ({ id, label })), "yard_block");
    }

    async function init() {
        initializeSelects(); bindEvents(); if (window.lucide) lucide.createIcons();
        if (locationId) { await loadAll(); setupMap(); }
        else {
            state.location = { map_origin_latitude: 0, map_origin_longitude: 0, default_zoom: 2 };
            setupMap(); $("validateLayout").disabled = true; $("publishLayout").disabled = true;
        }
        updateAssetSections(); updateTransferControls(); updateInterfaceReferenceControls(); setTool("select");
    }

    init();
}());
