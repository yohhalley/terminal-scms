"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const tools = require("./terminal_editor_map_tools.js");

async function editorFixture(options = {}) {
    const elements = new Map();
    const documentEvents = {};
    function element(id) {
        if (!elements.has(id)) {
            const classes = new Set(id === "assetContextPanel" ? ["hidden"] : []);
            elements.set(id, {
            value: "", checked: false, children: [], style: { setProperty(name, value) { this[name] = value; } }, dataset: {}, events: {},
            classList: {
                add(...names) { names.forEach((name) => classes.add(name)); },
                remove(...names) { names.forEach((name) => classes.delete(name)); },
                toggle(name, force) { const active = force === undefined ? !classes.has(name) : force; active ? classes.add(name) : classes.delete(name); return active; },
                contains(name) { return classes.has(name); },
            },
            addEventListener(name, callback) { this.events[name] = callback; },
            replaceChildren() { this.children = []; this.value = ""; },
            append(child) { this.children.push(child); if (child.selected || this.children.length === 1) this.value = String(child.value); },
            appendChild(child) { this.children.push(child); return child; },
            querySelector() { return this; }, querySelectorAll() { return []; },
            reset() {}, setAttribute() {}, remove() {},
            focus() {},
            setPointerCapture() {}, getBoundingClientRect() { return { left: 336 }; },
            });
        }
        return elements.get(id);
    }
    element("terminalEditor").dataset = { locationId: "1", layoutId: "1" };
    const tabs = ["terminal", "infrastructure", "routing", "equipment"].map((pane) => {
        const tab = element(`tab-${pane}`);
        tab.dataset.pane = pane;
        tab.active = pane === "terminal";
        tab.classList.toggle = (_, active) => { tab.active = active; };
        return tab;
    });
    const toolButtons = ["select", "move", "point"].map((tool) => {
        const button = element(`tool-${tool}`);
        button.dataset.tool = tool;
        return button;
    });
    const createButtons = ["building", "parking", "yard", "yard_block", "quay", "rail_area", "gate_area", "transfer_zone", "road"].map((kind) => {
        const button = element(`create-${kind}`);
        button.dataset.createKind = kind;
        return button;
    });
    const contextTabs = ["general", "layout", "transfers"].map((name) => {
        const button = element(`context-${name}`);
        button.dataset.contextTab = name;
        return button;
    });
    const anchor = [-102.15, 17.95];
    const grid = { longitude: anchor[0], latitude: anchor[1], heading_deg: 20, max_bays: 3, max_rows: 2, max_tier: 4, bay_use_heading_axis: true };
    const assets = ["terminal", "quay", "yard_block", "gate_area", "rail_area", "building", "parking"].map((asset_kind, index) => ({
        id: index + 1, asset_kind, code: `A${index}`, name: `Asset ${index}`, heading_deg: 20,
        geometry: tools.orientedFootprint(anchor, 20, 50, 20), metadata: {},
        ...(asset_kind === "yard_block" ? { storage_grid: grid } : {}),
    }));
    assets.push(...(options.layoutAssets || []));
    const interfaces = [{ id: 1, asset: 3, code: "TP", interface_kind: "vehicle_lane", geometry: { type: "LineString", coordinates: [anchor, tools.orientedPoint(anchor, 20, 40)] }, positions: [
        { id: 1, code: "TP.1", longitude: anchor[0], latitude: anchor[1] },
        { id: 2, code: "TP.2", longitude: anchor[0] + 0.0001, latitude: anchor[1] },
    ] }];
    const equipmentTypes = [{ id: 1, name: "Truck", equipment_type: "TT", length_m: 7, width_m: 3 },
        { id: 2, name: "Straddle", equipment_type: "SC", length_m: 10, width_m: 5 }];
    const ches = [{ pk: 17, name: "SC01", equipment_type: 2, longitude: anchor[0], latitude: anchor[1], rotation: 71.4,
        powered_on: true, equipment_type_details: equipmentTypes[1] },
    { pk: 18, name: "TT01", equipment_type: 1, longitude: anchor[0] + 0.0001, latitude: anchor[1], rotation: 180, powered_on: false }];
    const axes = [];
    axes.push(...(options.roadAxes || []));
    if (options.railEquipment) {
        equipmentTypes[1].equipment_type = "RMG";
        ches[0].current_asset = 3;
        axes.push({ id: 1, asset: 3, axis_kind: "crane_rail", name: "Block rail", points: [anchor, tools.orientedPoint(anchor, 20, 50)] });
    }
    let map;
    class MockMap {
        constructor() { map = this; this.sources = new Map(); this.layers = []; this.events = {}; this.doubleClickZoom = { disable() {} }; this.dragPan = { disable() {}, enable() {} }; }
        addControl() {}
        on(name, callback) { this.events[name] = callback; }
        once(name, callback) { this.events[name] = callback; }
        addSource(id, source) { this.sources.set(id, { data: source.data, setData(data) { this.data = JSON.parse(JSON.stringify(data)); } }); }
        getSource(id) { return this.sources.get(id); }
        addLayer(layer) { this.layers.push(layer); }
        getLayer(id) { return this.layers.find((layer) => layer.id === id); }
        moveLayer(id, before) { const layer = this.getLayer(id); this.layers = this.layers.filter((item) => item !== layer); this.layers.splice(this.layers.findIndex((item) => item.id === before), 0, layer); }
        setStyle() { this.sources.clear(); this.layers = []; this.events["style.load"](); }
        fitBounds() {}
        resize() { this.resizeCount = (this.resizeCount || 0) + 1; }
        getCanvas() { return element("canvas"); }
        queryRenderedFeatures(point, options) { this.hitLayers = options?.layers || point?.layers; return this.picked ? [this.picked] : []; }
        querySourceFeatures() { return []; }
    }
    class MockMarker {
        setLngLat() { return this; }
        addTo() { return this; }
        on() { return this; }
        remove() {}
    }
    const apiData = { "layout-assets": assets, "transfer-interfaces": interfaces, "layout-versions": [{ id: 1, is_current: true }],
        ches, "equipment-types": equipmentTypes, "linear-axes": axes, "layout-presets": options.layoutPresets || [] };
    const requests = [];
    const context = {
        document: {
            cookie: "", getElementById: element, createElement: () => element(`new-${elements.size}`),
            querySelectorAll: (selector) => selector === ".tab" ? tabs
                : selector === "[data-tool]" ? toolButtons
                    : selector === "[data-create-kind]" ? createButtons
                        : selector === "[data-context-tab]" ? contextTabs : [],
            querySelector: (selector) => selector === '[data-context-tab="layout"]' ? contextTabs[1]
                : selector === '[data-context-tab="transfers"]' ? contextTabs[2]
                    : tabs.find((tab) => tab.active),
            addEventListener(name, callback) { documentEvents[name] = callback; },
        },
        window: { TerminalEditorMapTools: options.mapTools || tools, innerWidth: 1440, confirm: () => true },
        maplibregl: { Map: MockMap, NavigationControl: class {}, Marker: MockMarker, LngLatBounds: class { extend() { return this; } } },
        fetch: async (url, request = {}) => {
            const method = request.method || "GET";
            const parts = url.split("/").filter(Boolean);
            const resource = parts.at(-1);
            const payload = request.body ? JSON.parse(request.body) : null;
            requests.push({ url, method, payload });
            if (url.endsWith("/validate/")) {
                return { ok: true, status: 200, json: async () => options.validationReport || { is_valid: true, errors: [], warnings: [] } };
            }
            if (method === "POST" && resource === "layout-assets") {
                const saved = { id: Math.max(...assets.map((item) => item.id), 0) + 1, ...payload, linear_axes: [], transfer_interfaces: [], berth_segments: [] };
                assets.push(saved);
                return { ok: true, status: 201, json: async () => saved };
            }
            if (method === "POST" && resource === "linear-axes") {
                const saved = { id: Math.max(...axes.map((item) => item.id), 0) + 1, ...payload };
                axes.push(saved);
                return { ok: true, status: 201, json: async () => saved };
            }
            return { ok: true, status: 200, json: async () => url.includes("/api/location/")
                ? { name: "Test Terminal", map_origin_latitude: anchor[1], map_origin_longitude: anchor[0] }
                : apiData[resource] || [] };
        },
    };
    vm.runInNewContext(fs.readFileSync(path.join(__dirname, "terminal_editor.js"), "utf8"), context);
    await new Promise(setImmediate);
    map.events.load();
    return { map, element, assets, axes, tabs, ches, createButtons, contextTabs, documentEvents, requests };
}

function assertSavedLayout(map) {
    assert.equal(map.getSource("assets").data.features.length, 7);
    assert.equal(map.getSource("perimeters").data.features.length, 1);
    assert.equal(map.getSource("yard-grid").data.features.filter((feature) => feature.properties.role === "yard_cell").length, 6);
    assert.equal(map.getSource("interfaces").data.features.length, 2);
    assert.equal(map.getSource("ches").data.features.filter((feature) => feature.properties.role === "footprint").length, 2);
}

test("editor initialization loads all saved footprints, cells, and transfer points", async () => {
    const { map } = await editorFixture();
    assertSavedLayout(map);
    const order = map.layers.map((layer) => layer.id);
    assert.ok(order.indexOf("draft-fill") < order.indexOf("yard-cell-outline"));
    assert.ok(order.indexOf("route-node") < order.indexOf("interface-point"));
    assert.ok(map.getLayer("asset-fill").filter.some((clause) => JSON.stringify(clause).includes("perimeter")));
    map.layers.filter((layer) => layer.type === "symbol").forEach((layer) => {
        assert.equal(JSON.stringify(layer.layout["text-font"]), JSON.stringify(["Noto Sans Regular"]));
    });
});

test("selecting infrastructure and starting a new draft do not clear saved map context", async () => {
    const { map, element, assets } = await editorFixture();
    map.picked = { properties: { entityType: "asset", entityId: assets[5].id } };
    map.events.click({ point: {}, lngLat: { toArray: () => [-102.15, 17.95] } });
    assertSavedLayout(map);
    element("newAsset").events.click();
    assertSavedLayout(map);
    element("clearDrawing").events.click();
    assertSavedLayout(map);
});

test("saved infrastructure is restored when switching to satellite, dark, and streets", async () => {
    const { map, element } = await editorFixture();
    for (const style of ["satellite", "dark", "streets"]) {
        element("basemapStyle").value = style;
        element("basemapStyle").events.change();
        assertSavedLayout(map);
        assert.ok(map.getLayer("perimeter-outline"));
        assert.ok(map.getLayer("yard-cell-outline"));
        assert.ok(map.getLayer("che-footprint"));
        assert.ok(map.getLayer("che-heading"));
        assert.ok(map.getLayer("che-arrowhead"));
        assert.equal(map.getLayer("interface-position-label").layout["text-font"][0], "Noto Sans Regular");
    }
});

test("transfer-point names are only shown while their owning asset is selected", async () => {
    const { map, assets } = await editorFixture();
    const labels = () => map.getSource("interfaces").data.features.map((feature) => ({
        label: feature.properties.label,
        ownerSelected: feature.properties.ownerSelected,
    }));
    assert.ok(labels().every((item) => item.ownerSelected === false));
    assert.equal(
        JSON.stringify(map.getLayer("interface-position-label").filter),
        JSON.stringify(["==", ["get", "ownerSelected"], true]),
    );

    const yardBlock = assets.find((item) => item.asset_kind === "yard_block");
    map.picked = { properties: { entityType: "asset", entityId: yardBlock.id } };
    map.events.click({ point: {}, lngLat: { toArray: () => [-102.15, 17.95] } });
    assert.ok(labels().every((item) => item.ownerSelected === true));

    const building = assets.find((item) => item.asset_kind === "building");
    map.picked = { properties: { entityType: "asset", entityId: building.id } };
    map.events.click({ point: {}, lngLat: { toArray: () => [-102.15, 17.95] } });
    assert.ok(labels().every((item) => item.ownerSelected === false));
});

test("CHEs render as sized rectangles and arrows, and either shape remains selectable", async () => {
    const { map, element } = await editorFixture();
    assert.equal(map.getLayer("che-point"), undefined);
    const footprints = map.getSource("ches").data.features.filter((feature) => feature.properties.role === "footprint");
    assert.deepEqual(footprints.map((feature) => feature.properties.length_m), [10, 7]);
    assert.deepEqual(footprints.map((feature) => feature.properties.width_m), [5, 3]);
    assert.equal(element("map").dataset.cheFootprints, "2");
    for (const role of ["footprint", "arrowhead", "heading", "label"]) {
        map.picked = map.getSource("ches").data.features.find((feature) => feature.properties.role === role && feature.properties.entityId === 17);
        map.events.click({ point: {}, lngLat: { toArray: () => [-102.15, 17.95] } });
        assert.equal(element("cheName").value, "SC01");
        assert.ok(map.hitLayers.includes("che-footprint"));
        assert.ok(map.hitLayers.includes("che-arrowhead"));
        assertSavedLayout(map);
    }
});

test("existing CHE rotation, type, coordinates, and power have a live preview without changing saved records", async () => {
    const { map, element, ches } = await editorFixture();
    const original = JSON.stringify(ches);
    map.picked = { properties: { entityType: "che", entityId: 17 } };
    map.events.click({ point: {}, lngLat: { toArray: () => [-102.15, 17.95] } });
    element("cheRotation").value = "90";
    element("cheRotation").events.input();
    let footprint = map.getSource("ches").data.features.find((feature) => feature.properties.role === "footprint" && feature.properties.entityId === 17);
    assert.equal(footprint.properties.heading_deg, 90);
    assert.equal(footprint.properties.preview, true);
    assert.equal(map.getSource("draft").data.features.length, 0);
    element("cheType").value = "1";
    element("cheType").events.change();
    footprint = map.getSource("ches").data.features.find((feature) => feature.properties.role === "footprint" && feature.properties.entityId === 17);
    assert.equal(footprint.properties.length_m, 7);
    element("chePowered").checked = false;
    element("chePowered").events.change();
    element("cheLatitude").value = "17.953";
    element("cheLongitude").value = "-102.147";
    element("cheLatitude").events.change();
    const label = map.getSource("ches").data.features.find((feature) => feature.properties.role === "label" && feature.properties.entityId === 17);
    assert.deepEqual(label.geometry.coordinates, [-102.147, 17.953]);
    assert.equal(label.properties.powered, false);
    assertSavedLayout(map);
    assert.equal(JSON.stringify(ches), original);
    element("clearDrawing").events.click();
    const restored = map.getSource("ches").data.features.find((feature) => feature.properties.role === "footprint" && feature.properties.entityId === 17);
    assert.ok(Math.abs(restored.properties.heading_deg - 71.4) < 1e-9);
    assert.equal(restored.properties.preview, false);
});

test("placing new equipment previews a rectangle and arrow while preserving all saved equipment", async () => {
    const { map, element, tabs } = await editorFixture();
    tabs.find((tab) => tab.dataset.pane === "equipment").events.click();
    element("newChe").events.click();
    element("cheType").value = "2";
    element("cheName").value = "SC02";
    element("cheRotation").value = "270";
    map.events.click({ point: {}, lngLat: { toArray: () => [-102.148, 17.952] } });
    const features = map.getSource("ches").data.features;
    assert.equal(features.filter((feature) => feature.properties.role === "footprint").length, 3);
    const preview = features.find((feature) => feature.properties.role === "footprint" && feature.properties.preview);
    assert.equal(preview.properties.heading_deg, 270);
    assert.equal(preview.properties.length_m, 10);
    assert.equal(preview.properties.entityId, null);
    assert.equal(map.getSource("draft").data.features.length, 0);
    for (const style of ["satellite", "dark", "streets"]) {
        element("basemapStyle").value = style;
        element("basemapStyle").events.change();
        assert.equal(map.getSource("ches").data.features.filter((feature) => feature.properties.role === "footprint").length, 3);
    }
});

test("equipment assigned-area options only include CHE placement assets", async () => {
    const { element } = await editorFixture();
    const options = element("cheAsset").children.slice(1);
    assert.deepEqual(
        options.map((item) => item.value),
        [2, 3, 5, 7],
    );
    assert.deepEqual(
        options.map((item) => item.textContent),
        ["A1 - Asset 1", "A2 - Asset 2", "A4 - Asset 4", "A6 - Asset 6"],
    );
});

test("moving a CHE keeps its rectangle, arrow, and editable GPS position together", async () => {
    const { map, element, ches } = await editorFixture();
    map.picked = { properties: { entityType: "che", entityId: 17 } };
    const origin = [-102.15, 17.95];
    const destination = [-102.148, 17.953];
    map.events.click({ point: {}, lngLat: { toArray: () => origin } });
    element("tool-move").events.click();
    map.events.mousedown({ lngLat: { toArray: () => origin } });
    map.events.mousemove({ point: {}, lngLat: { lng: destination[0], lat: destination[1], toArray: () => destination } });
    map.events.mouseup();
    const features = map.getSource("ches").data.features.filter((feature) => feature.properties.entityId === 17);
    assert.deepEqual(features.find((feature) => feature.properties.role === "label").geometry.coordinates, destination);
    assert.equal(Number(element("cheLongitude").value), destination[0]);
    assert.equal(Number(element("cheLatitude").value), destination[1]);
    const arrowTip = features.find((feature) => feature.properties.role === "heading").geometry.coordinates[1];
    const expected = tools.orientedPoint(destination, 71.4, 7.2);
    assert.ok(Math.abs(arrowTip[0] - expected[0]) < 1e-9);
    assert.ok(Math.abs(arrowTip[1] - expected[1]) < 1e-9);
    assert.equal(ches[0].longitude, origin[0]);
    assert.equal(ches[0].latitude, origin[1]);
    assertSavedLayout(map);
});

test("fixed CHE assignment locks heading and dragging projects the footprint back onto its rail", async () => {
    const { map, element } = await editorFixture({ railEquipment: true });
    map.picked = { properties: { entityType: "che", entityId: 17 } };
    const origin = [-102.15, 17.95];
    map.events.click({ point: {}, lngLat: { toArray: () => origin } });
    assert.equal(element("cheRotation").disabled, true);
    const expectedHeading = tools.railProjection([origin, tools.orientedPoint(origin, 20, 50)], origin).heading_deg;
    assert.ok(Math.abs(Number(element("cheRotation").value) - expectedHeading) < 0.1);
    element("tool-move").events.click();
    map.events.mousedown({ lngLat: { toArray: () => origin } });
    const requested = [-102.148, 17.953];
    map.events.mousemove({ point: {}, lngLat: { lng: requested[0], lat: requested[1], toArray: () => requested } });
    map.events.mouseup();
    const end = tools.orientedPoint(origin, 20, 50);
    const label = map.getSource("ches").data.features.find((feature) => feature.properties.entityId === 17 && feature.properties.role === "label");
    assert.ok(Math.abs(label.geometry.coordinates[0] - end[0]) < 1e-9);
    assert.ok(Math.abs(label.geometry.coordinates[1] - end[1]) < 1e-9);
    element("cheType").value = "1";
    element("cheType").events.change();
    assert.equal(element("cheRotation").disabled, false);
    assertSavedLayout(map);
});

test("yard, quay, and rail drafts render independently configurable parallel transfer lanes", async () => {
    const { map, element, assets } = await editorFixture();
    for (const kind of ["yard_block", "quay", "rail_area"]) {
        const asset = assets.find((item) => item.asset_kind === kind);
        map.picked = { properties: { entityType: "asset", entityId: asset.id } };
        map.events.click({ point: {}, lngLat: { toArray: () => [-102.15, 17.95] } });
        element("transferTopEnabled").checked = true;
        element("transferTopLanes").value = "4";
        element("transferTopLaneSpacing").value = "4";
        element("transferTopPositionMode").value = "custom";
        element("transferTopCount").value = "2";
        element("transferTopLanes").events.change();
        const guides = map.getSource("asset-guide").data.features;
        assert.equal(guides.filter((f) => f.properties.role === "transfer_top").length, 4, kind);
        assert.equal(guides.filter((f) => f.properties.role === "transfer_position").length, 8, kind);
        assertSavedLayout(map);
    }
});

test("settings panel can be resized by pointer and keyboard without leaving the map stale", async () => {
    const { map, element } = await editorFixture();
    const resizer = element("panelResizer");
    resizer.events.pointerdown({ pointerId: 1, preventDefault() {} });
    resizer.events.pointermove({ clientX: 520 });
    assert.equal(element("terminalEditor").style["--sidebar"], "520px");
    resizer.events.pointerup();
    resizer.events.pointermove({ clientX: 600 });
    assert.equal(element("terminalEditor").style["--sidebar"], "520px");
    resizer.events.keydown({ key: "ArrowRight", preventDefault() {} });
    assert.equal(element("terminalEditor").style["--sidebar"], "360px");
    assert.equal(map.resizeCount, 2);
});

test("named map tools create configured assets and open the bottom layout panel", async () => {
    const { map, element, createButtons } = await editorFixture();
    createButtons.find((button) => button.dataset.createKind === "yard_block").events.click();
    assert.equal(element("assetKind").value, "yard_block");
    assert.equal(element("assetCode").value, "YB-01");
    assert.equal(element("assetContextPanel").classList.contains("hidden"), true);

    const start = [-102.149, 17.951];
    const end = tools.orientedPoint(start, 35, 60);
    map.events.mousedown({ lngLat: { toArray: () => start } });
    map.events.mousemove({ point: {}, originalEvent: {}, lngLat: { lng: end[0], lat: end[1], toArray: () => end } });
    map.events.mouseup({ lngLat: { toArray: () => end } });

    assert.equal(element("assetContextPanel").classList.contains("hidden"), false);
    assert.equal(element("assetContextPanel").dataset.tab, "layout");
    assert.ok(Math.abs(Number(element("assetHeading").value) - 35) < 0.2);
    assert.equal(map.getSource("draft").data.features[0].geometry.type, "Polygon");
    assertSavedLayout(map);
});

test("automated and RTG block presets default bay and row spacing to 0.4 meters", async () => {
    const presets = [{
        id: 2,
        root_asset_kind: "yard_block",
        definition: {
            composition: "rtg_block",
            editor_defaults: { grid_kind: "manual", bay_spacing_m: 0.4, row_spacing_m: 0.4 },
        },
    }];
    const { element, createButtons } = await editorFixture({ layoutPresets: presets });
    createButtons.find((button) => button.dataset.createKind === "yard_block").events.click();
    assert.equal(Number(element("baySpacing").value), 0.4);
    assert.equal(Number(element("rowSpacing").value), 0.4);

    element("assetPreset").value = "2";
    element("assetPreset").events.change();
    assert.equal(element("gridKind").value, "manual");
    assert.equal(Number(element("baySpacing").value), 0.4);
    assert.equal(Number(element("rowSpacing").value), 0.4);
});

test("polygon creation uses automatic identity and opens general configuration", async () => {
    const { map, element, createButtons } = await editorFixture();
    createButtons.find((button) => button.dataset.createKind === "building").events.click();
    const points = [[-102.149, 17.951], [-102.1488, 17.951], [-102.1488, 17.9512]];
    points.forEach((point) => map.events.click({ point: {}, lngLat: { toArray: () => point } }));
    element("finishDrawing").events.click();

    assert.equal(element("assetKind").value, "building");
    assert.equal(element("assetCode").value, "BLDG-01");
    assert.equal(element("assetContextPanel").dataset.tab, "general");
    assert.equal(element("assetContextPanel").classList.contains("hidden"), false);
    assertSavedLayout(map);
});

test("road drawing accepts its first node after a configured asset drag", async () => {
    const { map, createButtons } = await editorFixture();
    createButtons.find((button) => button.dataset.createKind === "yard_block").events.click();
    const blockStart = [-102.1495, 17.9505];
    const blockEnd = tools.orientedPoint(blockStart, 25, 50);
    map.events.mousedown({ lngLat: { toArray: () => blockStart } });
    map.events.mousemove({ point: {}, originalEvent: {}, lngLat: { lng: blockEnd[0], lat: blockEnd[1], toArray: () => blockEnd } });
    map.events.mouseup({ lngLat: { toArray: () => blockEnd } });

    createButtons.find((button) => button.dataset.createKind === "road").events.click();
    const roadPoints = [[-102.149, 17.951], [-102.1485, 17.9515]];
    roadPoints.forEach((point) => {
        const event = { point: {}, lngLat: { toArray: () => point } };
        map.events.mousedown(event);
        map.events.mouseup(event);
        map.events.click(event);
    });

    assert.deepEqual(
        map.getSource("draft").data.features[0].geometry.coordinates,
        roadPoints,
    );
    assert.equal(
        map.getSource("draw-guide").data.features.filter((feature) => feature.properties.role === "vertex").length,
        2,
    );
});

test("road drawing remains usable when an older cached helper lacks segment snapping", async () => {
    const legacyTools = { ...tools };
    delete legacyTools.roadSegmentSnap;
    const { map, createButtons } = await editorFixture({ mapTools: legacyTools });
    createButtons.find((button) => button.dataset.createKind === "road").events.click();
    const points = [[-102.149, 17.951], [-102.1485, 17.9515]];
    points.forEach((point) => map.events.click({ point: {}, lngLat: { toArray: () => point } }));

    assert.deepEqual(map.getSource("draft").data.features[0].geometry.coordinates, points);
});

test("Backspace removes the latest polygon point and Enter finishes the replacement", async () => {
    const { map, element, createButtons, documentEvents } = await editorFixture();
    createButtons.find((button) => button.dataset.createKind === "building").events.click();
    const points = [[-102.149, 17.951], [-102.1488, 17.951], [-102.1488, 17.9512]];
    points.forEach((point) => map.events.click({ point: {}, lngLat: { toArray: () => point } }));

    documentEvents.keydown({ key: "Backspace", preventDefault() {} });
    assert.equal(
        map.getSource("draw-guide").data.features.filter((feature) => feature.properties.role === "vertex").length,
        2,
    );
    const replacement = [-102.149, 17.9512];
    map.events.click({ point: {}, lngLat: { toArray: () => replacement } });
    documentEvents.keydown({ key: "Enter", preventDefault() {} });

    assert.equal(map.getSource("draft").data.features[0].geometry.type, "Polygon");
    assert.equal(element("assetContextPanel").classList.contains("hidden"), false);
    assert.equal(element("assetContextPanel").dataset.tab, "general");
});

test("right-click finishes a parking polygon and leaves it ready for configuration", async () => {
    const { map, element, createButtons, requests } = await editorFixture();
    createButtons.find((button) => button.dataset.createKind === "parking").events.click();
    const points = [[-102.149, 17.951], [-102.1488, 17.951], [-102.1488, 17.9512]];
    points.forEach((point) => map.events.click({ point: {}, lngLat: { toArray: () => point } }));
    await map.events.contextmenu({ preventDefault() {}, originalEvent: { preventDefault() {} } });

    assert.equal(map.getSource("draft").data.features[0].geometry.type, "Polygon");
    assert.equal(element("assetContextPanel").classList.contains("hidden"), false);
    assert.equal(requests.some((request) => request.method === "POST"), false);
});

test("Ctrl+C and Ctrl+V duplicate a yard block directly into movable draft mode", async () => {
    const { map, element, assets, documentEvents } = await editorFixture();
    const source = assets.find((item) => item.asset_kind === "yard_block");
    map.picked = { properties: { entityType: "asset", entityId: source.id } };
    map.events.click({ point: {}, lngLat: { toArray: () => source.geometry.coordinates[0][0] } });
    const preventDefault = () => {};
    documentEvents.keydown({ key: "c", ctrlKey: true, preventDefault });
    documentEvents.keydown({ key: "v", ctrlKey: true, preventDefault });

    const pasted = map.getSource("draft").data.features[0].geometry;
    assert.equal(element("assetCode").value, "YB-01");
    assert.equal(element("assetName").value, `${source.name} copy`);
    assert.notDeepEqual(pasted.coordinates, source.geometry.coordinates);
    assert.equal(element("tool-move").classList.contains("active"), true);

    const dragStart = pasted.coordinates[0][0];
    const dragEnd = tools.orientedPoint(dragStart, 90, 20);
    map.events.mousedown({ lngLat: { toArray: () => dragStart } });
    map.events.mousemove({ point: {}, originalEvent: {}, lngLat: { lng: dragEnd[0], lat: dragEnd[1], toArray: () => dragEnd } });
    map.events.mouseup({ lngLat: { toArray: () => dragEnd } });
    const moved = map.getSource("draft").data.features[0].geometry;
    assert.notDeepEqual(moved.coordinates, pasted.coordinates);
    assert.equal(element("tool-select").classList.contains("active"), true);
    assert.equal(element("assetContextPanel").classList.contains("hidden"), false);
    assertSavedLayout(map);
});

test("right-click finishes and saves a road while keeping it selected", async () => {
    const existingPoints = [
        [-102.1495, 17.9505],
        [-102.149, 17.951],
        [-102.1485, 17.9515],
    ];
    const existingRoad = {
        id: 90,
        code: "EXISTING",
        axis_kind: "road_centerline",
        is_enabled: true,
        points: existingPoints,
    };
    const { map, element, assets, axes, createButtons, documentEvents, requests } = await editorFixture({ roadAxes: [existingRoad] });
    createButtons.find((button) => button.dataset.createKind === "road").events.click();
    const clicks = [
        tools.offsetLonLat(existingPoints[0], 3, 0),
        tools.offsetLonLat(existingPoints[1], 0, 3),
        [-102.148, 17.952],
    ];
    clicks.forEach((point) => map.events.click({ point: {}, lngLat: { toArray: () => point } }));
    assert.deepEqual(map.getSource("draft").data.features[0].geometry.coordinates, [existingPoints[0], existingPoints[1], clicks[2]]);

    for (let index = 0; index < clicks.length; index += 1) {
        documentEvents.keydown({ key: "Backspace", preventDefault() {} });
    }
    assert.equal(map.getSource("draft").data.features.length, 0);
    assert.equal(map.getSource("draw-guide").data.features.filter((feature) => feature.properties.role === "vertex").length, 0);

    clicks.forEach((point) => map.events.click({ point: {}, lngLat: { toArray: () => point } }));
    await map.events.contextmenu({ preventDefault() {}, originalEvent: { preventDefault() {} } });

    const road = assets.find((item) => item.code === "ROAD-01");
    assert.ok(road);
    assert.equal(road.geometry.type, "LineString");
    assert.deepEqual(road.geometry.coordinates, [existingPoints[0], existingPoints[1], clicks[2]]);
    assert.equal(axes.filter((axis) => axis.asset === road.id && axis.axis_kind === "road_centerline").length, 1);
    assert.equal(element("assetCode").value, "ROAD-01");
    assert.equal(element("assetContextPanel").classList.contains("hidden"), false);
    assert.equal(element("assetContextPanel").dataset.tab, "layout");
    assert.ok(requests.some((request) => request.method === "POST" && request.url.endsWith("/layout-assets/")));
});

test("selecting a road centerline opens and deletes its owning road asset", async () => {
    const roadAsset = {
        id: 80,
        code: "ROAD-EXISTING",
        name: "Existing road",
        asset_kind: "road",
        geometry: { type: "LineString", coordinates: [[-102.1495, 17.9505], [-102.1485, 17.9515]] },
        heading_deg: 0,
        metadata: {},
        is_visible: true,
        is_active: true,
    };
    const roadAxis = {
        id: 90,
        asset: roadAsset.id,
        code: "ROAD-EXISTING.AXIS",
        name: "Existing road centerline",
        axis_kind: "road_centerline",
        is_enabled: true,
        lane_count: 2,
        direction: "both",
        capacity: 2,
        points: roadAsset.geometry.coordinates,
        metadata: { generated_by: "asset_editor" },
    };
    const { map, element, requests } = await editorFixture({ layoutAssets: [roadAsset], roadAxes: [roadAxis] });
    map.picked = { properties: { entityType: "axis", entityId: roadAxis.id } };
    map.events.click({ point: {}, lngLat: { toArray: () => roadAsset.geometry.coordinates[0] } });

    assert.equal(element("assetCode").value, roadAsset.code);
    assert.equal(element("assetContextPanel").classList.contains("hidden"), false);
    assert.equal(element("assetContextPanel").dataset.tab, "layout");
    assert.equal(map.getSource("axes").data.features.find((feature) => feature.properties.entityId === roadAxis.id).properties.selected, true);

    await element("deleteAsset").events.click();
    assert.ok(requests.some((request) => request.method === "DELETE" && request.url.endsWith(`/layout-assets/${roadAsset.id}/`)));
    assert.equal(element("assetContextPanel").classList.contains("hidden"), true);
});

test("validation explains disconnected road groups with affected assets and nodes", async () => {
    const report = {
        is_valid: true,
        errors: [],
        warnings: [{
            code: "graph_components",
            object: "Routing network",
            message: "Routing network is split into 2 disconnected groups. 2 nodes cannot reach the largest group of 5 nodes.",
            hint: "Connect one road endpoint from the listed group to the main road network.",
            disconnected_components: [{
                node_count: 2,
                asset_codes: ["ROAD-ISLAND"],
                nodes: [{ id: 91, code: "ROAD-ISLAND.N001", name: "Island point 1", asset_code: "ROAD-ISLAND" }],
                omitted_node_count: 1,
            }],
        }],
    };
    const { element } = await editorFixture({ validationReport: report });
    await element("validateLayout").events.click();

    assert.match(element("validationReport").innerHTML, /Routing network is split into 2 disconnected groups/);
    assert.match(element("validationReport").innerHTML, /ROAD-ISLAND/);
    assert.match(element("validationReport").innerHTML, /ROAD-ISLAND\.N001/);
    assert.match(element("validationReport").innerHTML, /additional nodes/);
});
