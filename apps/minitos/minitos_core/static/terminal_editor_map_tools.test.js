"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");

const tools = require("./terminal_editor_map_tools.js");

test("parallel transfer lanes grow outward and saved lane geometry matches previews", () => {
    const anchor = [-3, 40];
    const grid = { max_bays: 3, max_rows: 2, pile_length_m: 6, pile_width_m: 2.5, bay_use_heading_axis: true };
    for (const side of ["top", "bottom", "left", "right"]) {
        const definition = { enabled: true, offset_m: 2, lane_count: 4, lane_spacing_m: 4, position_mode: "custom", position_count: 2 };
        const features = tools.yardTransferGuides(anchor, 71.4, grid, { sides: { [side]: definition } }).features;
        const lines = features.filter((f) => f.geometry.type === "LineString");
        assert.equal(lines.length, 4);
        assert.equal(features.filter((f) => f.geometry.type === "Point").length, 8);
        const saved = tools.transferLaneGeometries({ side: { top: "end", bottom: "start" }[side] || side,
            lane_count: 4, lane_spacing_m: 4, geometry: lines[0].geometry });
        assert.ok(Math.abs(distanceMeters(saved[1].coordinates[0], saved[0].coordinates[0]) - 4) < .05);
        assert.ok(distanceMeters(saved[3].coordinates[0], lines[3].geometry.coordinates[0]) < .05);
    }
});

test("quay transfers respect both water sides and rail areas expose a transfer frame", () => {
    const anchor = [-3, 40];
    const settings = { bollard_count: 5, default_bollard_spacing_m: 20, width_m: 25, water_side: "right" };
    const adjusted = tools.assetTransferAnchor("quay", anchor, 0, settings);
    assert.ok(adjusted[0] < anchor[0]);
    assert.equal(tools.assetTransferAnchor("quay", anchor, 0, { ...settings, water_side: "left" }), anchor);
    assert.equal(tools.yardFrame(tools.assetTransferGrid("quay", settings)).forwardLength, 80);
    const rail = tools.yardFrame(tools.assetTransferGrid("rail_area", { track_count: 3, track_length_m: 200, track_spacing_m: 5 }));
    assert.equal(rail.forwardLength, 200);
    assert.equal(rail.rightLength, 14);
});

test("logical crane rail spans the block and all start/end transfer lanes", () => {
    const anchor = [-3, 40];
    const grid = { max_bays: 5, max_rows: 8, pile_length_m: 6, pile_width_m: 2.5, bay_use_heading_axis: true };
    const rail = tools.yardCraneRail(anchor, 0, grid, { sides: {
        bottom: { enabled: true, offset_m: 2, lane_count: 2, lane_spacing_m: 4 },
        top: { enabled: true, offset_m: 2, lane_count: 4, lane_spacing_m: 4 },
    } });
    assert.ok(Math.abs(distanceMeters(rail[0], rail[1]) - 50) < .5);
    assert.ok(rail[0][1] < anchor[1]);
    assert.ok(rail[0][0] > anchor[0]);
});

function distanceMeters(a, b) {
    const meanLat = (a[1] + b[1]) / 2 * Math.PI / 180;
    return Math.hypot(
        (b[0] - a[0]) * 111320 * Math.cos(meanLat),
        (b[1] - a[1]) * 110574,
    );
}

test("CHE footprints keep their GPS center and actual length/width at any true-north heading", () => {
    const center = [-102.15, 17.95];
    for (const rotation of [0, 90, 180, 270, 71.4, -45, 405]) {
        const features = tools.equipmentFeatures({ pk: 17, name: "SC01", longitude: center[0], latitude: center[1], rotation,
            powered_on: true, equipment_type_details: { length_m: "10", width_m: "5" } }, {}, { selected: true }).features;
        const footprint = features.find((feature) => feature.properties.role === "footprint");
        const ring = footprint.geometry.coordinates[0];
        assert.equal(ring.length, 5);
        assert.deepEqual(ring[0], ring[4]);
        assert.ok(Math.abs(distanceMeters(ring[0], ring[1]) - 10) < 0.01);
        assert.ok(Math.abs(distanceMeters(ring[1], ring[2]) - 5) < 0.01);
        const midpoint = [(ring[0][0] + ring[2][0]) / 2, (ring[0][1] + ring[2][1]) / 2];
        assert.ok(distanceMeters(midpoint, center) < 0.001);
        assert.equal(footprint.properties.heading_deg, tools.normalizeHeading(rotation));
        assert.equal(footprint.properties.powered, true);
        assert.equal(footprint.properties.selected, true);
        assert.equal(new Set(features.map((feature) => feature.id)).size, 4);
        features.forEach((feature) => assert.equal(feature.properties.entityId, 17));
    }
});

test("CHE arrows point ahead of the footprint rather than following the terminal or camera bearing", () => {
    const center = [-3, 40];
    for (const rotation of [0, 90, 180, 270, 71.4]) {
        const che = { id: 1, longitude: center[0], latitude: center[1], rotation };
        const features = tools.equipmentFeatures(che, { length_m: 8, width_m: 3 }).features;
        const line = features.find((feature) => feature.properties.role === "heading").geometry.coordinates;
        const arrow = features.find((feature) => feature.properties.role === "arrowhead").geometry.coordinates[0];
        assert.ok(distanceMeters(line[1], tools.orientedPoint(center, rotation, 5.76)) < 0.001);
        assert.deepEqual(arrow[0], line[1]);
        assert.deepEqual(arrow[0], arrow.at(-1));
        assert.ok(Math.abs(tools.bearingDegrees(center, arrow[0]) - rotation) < 0.01);
    }
});

test("CHE dimensions prefer nested API data, fall back to equipment types, and handle incomplete records", () => {
    const che = { pk: 1, longitude: 0, latitude: 0, rotation: "invalid", equipment_type_details: { length_m: 12, width_m: null } };
    const footprint = tools.equipmentFeatures(che, { length_m: 9, width_m: 4 }).features[0];
    assert.equal(footprint.properties.length_m, 12);
    assert.equal(footprint.properties.width_m, 4);
    assert.equal(footprint.properties.heading_deg, 0);
    const fallback = tools.equipmentFeatures({ ...che, equipment_type_details: { length_m: -1, width_m: Infinity } }).features[0];
    assert.equal(fallback.properties.length_m, 6);
    assert.equal(fallback.properties.width_m, 3);
    for (const invalid of [null, "", NaN, Infinity, "invalid", 181]) {
        assert.equal(tools.equipmentFeatures({ ...che, longitude: invalid }).features.length, 0);
    }
    assert.equal(tools.equipmentFeatures({ ...che, latitude: 91 }).features.length, 0);
});

test("rail placement follows true-north segments and clamps to scope chainage limits", () => {
    const anchor = [-3, 40];
    const axes = [{ id: 1, asset: 10, axis_kind: "crane_rail", points: [anchor, anchor, tools.orientedPoint(anchor, 0, 100)] }];
    const motion = tools.editorRailMotion({ cheId: 17, equipmentType: { id: 2, equipment_type: "ARMG" }, assetId: 11,
        assets: [{ id: 10 }, { id: 11, parent: 10 }], axes,
        scopes: [{ che: 17, axis: 1, min_chainage_m: 20, max_chainage_m: 40 }] });
    assert.equal(motion.mode, "fixed");
    assert.equal(motion.assigned_axis_id, 1);
    assert.equal(motion.rails[0].entry_at_start, false);
    assert.equal(motion.rails[0].entry_at_end, false);
    const aligned = tools.alignEquipment({ longitude: -2.999, latitude: 40.001, rotation: 90 }, motion);
    assert.equal(aligned.longitude, -3);
    assert.equal(aligned.rotation, 0);
    assert.ok(Math.abs(tools.railProjection(axes[0].points, [aligned.longitude, aligned.latitude]).chainage_m - 40) < 0.01);
});

test("rail constraints only apply to cranes, respect disabled axes, and let an off-rail RTG keep its heading", () => {
    const axis = { id: 1, asset: 10, axis_kind: "crane_rail", points: [[-3, 40], [-3, 40.001]] };
    const base = { cheId: 17, assetId: 10, assets: [{ id: 10 }], axes: [axis], scopes: [] };
    assert.equal(tools.editorRailMotion({ ...base, equipmentType: { equipment_type: "SC" } }).mode, "free");
    const motion = tools.editorRailMotion({ ...base, equipmentType: { equipment_type: "RTG", width_m: 5 } });
    assert.equal(motion.mode, "repositionable");
    assert.equal(motion.rails[0].can_exit, true);
    assert.equal(motion.rails[0].entry_at_start, true);
    assert.equal(motion.rails[0].entry_at_end, true);
    const offRail = { longitude: -2.99, latitude: 40, rotation: 71.4 };
    assert.equal(tools.alignEquipment(offRail, motion), offRail);
    assert.equal(tools.alignEquipment(offRail, motion, true).rotation, 0);
    const disabled = tools.editorRailMotion({ ...base, axes: [{ ...axis, is_enabled: false }], equipmentType: { equipment_type: "RMG" } });
    assert.equal(disabled.rails.length, 0);
});

test("STS placement is perpendicular to its rail and points toward either quay water side", () => {
    const origin = [-3, 40];
    const face = { id: 1, asset: 10, axis_kind: "quay_face", points: [origin, tools.orientedPoint(origin, 0, 100)] };
    const rail = { id: 2, asset: 10, axis_kind: "crane_rail", points: [origin, tools.orientedPoint(origin, 0, 100)] };
    const asset = { id: 10, metadata: {}, berth_segments: [{ quay_face_axis: 1, water_side: "left", is_active: true }] };
    const input = { cheId: 4, equipmentType: { id: 4, equipment_type: "STS" }, assetId: 10,
        assets: [asset], axes: [face, rail], scopes: [] };
    let motion = tools.editorRailMotion(input);
    assert.equal(motion.rails[0].heading_offset_deg, -90);
    assert.equal(tools.alignEquipment({ longitude: origin[0], latitude: origin[1], rotation: 0 }, motion).rotation, 270);
    asset.berth_segments[0].water_side = "right";
    motion = tools.editorRailMotion(input);
    assert.equal(tools.alignEquipment({ longitude: origin[0], latitude: origin[1], rotation: 0 }, motion).rotation, 90);
    rail.points.reverse();
    motion = tools.editorRailMotion(input);
    assert.equal(tools.alignEquipment({ longitude: origin[0], latitude: origin[1], rotation: 0 }, motion).rotation, 90);
});

test("a polygon vertex can be dragged a large distance without corrupting its ring", () => {
    const geometry = {
        type: "Polygon",
        coordinates: [[[-102.15, 17.95], [-102.149, 17.95], [-102.149, 17.951], [-102.15, 17.95]]],
    };
    const destination = [-102.145, 17.956];

    const updated = tools.replaceGeometryPoint(geometry, 1, destination);

    assert.deepEqual(updated.coordinates[0][1], destination);
    assert.deepEqual(updated.coordinates[0][0], geometry.coordinates[0][0]);
    assert.deepEqual(updated.coordinates[0].at(-1), updated.coordinates[0][0]);
    assert.ok(distanceMeters(geometry.coordinates[0][1], destination) > 500);
});

test("polygon and line tools display feedback from their first point", () => {
    const first = [-102.15, 17.95];
    const pointer = [-102.149, 17.951];

    const firstClick = tools.drawingGuide("polygon", [first]);
    const preview = tools.drawingGuide("line", [first], pointer);

    assert.equal(firstClick.features.length, 1);
    assert.equal(firstClick.features[0].geometry.type, "Point");
    assert.equal(preview.features[0].geometry.type, "LineString");
    assert.deepEqual(preview.features[0].geometry.coordinates, [first, pointer]);
});

test("double-click completion does not duplicate the last vertex", () => {
    const first = [-102.15, 17.95];
    const second = [-102.149, 17.951];

    let points = tools.appendDistinctPoint([], first);
    points = tools.appendDistinctPoint(points, second);
    points = tools.appendDistinctPoint(points, second);

    assert.deepEqual(points, [first, second]);
});

test("quay guides expose water side, bollard count, and numbering direction", () => {
    const anchor = [-102.15, 17.95];
    const settings = {
        bollard_count: 4,
        default_bollard_spacing_m: 20,
        bollard_spacings: [],
        start_margin_m: 5,
        end_margin_m: 10,
        width_m: 25,
        water_side: "left",
        bollard_direction: "forward",
    };

    const forward = tools.configuredAssetGuides("quay", anchor, 0, settings);
    const reverse = tools.configuredAssetGuides("quay", anchor, 0, { ...settings, bollard_direction: "reverse" });
    const forwardBollards = forward.features.filter((feature) => feature.properties.role === "bollard");
    const reverseBollards = reverse.features.filter((feature) => feature.properties.role === "bollard");

    assert.equal(forward.features.find((feature) => feature.properties.role === "water").properties.label, "WATER");
    assert.deepEqual(forwardBollards.map((feature) => feature.properties.label), ["1", "2", "3", "4"]);
    assert.equal(reverseBollards.length, 4);
    assert.ok(forwardBollards[0].geometry.coordinates[1] < forwardBollards[3].geometry.coordinates[1]);
    assert.ok(reverseBollards[0].geometry.coordinates[1] > reverseBollards[3].geometry.coordinates[1]);
});

test("configured gates generate directional lanes and rail areas generate tracks", () => {
    const anchor = [-102.15, 17.95];
    const gate = tools.configuredAssetGuides("gate_area", anchor, 45, {
        inbound_lane_count: 2,
        outbound_lane_count: 1,
        lane_length_m: 60,
        lane_width_m: 3.5,
        lane_spacing_m: 0.5,
    });
    const rail = tools.configuredAssetGuides("rail_area", anchor, 90, {
        track_count: 3,
        track_length_m: 400,
        track_spacing_m: 5,
    });

    assert.deepEqual(gate.features.map((feature) => feature.properties.label), ["IN 1", "IN 2", "OUT 1"]);
    assert.deepEqual(gate.features.map((feature) => feature.properties.role), ["gate_in", "gate_in", "gate_out"]);
    assert.deepEqual(rail.features.map((feature) => feature.properties.label), ["TRACK 1", "TRACK 2", "TRACK 3"]);
    rail.features.forEach((feature) => assert.ok(Math.abs(distanceMeters(...feature.geometry.coordinates) - 400) < 1));
});

test("yard grid guides retain bay and row labels under true-north rotation", () => {
    const grid = {
        max_bays: 3, max_rows: 2, pile_length_m: 6, pile_width_m: 2.5,
        bay_spacing_m: 1, row_spacing_m: 0.5, bay_use_heading_axis: true,
        initial_bay: "1", initial_row: "A", bay_naming: "1,3,5...", row_naming: "A,B,C...",
        bay_order: "forward", row_order: "forward",
        padding_top_m: 0, padding_bottom_m: 0, padding_left_m: 0, padding_right_m: 0,
    };

    const guide = tools.yardGridGuides([-102.15, 17.95], 90, grid);
    const labels = guide.features.filter((feature) => feature.properties.role === "yard_label").map((feature) => feature.properties.label);

    assert.deepEqual(labels, ["B 1", "B 3", "B 5", "R A", "R B"]);
    assert.equal(guide.features.filter((feature) => feature.properties.role === "yard_grid").length, 10);
});

test("every bay/row has a closed ground cell and a matching position center", () => {
    const grid = {
        max_bays: 3, max_rows: 2, max_tier: 4, pile_length_m: 6, pile_width_m: 2.5,
        bay_spacing_m: 1, row_spacing_m: 0.5, bay_use_heading_axis: true,
        initial_bay: "1", initial_row: "A", bay_naming: "1,3,5...", row_naming: "A,B,C...",
        padding_bottom_m: 2, padding_left_m: 3,
    };
    const anchor = [-102.15, 17.95];
    for (const heading of [0, 71.4, 180]) {
        const guide = tools.yardGridGuides(anchor, heading, grid);
        const cells = guide.features.filter((feature) => feature.properties.role === "yard_cell");
        const positions = guide.features.filter((feature) => feature.properties.role === "yard_position");
        assert.equal(cells.length, 6);
        assert.equal(positions.length, 6);
        assert.deepEqual(positions.map((feature) => feature.properties.label), ["1.A", "1.B", "3.A", "3.B", "5.A", "5.B"]);
        positions.forEach((feature) => assert.equal(feature.properties.tiers, 4));
        cells.forEach((feature) => {
            const ring = feature.geometry.coordinates[0];
            assert.deepEqual(ring[0], ring.at(-1));
            assert.ok(Math.abs(distanceMeters(ring[0], ring[1]) - 6) < 0.01);
        });
        assert.deepEqual(positions[0].geometry.coordinates, tools.yardPoint(anchor, heading, grid, 5, 4.25));
    }
});

test("yard cells preserve their centers when slot heading changes and regenerate after resizing", () => {
    const anchor = [-102.15, 17.95];
    const grid = { max_bays: 2, max_rows: 3, bay_use_heading_axis: false, pile_length_m: 6, pile_width_m: 2.5 };
    const base = tools.yardGridGuides(anchor, 71.4, grid).features;
    const skewed = tools.yardGridGuides(anchor, 71.4, { ...grid, slot_heading_deg: 20 }).features;
    const positions = (features) => features.filter((feature) => feature.properties.role === "yard_position");
    const cells = (features) => features.filter((feature) => feature.properties.role === "yard_cell");
    assert.deepEqual(positions(base), positions(skewed));
    assert.notDeepEqual(cells(base)[0].geometry, cells(skewed)[0].geometry);
    const ring = cells(base)[0].geometry.coordinates[0];
    assert.ok(Math.abs(distanceMeters(ring[0], ring[1]) - 2.5) < 0.01);
    assert.ok(Math.abs(distanceMeters(ring[1], ring[2]) - 6) < 0.01);
    assert.equal(cells(tools.yardGridGuides(anchor, 71.4, { ...grid, max_bays: 4, max_rows: 2 }).features).length, 8);
});

test("saved context preserves all asset kinds and renders terminal boundaries as edges only", () => {
    const kinds = ["terminal", "terminal", "yard_block", "quay", "gate_area", "rail_area", "building", "parking"];
    const assets = kinds.map((asset_kind, index) => ({
        id: index + 1, code: `A${index}`, asset_kind,
        geometry: tools.orientedFootprint([-102.15, 17.95], index * 20, 50, 20),
    }));
    const colors = { quay: "#a8adb0", yard_block: "#e0b94c", other: "#8c979c" };
    const unselected = tools.savedAssetFeatures(assets, colors);
    const selected = tools.savedAssetFeatures(assets, colors, { type: "asset", id: 3 });
    assert.equal(unselected.assets.features.length, assets.length);
    assert.equal(selected.perimeters.features.length, 2);
    selected.perimeters.features.forEach((feature) => assert.equal(feature.geometry.type, "LineString"));
    assert.deepEqual(selected.assets.features.map((feature) => feature.geometry), unselected.assets.features.map((feature) => feature.geometry));
    assert.equal(selected.assets.features[2].properties.selected, true);
    assert.equal(unselected.assets.features[2].properties.selected, false);
    assert.equal(selected.assets.features[3].properties.color, colors.quay);
});

test("yard transfer guides and split ranges follow the configured sides", () => {
    const grid = {
        max_bays: 8, max_rows: 6, pile_length_m: 6, pile_width_m: 2.5,
        bay_spacing_m: 1, row_spacing_m: 0.5, bay_use_heading_axis: true,
        padding_top_m: 0, padding_bottom_m: 0, padding_left_m: 0, padding_right_m: 0,
    };
    const settings = {
        sides: {
            left: { enabled: true, interface_kind: "vehicle_lane", offset_m: 2, position_mode: "edge", position_count: 1 },
            right: { enabled: true, interface_kind: "ground_buffer", offset_m: 3, position_mode: "custom", position_count: 3 },
            top: { enabled: false }, bottom: { enabled: false },
        },
        left_right_split: 4,
        left_right_lower_side: "right",
        top_bottom_split: null,
        top_bottom_lower_side: "bottom",
    };

    const guide = tools.yardTransferGuides([-102.15, 17.95], 0, grid, settings);
    const ranges = tools.yardTransferRanges(grid, settings);

    assert.equal(guide.features.filter((feature) => feature.properties.role === "transfer_position").length, 11);
    assert.deepEqual({ start: ranges.right.row_start, end: ranges.right.row_end }, { start: 1, end: 4 });
    assert.deepEqual({ start: ranges.left.row_start, end: ranges.left.row_end }, { start: 5, end: 6 });
    assert.equal(ranges.left.bay_end, 8);
});

test("tool cursors distinguish selection, drawing, and moving", () => {
    const canvas = { style: {} };

    tools.applyCursor(canvas, "select", { hovering: true });
    assert.equal(canvas.style.cursor, "pointer");
    tools.applyCursor(canvas, "polygon");
    assert.equal(canvas.style.cursor, "crosshair");
    tools.applyCursor(canvas, "move");
    assert.equal(canvas.style.cursor, "move");
});

test("basemap replacement restores editor layers after every style load", () => {
    const events = [];
    const map = {
        once(name, callback) { events.push(`listen:${name}`); this.callback = callback; },
        setStyle(style, options) { events.push("set-style"); this.style = style; this.options = options; },
    };
    const satellite = tools.basemapStyle("satellite");

    tools.replaceMapStyle(map, satellite, () => events.push("restore"));

    assert.deepEqual(events, ["listen:style.load", "set-style"]);
    assert.match(satellite.glyphs, /fonts\/\{fontstack\}/);
    assert.deepEqual(map.options, { diff: false });
    map.callback();
    assert.deepEqual(events, ["listen:style.load", "set-style", "restore"]);
});

test("road drawing snaps to enabled road vertices inside the magnetic tolerance", () => {
    const vertex = [-3, 40];
    const nearby = tools.offsetLonLat(vertex, 5, 0);
    const far = tools.offsetLonLat(vertex, 12, 0);
    const axes = [
        { id: 1, code: "ROAD01", axis_kind: "road_centerline", points: [vertex, tools.offsetLonLat(vertex, 0, 100)] },
        { id: 2, code: "RAIL01", axis_kind: "rail_track", points: [nearby, far] },
        { id: 3, code: "ROAD-OFF", axis_kind: "road_centerline", is_enabled: false, points: [nearby, far] },
    ];

    const snapped = tools.roadVertexSnap(nearby, axes, 8);
    assert.deepEqual(snapped.point, vertex);
    assert.equal(snapped.axis_id, 1);
    assert.equal(snapped.axis_code, "ROAD01");
    assert.equal(tools.roadVertexSnap(far, axes, 8), null);
    assert.equal(tools.roadVertexSnap(nearby, axes, 8, 1), null);
});

test("road drawing snaps to the middle of an existing centerline", () => {
    const start = [-3, 40];
    const end = tools.offsetLonLat(start, 0, 100);
    const pointer = tools.offsetLonLat(start, 5, 45);
    const snapped = tools.roadSegmentSnap(pointer, [{
        id: 4, code: "SPINE", axis_kind: "road_centerline", points: [start, end],
    }], 8);

    assert.equal(snapped.axis_id, 4);
    assert.equal(snapped.axis_code, "SPINE");
    assert.ok(snapped.distance_m < 6);
    assert.ok(Math.abs(snapped.segment_ratio - 0.45) < 0.01);
    assert.equal(tools.roadSegmentSnap(pointer, [{ id: 4, axis_kind: "road_centerline", points: [start, end] }], 8, 4), null);
});

test("routing endpoint choices show aggregate hubs and road points but hide position noise", () => {
    const nodes = [
        { id: 1, code: "A1.NODE", name: "AB01 area", node_kind: "operational", is_generated: true, point: { coordinates: [-3, 40] }, metadata: { aggregate_kind: "asset" } },
        { id: 2, code: "A1.IF2.NODE", name: "AB01.TP interface", node_kind: "interface", is_generated: true, point: { coordinates: [-3, 40] }, metadata: { aggregate_kind: "interface" } },
        { id: 3, code: "AB01.TP.01.NODE", name: "Position 1", node_kind: "operational", is_generated: true, point: { coordinates: [-3, 40] }, metadata: { aggregate_kind: "interface_position" } },
        { id: 4, code: "ROAD.N001", name: "Road point 1", node_kind: "axis_point", is_generated: true, point: { coordinates: [-3, 40] }, metadata: { axis_id: 7, axis_index: 1 } },
        { id: 5, code: "MANUAL", name: "Manual", node_kind: "junction", is_generated: false, point: { coordinates: [-3, 40] }, metadata: {} },
    ];
    const options = tools.routingEndpointOptions(nodes, [{ id: 7, code: "ROAD01" }]);

    assert.deepEqual(options.map((item) => item.id), [1, 2, 4, 5]);
    assert.match(options[0].label, /^Area:/);
    assert.match(options[1].label, /^Interface:/);
    assert.match(options[2].label, /ROAD01 \/ point 1/);
    assert.deepEqual(tools.visibleRouteNodes(nodes).map((item) => item.id), [1, 2, 4, 5]);
});

test("roads snap directly to transfer positions and interface lane endpoints", () => {
    const origin = [-3, 40];
    const laneEnd = tools.offsetLonLat(origin, 0, 40);
    const position = tools.offsetLonLat(origin, 3, 20);
    const interfaces = [{
        id: 9, code: "AB01.TP.LEFT", geometry: { type: "LineString", coordinates: [origin, laneEnd] },
        positions: [{ longitude: position[0], latitude: position[1] }],
    }];

    const endpointSnap = tools.interfaceConnectionSnap(tools.offsetLonLat(origin, 4, 0), interfaces, 8);
    const positionSnap = tools.interfaceConnectionSnap(tools.offsetLonLat(position, 0, 4), interfaces, 8);
    assert.equal(endpointSnap.interface_id, 9);
    assert.deepEqual(endpointSnap.point, origin);
    assert.equal(positionSnap.interface_code, "AB01.TP.LEFT");
    assert.deepEqual(positionSnap.point, position);
});

test("double-click road insertion projects a new draggable vertex onto the nearest segment", () => {
    const start = [-3, 40];
    const corner = tools.offsetLonLat(start, 0, 100);
    const end = tools.offsetLonLat(corner, 100, 0);
    const pointer = tools.offsetLonLat(corner, 40, 5);
    const result = tools.insertRoadVertex([start, corner, end], pointer);

    assert.equal(result.inserted, true);
    assert.equal(result.vertex_index, 2);
    assert.equal(result.points.length, 4);
    assert.ok(result.distance_m < 6);
    assert.ok(Math.abs(result.heading_deg - 90) < 0.01);
});

test("shift branch constraint produces a perpendicular first road segment", () => {
    const origin = [-3, 40];
    const pointer = tools.offsetLonLat(origin, 30, 25);
    const constrained = tools.perpendicularPoint(origin, pointer, 0);
    assert.ok(Math.abs(tools.bearingDegrees(origin, constrained) - 90) < 0.01);
});

test("quay guides generate service, under-crane, and backreach lane centerlines", () => {
    const settings = {
        bollard_count: 3, default_bollard_spacing_m: 20, water_side: "left",
        service_lane_count: 2, crane_lane_count: 3, backreach_lane_count: 1,
        lane_width_m: 3.5, lane_spacing_m: 0.5,
    };
    const features = tools.configuredAssetGuides("quay", [-3, 40], 0, settings).features;
    assert.equal(features.filter((feature) => feature.properties.role === "quay_service").length, 2);
    assert.equal(features.filter((feature) => feature.properties.role === "quay_crane").length, 3);
    assert.equal(features.filter((feature) => feature.properties.role === "quay_backreach").length, 1);
    assert.ok(features.filter((feature) => String(feature.properties.role).startsWith("quay_")).every((feature) => feature.geometry.type === "LineString"));
});
