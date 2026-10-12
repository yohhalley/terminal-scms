(function (global, factory) {
    "use strict";

    const tools = factory();
    if (typeof module === "object" && module.exports) module.exports = tools;
    if (global) global.TerminalEditorMapTools = tools;
}(typeof window !== "undefined" ? window : null, function () {
    "use strict";

    const CURSORS = {
        select: "default",
        connect: "crosshair",
        polygon: "crosshair",
        rectangle: "crosshair",
        line: "crosshair",
        point: "crosshair",
        block: "crosshair",
        move: "move",
    };

    function basemapStyle(name) {
        const vectorStyles = {
            streets: "https://tiles.openfreemap.org/styles/liberty",
            dark: "https://tiles.openfreemap.org/styles/dark",
        };
        if (vectorStyles[name]) return vectorStyles[name];
        return {
            version: 8,
            glyphs: "https://tiles.openfreemap.org/fonts/{fontstack}/{range}.pbf",
            sources: {
                basemap: {
                    type: "raster",
                    tiles: ["https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"],
                    tileSize: 256,
                    attribution: "Tiles &copy; Esri and data providers",
                },
            },
            layers: [{ id: "basemap", type: "raster", source: "basemap", paint: { "raster-saturation": -0.18, "raster-opacity": 0.86 } }],
        };
    }

    function replaceMapStyle(map, style, restore) {
        map.once("style.load", restore);
        map.setStyle(style, { diff: false });
    }

    function savedAssetFeatures(assets, colors, selected = null) {
        const surfaceOrder = { water: 0, land: 1, yard: 2, quay: 3 };
        const features = assets.filter((asset) => asset.geometry?.coordinates).map((asset) => ({
            type: "Feature",
            id: asset.id,
            geometry: asset.geometry,
            properties: {
                entityType: "asset", entityId: asset.id, label: asset.code,
                color: colors[asset.asset_kind] || colors.other,
                perimeter: asset.asset_kind === "terminal",
                surfaceOrder: surfaceOrder[asset.asset_kind] ?? 4,
                selected: Boolean(selected?.type === "asset" && selected.id === asset.id),
            },
        }));
        const perimeters = features.filter((feature) => feature.properties.perimeter).flatMap((feature) => {
            const rings = feature.geometry.type === "Polygon" ? feature.geometry.coordinates : [];
            return rings.map((ring) => ({ ...feature, geometry: { type: "LineString", coordinates: ring } }));
        });
        return { assets: { type: "FeatureCollection", features }, perimeters: { type: "FeatureCollection", features: perimeters } };
    }

    function normalizeHeading(value) {
        return ((Number(value) % 360) + 360) % 360;
    }

    function offsetLonLat(origin, eastM, northM) {
        const latRad = origin[1] * Math.PI / 180;
        return [
            origin[0] + eastM / (111320 * Math.max(Math.cos(latRad), 0.01)),
            origin[1] + northM / 110574,
        ];
    }

    function headingVectors(headingDeg) {
        const heading = normalizeHeading(headingDeg) * Math.PI / 180;
        return {
            forward: [Math.sin(heading), Math.cos(heading)],
            right: [Math.cos(heading), -Math.sin(heading)],
        };
    }

    function orientedPoint(anchor, headingDeg, forwardM, rightM = 0) {
        const vectors = headingVectors(headingDeg);
        return offsetLonLat(
            anchor,
            vectors.forward[0] * forwardM + vectors.right[0] * rightM,
            vectors.forward[1] * forwardM + vectors.right[1] * rightM,
        );
    }

    function orientedFootprint(anchor, headingDeg, lengthM, widthM, side = 1) {
        const signedWidth = Number(widthM) * (Number(side) < 0 ? -1 : 1);
        const ring = [
            anchor,
            orientedPoint(anchor, headingDeg, Number(lengthM), 0),
            orientedPoint(anchor, headingDeg, Number(lengthM), signedWidth),
            orientedPoint(anchor, headingDeg, 0, signedWidth),
            anchor,
        ];
        return { type: "Polygon", coordinates: [ring] };
    }

    function railProjection(points, coordinate) {
        let total = 0;
        let best = null;
        const scale = 6371000 * Math.PI / 180;
        const cosine = Math.cos(coordinate[1] * Math.PI / 180);
        for (let index = 0; index < points.length - 1; index += 1) {
            const start = points[index], end = points[index + 1];
            const x = (start[0] - coordinate[0]) * scale * cosine;
            const y = (start[1] - coordinate[1]) * scale;
            const dx = (end[0] - start[0]) * scale * cosine;
            const dy = (end[1] - start[1]) * scale;
            const length = Math.hypot(dx, dy);
            if (!length) continue;
            const ratio = Math.max(0, Math.min(1, -(x * dx + y * dy) / (length * length)));
            const distance = Math.hypot(x + ratio * dx, y + ratio * dy);
            if (!best || distance < best.distance_m) best = {
                point: [start[0] + (end[0] - start[0]) * ratio, start[1] + (end[1] - start[1]) * ratio],
                chainage_m: total + ratio * length, distance_m: distance,
                heading_deg: normalizeHeading(Math.atan2(dx, dy) * 180 / Math.PI),
                segment_index: index,
                segment_ratio: ratio,
            };
            total += length;
        }
        return best;
    }

    function pointDistanceMeters(first, second) {
        const latitude = (Number(first[1]) + Number(second[1])) / 2 * Math.PI / 180;
        return Math.hypot(
            (Number(second[0]) - Number(first[0])) * 111320 * Math.cos(latitude),
            (Number(second[1]) - Number(first[1])) * 110574,
        );
    }

    function roadVertexSnap(coordinate, axes, toleranceM = 8, excludeAxisId = null) {
        let best = null;
        for (const axis of axes || []) {
            if (axis.axis_kind !== "road_centerline" || axis.is_enabled === false || axis.id === excludeAxisId) continue;
            (axis.points || []).forEach((point, index) => {
                if (!Array.isArray(point) || point.length < 2) return;
                const distanceM = pointDistanceMeters(coordinate, point);
                if (distanceM <= toleranceM && (!best || distanceM < best.distance_m)) {
                    best = {
                        point: [Number(point[0]), Number(point[1])],
                        distance_m: distanceM,
                        axis_id: axis.id,
                        axis_code: axis.code,
                        vertex_index: index,
                        heading_deg: bearingDegrees(
                            index < axis.points.length - 1 ? point : axis.points[index - 1],
                            index < axis.points.length - 1 ? axis.points[index + 1] : point,
                        ),
                    };
                }
            });
        }
        return best;
    }

    function roadSegmentSnap(coordinate, axes, toleranceM = 8, excludeAxisId = null) {
        let best = null;
        for (const axis of axes || []) {
            if (axis.axis_kind !== "road_centerline" || axis.is_enabled === false || axis.id === excludeAxisId) continue;
            const projection = railProjection(axis.points || [], coordinate);
            if (!projection || projection.distance_m > toleranceM || (best && projection.distance_m >= best.distance_m)) continue;
            best = {
                ...projection,
                axis_id: axis.id,
                axis_code: axis.code,
            };
        }
        return best;
    }

    function interfaceConnectionSnap(coordinate, interfaces, toleranceM = 8) {
        let best = null;
        for (const item of interfaces || []) {
            const candidates = [];
            if (item.geometry?.type === "Point") candidates.push(item.geometry.coordinates);
            if (item.geometry?.type === "LineString") {
                candidates.push(item.geometry.coordinates[0], item.geometry.coordinates.at(-1));
            }
            (item.positions || []).forEach((position) => candidates.push([position.longitude, position.latitude]));
            candidates.forEach((point) => {
                if (!Array.isArray(point) || point.length < 2 || !Number.isFinite(Number(point[0])) || !Number.isFinite(Number(point[1]))) return;
                const distanceM = pointDistanceMeters(coordinate, point);
                if (distanceM <= toleranceM && (!best || distanceM < best.distance_m)) {
                    best = {
                        point: [Number(point[0]), Number(point[1])],
                        distance_m: distanceM,
                        interface_id: item.id,
                        interface_code: item.code,
                    };
                }
            });
        }
        return best;
    }

    function insertRoadVertex(points, coordinate, endpointToleranceM = 0.5) {
        const copy = (points || []).map((point) => [...point]);
        const projection = railProjection(copy, coordinate);
        if (!projection) return null;
        const existingIndex = copy.findIndex((point) => pointDistanceMeters(point, projection.point) <= endpointToleranceM);
        if (existingIndex >= 0) return { ...projection, points: copy, vertex_index: existingIndex, inserted: false };
        const vertexIndex = projection.segment_index + 1;
        copy.splice(vertexIndex, 0, projection.point);
        return { ...projection, points: copy, vertex_index: vertexIndex, inserted: true };
    }

    function perpendicularPoint(origin, pointer, sourceHeadingDeg) {
        const meanLat = (Number(origin[1]) + Number(pointer[1])) / 2 * Math.PI / 180;
        const east = (Number(pointer[0]) - Number(origin[0])) * 111320 * Math.cos(meanLat);
        const north = (Number(pointer[1]) - Number(origin[1])) * 110574;
        const heading = normalizeHeading(sourceHeadingDeg) * Math.PI / 180;
        const right = [Math.cos(heading), -Math.sin(heading)];
        const projection = east * right[0] + north * right[1];
        const distance = Math.max(0.01, Math.abs(projection));
        return orientedPoint(origin, normalizeHeading(sourceHeadingDeg + (projection >= 0 ? 90 : -90)), distance);
    }

    function visibleRouteNodes(nodes) {
        return (nodes || []).filter((node) => node.point?.coordinates
            && node.metadata?.aggregate_kind !== "interface_position");
    }

    function routingEndpointOptions(nodes, axes = []) {
        const axisById = new Map((axes || []).map((axis) => [Number(axis.id), axis]));
        return visibleRouteNodes(nodes).filter((node) => {
            const aggregate = node.metadata?.aggregate_kind;
            return !node.is_generated || aggregate === "asset" || aggregate === "interface" || node.node_kind === "axis_point";
        }).map((node) => {
            const aggregate = node.metadata?.aggregate_kind;
            const axis = axisById.get(Number(node.metadata?.axis_id));
            let label = `${node.code} - ${node.name}`;
            if (aggregate === "asset") label = `Area: ${node.name}`;
            else if (aggregate === "interface") label = `Interface: ${node.name}`;
            else if (axis) label = `Axis: ${axis.code} / point ${node.metadata?.axis_index || "?"}`;
            return { id: node.id, label };
        });
    }

    function editorRailMotion({ cheId, equipmentType, assetId, assets, axes, scopes }) {
        const code = equipmentType?.equipment_type;
        const mode = ["STS", "QC", "RMG", "ARMG", "ASC", "RGC", "ARTG"].includes(code) ? "fixed" : code === "RTG" ? "repositionable" : "free";
        if (mode === "free") return { mode, rails: [], assigned_axis_id: null };
        const ancestors = new Set();
        let asset = assets.find((item) => item.id === assetId);
        while (asset && !ancestors.has(asset.id)) { ancestors.add(asset.id); asset = assets.find((item) => item.id === asset.parent); }
        let ranges = scopes.filter((scope) => scope.is_enabled !== false && scope.che === cheId && cheId != null);
        if (!ranges.length) ranges = scopes.filter((scope) => scope.is_enabled !== false && scope.equipment_type === equipmentType?.id);
        const kinds = ["crane_rail", ...(["STS", "QC"].includes(code) ? ["quay_face"] : mode === "fixed" ? ["rail_track"] : [])];
        const rails = axes.filter((axis) => axis.is_enabled !== false && kinds.includes(axis.axis_kind)
            && (mode !== "fixed" || ancestors.has(axis.asset) || (!ancestors.size && ranges.length))
            && (!ranges.length || ranges.some((scope) => scope.axis ? scope.axis === axis.id : scope.asset === axis.asset)))
            .sort((a, b) => kinds.indexOf(a.axis_kind) - kinds.indexOf(b.axis_kind) || a.id - b.id)
            .flatMap((axis) => {
                let points = axis.points || [];
                if (points.length < 2 || points.some((point) => point.length < 2 || !point.slice(0, 2).every(Number.isFinite))) return [];
                points = points.filter((point, index) => !index || point[0] !== points[index - 1][0] || point[1] !== points[index - 1][1]);
                if (points.length < 2) return [];
                const scope = ranges.find((item) => item.axis ? item.axis === axis.id : item.asset === axis.asset);
                const distances = [0];
                for (let index = 1; index < points.length; index += 1) distances.push(distances.at(-1) + railProjection(points.slice(index - 1, index + 1), points[index]).chainage_m);
                const length = distances.at(-1);
                if (scope && (scope.min_chainage_m != null || scope.max_chainage_m != null)) {
                    const low = Math.max(0, scope.min_chainage_m || 0), high = Math.min(distances.at(-1), scope.max_chainage_m ?? distances.at(-1));
                    if (low >= high) return [];
                    const at = (chainage) => {
                        const index = Math.max(1, distances.findIndex((distance) => distance >= chainage));
                        const ratio = (chainage - distances[index - 1]) / (distances[index] - distances[index - 1]);
                        return points[index - 1].map((value, dimension) => value + (points[index][dimension] - value) * ratio);
                    };
                    points = [at(low), ...points.filter((_, index) => distances[index] > low && distances[index] < high), at(high)];
                }
                let headingOffset = 0, waterSide = null;
                if (["STS", "QC"].includes(code)) {
                    let quay = assets.find((item) => item.id === axis.asset);
                    const visited = new Set();
                    while (quay?.parent && quay.asset_kind !== "quay" && !visited.has(quay.id)) {
                        visited.add(quay.id);
                        quay = assets.find((item) => item.id === quay.parent) || quay;
                    }
                    const berth = quay?.berth_segments?.find((item) => item.is_active !== false) || null;
                    waterSide = berth?.water_side || quay?.metadata?.editor_generator?.water_side || "left";
                    const face = axes.find((item) => item.id === berth?.quay_face_axis)
                        || axes.find((item) => item.asset === quay?.id && item.axis_kind === "quay_face") || axis;
                    const railHeading = railProjection(points, points[0])?.heading_deg ?? 0;
                    const faceHeading = railProjection(face.points || points, points[0])?.heading_deg ?? railHeading;
                    const sameDirection = Math.cos((railHeading - faceHeading) * Math.PI / 180) >= 0;
                    headingOffset = (waterSide === "left" ? -90 : 90) * (sameDirection ? 1 : -1);
                }
                return [{ axis_id: axis.id, asset_id: axis.asset, points, can_exit: mode === "repositionable" && (scope ? Boolean(scope.can_reposition) : true),
                    entry_at_start: !scope || scope.min_chainage_m == null || scope.min_chainage_m <= 0,
                    entry_at_end: !scope || scope.max_chainage_m == null || scope.max_chainage_m >= length,
                    heading_offset_deg: headingOffset, faces_water_side: waterSide != null, water_side: waterSide,
                    entry_tolerance_m: Math.max(0.5, Number(equipmentType?.width_m || 0) / 2) }];
            });
        const assigned = rails.find((rail) => ancestors.has(rail.asset_id)) || (mode === "fixed" && ranges.length ? rails[0] : null);
        return { mode, rails, assigned_axis_id: assigned?.axis_id ?? null };
    }

    function alignEquipment(che, motion = {}, force = false) {
        const rail = motion.rails?.find((item) => item.axis_id === motion.assigned_axis_id);
        if (!rail || che.longitude == null || che.latitude == null || che.longitude === "" || che.latitude === "") return che;
        const coordinate = [Number(che.longitude), Number(che.latitude)];
        if (!coordinate.every(Number.isFinite)) return che;
        const projection = railProjection(rail.points, coordinate);
        if (!projection || (motion.mode !== "fixed" && !force && projection.distance_m > rail.entry_tolerance_m)) return che;
        return { ...che, longitude: projection.point[0], latitude: projection.point[1],
            rotation: normalizeHeading(projection.heading_deg + Number(rail.heading_offset_deg || 0)), rail_axis_id: rail.axis_id };
    }

    function equipmentFeatures(che, equipmentType = {}, options = {}) {
        const collection = { type: "FeatureCollection", features: [] };
        if (che.longitude == null || che.latitude == null || che.longitude === "" || che.latitude === "") return collection;
        const center = [Number(che.longitude), Number(che.latitude)];
        if (!center.every(Number.isFinite) || Math.abs(center[0]) > 180 || Math.abs(center[1]) > 90) return collection;
        const dimension = (field, fallback) => [che.equipment_type_details?.[field], equipmentType[field]]
            .map(Number).find((value) => Number.isFinite(value) && value > 0) || fallback;
        const length = dimension("length_m", 6);
        const width = dimension("width_m", 3);
        const heading = Number.isFinite(Number(che.rotation)) ? normalizeHeading(che.rotation) : 0;
        const id = che.pk ?? che.id ?? null;
        const properties = {
            entityType: "che", entityId: id, label: che.name || "CHE",
            powered: Boolean(che.powered_on), selected: Boolean(options.selected), preview: Boolean(options.preview),
            length_m: length, width_m: width, heading_deg: heading,
        };
        const feature = (role, geometry) => ({
            type: "Feature", id: `che-${id ?? "draft"}-${role}`, geometry, properties: { ...properties, role },
        });
        // GPS is the equipment center, not the growth corner used by infrastructure.
        const corners = [[-length / 2, -width / 2], [length / 2, -width / 2], [length / 2, width / 2], [-length / 2, width / 2]]
            .map(([forward, right]) => orientedPoint(center, heading, forward, right));
        const tip = orientedPoint(center, heading, length * 0.72);
        const left = orientedPoint(center, heading, length * 0.42, -width * 0.28);
        const right = orientedPoint(center, heading, length * 0.42, width * 0.28);
        collection.features = [
            feature("footprint", { type: "Polygon", coordinates: [[...corners, corners[0]]] }),
            feature("heading", { type: "LineString", coordinates: [orientedPoint(center, heading, -length * 0.15), tip] }),
            feature("arrowhead", { type: "Polygon", coordinates: [[tip, left, right, tip]] }),
            feature("label", { type: "Point", coordinates: center }),
        ];
        return collection;
    }

    function geometryCoordinates(geometry) {
        if (!geometry) return [];
        if (geometry.type === "Point") return [geometry.coordinates];
        if (geometry.type === "LineString") return geometry.coordinates;
        if (geometry.type === "Polygon") return geometry.coordinates[0].slice(0, -1);
        return [];
    }

    function geometryFromPoints(type, points) {
        const copied = points.map((point) => [...point]);
        if (type === "Point") return copied.length ? { type, coordinates: copied[0] } : null;
        if (type === "LineString") return copied.length >= 2 ? { type, coordinates: copied } : null;
        if (type === "Polygon" && copied.length >= 3) return { type, coordinates: [[...copied, copied[0]]] };
        return null;
    }

    function replaceGeometryPoint(geometry, index, coordinate) {
        const points = geometryCoordinates(geometry);
        if (!points[index]) return geometry;
        points[index] = [...coordinate];
        return geometryFromPoints(geometry.type, points);
    }

    function appendDistinctPoint(points, coordinate, tolerance = 1e-9) {
        const previous = points[points.length - 1];
        if (previous && Math.abs(previous[0] - coordinate[0]) <= tolerance && Math.abs(previous[1] - coordinate[1]) <= tolerance) return [...points];
        return [...points, [...coordinate]];
    }

    function drawingGuide(tool, points, pointer = null) {
        const features = points.map((point, index) => ({
            type: "Feature",
            properties: { role: "vertex", index },
            geometry: { type: "Point", coordinates: point },
        }));
        const preview = [...points];
        if (pointer && points.length) preview.push(pointer);
        if (preview.length >= 2) {
            features.unshift({
                type: "Feature",
                properties: { role: "preview", tool },
                geometry: { type: "LineString", coordinates: preview },
            });
        }
        return { type: "FeatureCollection", features };
    }

    function quayLength(settings) {
        const count = Math.max(2, Number(settings.bollard_count || 2));
        const custom = Array.isArray(settings.bollard_spacings) ? settings.bollard_spacings.map(Number) : [];
        const spacing = Number(settings.default_bollard_spacing_m || 20);
        const gaps = custom.length === count - 1 ? custom : Array(count - 1).fill(spacing);
        return Number(settings.start_margin_m || 0) + gaps.reduce((sum, value) => sum + value, 0) + Number(settings.end_margin_m || 0);
    }

    function configuredAssetGeometry(kind, anchor, headingDeg, settings) {
        if (kind === "quay") {
            const side = settings.water_side === "right" ? -1 : 1;
            return orientedFootprint(anchor, headingDeg, quayLength(settings), Number(settings.width_m || 25), side);
        }
        if (kind === "gate_area") {
            const count = Math.max(1, Number(settings.inbound_lane_count || 0) + Number(settings.outbound_lane_count || 0));
            const width = count * Number(settings.lane_width_m || 3.5) + Math.max(0, count - 1) * Number(settings.lane_spacing_m || 0.5);
            return orientedFootprint(anchor, headingDeg, Number(settings.lane_length_m || 60), width);
        }
        if (kind === "rail_area") {
            const count = Math.max(1, Number(settings.track_count || 1));
            const width = Math.max(4, Math.max(0, count - 1) * Number(settings.track_spacing_m || 5) + 4);
            return orientedFootprint(anchor, headingDeg, Number(settings.track_length_m || 400), width);
        }
        return null;
    }

    function lineFeature(start, end, properties) {
        return { type: "Feature", properties, geometry: { type: "LineString", coordinates: [start, end] } };
    }

    function configuredAssetGuides(kind, anchor, headingDeg, settings) {
        const features = [];
        if (kind === "quay") {
            const length = quayLength(settings);
            const waterOffset = settings.water_side === "right" ? 2 : -2;
            features.push(lineFeature(
                orientedPoint(anchor, headingDeg, 0, waterOffset),
                orientedPoint(anchor, headingDeg, length, waterOffset),
                { role: "water", label: "WATER" },
            ));
            const count = Math.max(2, Number(settings.bollard_count || 2));
            const custom = Array.isArray(settings.bollard_spacings) ? settings.bollard_spacings.map(Number) : [];
            const gaps = custom.length === count - 1 ? custom : Array(count - 1).fill(Number(settings.default_bollard_spacing_m || 20));
            let chainage = settings.bollard_direction === "reverse" ? length - Number(settings.end_margin_m || 0) : Number(settings.start_margin_m || 0);
            for (let index = 0; index < count; index += 1) {
                if (index) chainage += (settings.bollard_direction === "reverse" ? -1 : 1) * gaps[index - 1];
                features.push({
                    type: "Feature",
                    properties: { role: "bollard", label: String(index + 1), sequence: index + 1 },
                    geometry: { type: "Point", coordinates: orientedPoint(anchor, headingDeg, chainage, waterOffset) },
                });
            }
            const laneWidth = Number(settings.lane_width_m || 3.5);
            const laneSpacing = Number(settings.lane_spacing_m || 0.5);
            const inlandSign = settings.water_side === "right" ? -1 : 1;
            const groups = [
                ["quay_service", "SERVICE", Math.max(0, Number(settings.service_lane_count || 0))],
                ["quay_crane", "CRANE", Math.max(0, Number(settings.crane_lane_count || 0))],
                ["quay_backreach", "BACKREACH", Math.max(0, Number(settings.backreach_lane_count || 0))],
            ];
            let laneIndex = 0;
            groups.forEach(([role, label, count]) => {
                for (let index = 1; index <= count; index += 1) {
                    laneIndex += 1;
                    const offset = inlandSign * (laneWidth / 2 + (laneIndex - 1) * (laneWidth + laneSpacing));
                    features.push(lineFeature(
                        orientedPoint(anchor, headingDeg, 0, offset),
                        orientedPoint(anchor, headingDeg, length, offset),
                        { role, label: `${label} ${index}`, index, lane_index: laneIndex },
                    ));
                }
            });
        }
        if (kind === "gate_area") {
            const inbound = Math.max(0, Number(settings.inbound_lane_count || 0));
            const outbound = Math.max(0, Number(settings.outbound_lane_count || 0));
            const count = Math.max(1, inbound + outbound);
            const laneWidth = Number(settings.lane_width_m || 3.5);
            const spacing = Number(settings.lane_spacing_m || 0.5);
            for (let index = 0; index < count; index += 1) {
                const role = index < inbound ? "gate_in" : "gate_out";
                const offset = laneWidth / 2 + index * (laneWidth + spacing);
                features.push(lineFeature(
                    orientedPoint(anchor, headingDeg, 0, offset),
                    orientedPoint(anchor, headingDeg, Number(settings.lane_length_m || 60), offset),
                    { role, label: `${role === "gate_in" ? "IN" : "OUT"} ${role === "gate_in" ? index + 1 : index - inbound + 1}`, index: index + 1 },
                ));
            }
        }
        if (kind === "rail_area") {
            const count = Math.max(1, Number(settings.track_count || 1));
            const spacing = Number(settings.track_spacing_m || 5);
            for (let index = 0; index < count; index += 1) {
                const offset = 2 + index * spacing;
                features.push(lineFeature(
                    orientedPoint(anchor, headingDeg, 0, offset),
                    orientedPoint(anchor, headingDeg, Number(settings.track_length_m || 400), offset),
                    { role: "rail_track", label: `TRACK ${index + 1}`, index: index + 1 },
                ));
            }
        }
        return { type: "FeatureCollection", features };
    }

    function bearingDegrees(origin, target) {
        const meanLat = (origin[1] + target[1]) / 2 * Math.PI / 180;
        const east = (target[0] - origin[0]) * 111320 * Math.cos(meanLat);
        const north = (target[1] - origin[1]) * 110574;
        if (Math.hypot(east, north) < 0.01) return 0;
        return normalizeHeading(Math.atan2(east, north) * 180 / Math.PI);
    }

    function yardDimensions(grid) {
        const bayUsesHeading = grid.bay_use_heading_axis === true || String(grid.bay_use_heading_axis) === "true";
        const headingPadding = Number(grid.padding_top_m || 0) + Number(grid.padding_bottom_m || 0);
        const sidePadding = Number(grid.padding_left_m || 0) + Number(grid.padding_right_m || 0);
        const bayLength = Number(grid.max_bays || 1) * Number(grid.pile_length_m || 6.058)
            + Math.max(0, Number(grid.max_bays || 1) - 1) * Number(grid.bay_spacing_m || 0)
            + (bayUsesHeading ? headingPadding : sidePadding);
        const rowWidth = Number(grid.max_rows || 1) * Number(grid.pile_width_m || 2.438)
            + Math.max(0, Number(grid.max_rows || 1) - 1) * Number(grid.row_spacing_m || 0)
            + (bayUsesHeading ? sidePadding : headingPadding);
        return { bayLength, rowWidth };
    }

    function yardFootprint(anchor, headingDeg, grid) {
        const { bayLength, rowWidth } = yardDimensions(grid);
        const { forward, right } = headingVectors(headingDeg);
        const bayUsesHeading = grid.bay_use_heading_axis === true || String(grid.bay_use_heading_axis) === "true";
        const bayVector = bayUsesHeading ? forward : right;
        const rowVector = bayUsesHeading ? right : forward;
        const corner = (bayM, rowM) => offsetLonLat(
            anchor,
            bayVector[0] * bayM + rowVector[0] * rowM,
            bayVector[1] * bayM + rowVector[1] * rowM,
        );
        const ring = [
            anchor,
            corner(bayLength, 0),
            corner(bayLength, rowWidth),
            corner(0, rowWidth),
            anchor,
        ];
        return { type: "Polygon", coordinates: [ring] };
    }

    function yardFrame(grid) {
        const dimensions = yardDimensions(grid);
        const bayUsesHeading = grid.bay_use_heading_axis === true || String(grid.bay_use_heading_axis) === "true";
        return {
            ...dimensions,
            bayUsesHeading,
            forwardLength: bayUsesHeading ? dimensions.bayLength : dimensions.rowWidth,
            rightLength: bayUsesHeading ? dimensions.rowWidth : dimensions.bayLength,
        };
    }

    function yardPoint(anchor, headingDeg, grid, bayM, rowM) {
        const { forward, right } = headingVectors(headingDeg);
        const { bayUsesHeading } = yardFrame(grid);
        const bayVector = bayUsesHeading ? forward : right;
        const rowVector = bayUsesHeading ? right : forward;
        return offsetLonLat(
            anchor,
            bayVector[0] * bayM + rowVector[0] * rowM,
            bayVector[1] * bayM + rowVector[1] * rowM,
        );
    }

    function alphaNumber(value) {
        let number = 0;
        for (const character of String(value || "A").toUpperCase()) {
            if (character < "A" || character > "Z") return 1;
            number = number * 26 + character.charCodeAt(0) - 64;
        }
        return Math.max(1, number);
    }

    function alphaLabel(number) {
        let result = "";
        let value = Math.max(1, Number(number));
        while (value) {
            value -= 1;
            result = String.fromCharCode(65 + value % 26) + result;
            value = Math.floor(value / 26);
        }
        return result;
    }

    function yardGridLabel(grid, axis, physicalIndex) {
        const count = Number(grid[`max_${axis}s`] || 1);
        const logical = grid[`${axis}_order`] === "reverse" ? count - physicalIndex + 1 : physicalIndex;
        const naming = grid[`${axis}_naming`];
        const initial = grid[`initial_${axis}`] || "1";
        if (naming === "A,B,C...") return alphaLabel(alphaNumber(initial) + logical - 1);
        const start = Number.parseInt(initial, 10);
        const step = naming === "1,3,5..." ? 2 : 1;
        return String((Number.isFinite(start) ? start : 1) + (logical - 1) * step);
    }

    function yardGridGuides(anchor, headingDeg, grid) {
        if (!anchor || !grid) return { type: "FeatureCollection", features: [] };
        const bayCount = Math.max(1, Number(grid.max_bays || 1));
        const rowCount = Math.max(1, Number(grid.max_rows || 1));
        const bayStart = Number((yardFrame(grid).bayUsesHeading ? grid.padding_bottom_m : grid.padding_left_m) || 0);
        const rowStart = Number((yardFrame(grid).bayUsesHeading ? grid.padding_left_m : grid.padding_bottom_m) || 0);
        const bayPitch = Number(grid.pile_length_m || 6.058) + Number(grid.bay_spacing_m || 0);
        const rowPitch = Number(grid.pile_width_m || 2.438) + Number(grid.row_spacing_m || 0);
        const bayEnd = bayStart + (bayCount - 1) * bayPitch + Number(grid.pile_length_m || 6.058);
        const rowEnd = rowStart + (rowCount - 1) * rowPitch + Number(grid.pile_width_m || 2.438);
        const features = [];

        for (let index = 0; index < bayCount; index += 1) {
            const start = bayStart + index * bayPitch;
            const end = start + Number(grid.pile_length_m || 6.058);
            for (const boundary of [start, end]) {
                features.push(lineFeature(
                    yardPoint(anchor, headingDeg, grid, boundary, rowStart),
                    yardPoint(anchor, headingDeg, grid, boundary, rowEnd),
                    { role: "yard_grid", axis: "bay" },
                ));
            }
            features.push({
                type: "Feature",
                properties: { role: "yard_label", label: `B ${yardGridLabel(grid, "bay", index + 1)}` },
                geometry: { type: "Point", coordinates: yardPoint(anchor, headingDeg, grid, start + Number(grid.pile_length_m || 6.058) / 2, rowStart) },
            });
        }
        for (let index = 0; index < rowCount; index += 1) {
            const start = rowStart + index * rowPitch;
            const end = start + Number(grid.pile_width_m || 2.438);
            for (const boundary of [start, end]) {
                features.push(lineFeature(
                    yardPoint(anchor, headingDeg, grid, bayStart, boundary),
                    yardPoint(anchor, headingDeg, grid, bayEnd, boundary),
                    { role: "yard_grid", axis: "row" },
                ));
            }
            features.push({
                type: "Feature",
                properties: { role: "yard_label", label: `R ${yardGridLabel(grid, "row", index + 1)}` },
                geometry: { type: "Point", coordinates: yardPoint(anchor, headingDeg, grid, bayStart, start + Number(grid.pile_width_m || 2.438) / 2) },
            });
        }
        const length = Number(grid.pile_length_m || 6.058);
        const width = Number(grid.pile_width_m || 2.438);
        const forwardSize = yardFrame(grid).bayUsesHeading ? length : width;
        const rightSize = yardFrame(grid).bayUsesHeading ? width : length;
        const slotVectors = headingVectors(Number(headingDeg) + Number(grid.slot_heading_deg || 0));
        for (let bayIndex = 1; bayIndex <= bayCount; bayIndex += 1) {
            for (let rowIndex = 1; rowIndex <= rowCount; rowIndex += 1) {
                const center = yardPoint(anchor, headingDeg, grid,
                    bayStart + (bayIndex - 1) * bayPitch + length / 2,
                    rowStart + (rowIndex - 1) * rowPitch + width / 2);
                const ring = [[-1, -1], [1, -1], [1, 1], [-1, 1]].map(([forward, right]) => offsetLonLat(center,
                    slotVectors.forward[0] * forward * forwardSize / 2 + slotVectors.right[0] * right * rightSize / 2,
                    slotVectors.forward[1] * forward * forwardSize / 2 + slotVectors.right[1] * right * rightSize / 2));
                ring.push(ring[0]);
                const bay = yardGridLabel(grid, "bay", bayIndex);
                const row = yardGridLabel(grid, "row", rowIndex);
                const properties = { bayIndex, rowIndex, bay, row, label: `${bay}.${row}`, tiers: Number(grid.max_tier || 1) };
                features.push({ type: "Feature", properties: { ...properties, role: "yard_cell" }, geometry: { type: "Polygon", coordinates: [ring] } });
                features.push({ type: "Feature", properties: { ...properties, role: "yard_position" }, geometry: { type: "Point", coordinates: center } });
            }
        }
        return { type: "FeatureCollection", features };
    }

    function yardTransferLine(anchor, headingDeg, grid, side, offsetM = 0) {
        const frame = yardFrame(grid);
        const offset = Math.max(0, Number(offsetM || 0));
        if (side === "top") return [
            orientedPoint(anchor, headingDeg, frame.forwardLength + offset, 0),
            orientedPoint(anchor, headingDeg, frame.forwardLength + offset, frame.rightLength),
        ];
        if (side === "bottom") return [
            orientedPoint(anchor, headingDeg, -offset, 0),
            orientedPoint(anchor, headingDeg, -offset, frame.rightLength),
        ];
        if (side === "right") return [
            orientedPoint(anchor, headingDeg, 0, frame.rightLength + offset),
            orientedPoint(anchor, headingDeg, frame.forwardLength, frame.rightLength + offset),
        ];
        return [
            orientedPoint(anchor, headingDeg, 0, -offset),
            orientedPoint(anchor, headingDeg, frame.forwardLength, -offset),
        ];
    }

    function yardTransferDefaultCount(grid, side) {
        const frame = yardFrame(grid);
        if (["top", "bottom"].includes(side)) return Math.max(1, Number(frame.bayUsesHeading ? grid.max_rows : grid.max_bays));
        return Math.max(1, Number(frame.bayUsesHeading ? grid.max_bays : grid.max_rows));
    }

    function yardCraneRail(anchor, heading, grid, settings = {}) {
        const frame = yardFrame(grid);
        const extension = (side) => {
            const definition = settings.sides?.[side];
            return definition?.enabled ? Number(definition.offset_m || 0) + (Number(definition.lane_count || 1) - 1) * Number(definition.lane_spacing_m || 3.5) : 0;
        };
        return [orientedPoint(anchor, heading, -extension("bottom"), frame.rightLength / 2),
            orientedPoint(anchor, heading, frame.forwardLength + extension("top"), frame.rightLength / 2)];
    }

    function assetTransferGrid(kind, settings = {}) {
        const length = kind === "quay" ? quayLength(settings) : Number(settings.track_length_m || 400);
        const width = kind === "quay" ? Number(settings.width_m || 25) : Math.max(4, (Number(settings.track_count || 1) - 1) * Number(settings.track_spacing_m || 5) + 4);
        return { max_bays: 1, max_rows: 1, bay_use_heading_axis: true, pile_length_m: length, pile_width_m: width,
            bay_spacing_m: 0, row_spacing_m: 0 };
    }

    function assetTransferAnchor(kind, anchor, heading, settings = {}) {
        return kind === "quay" && settings.water_side === "right"
            ? orientedPoint(anchor, heading, 0, -Number(settings.width_m || 25)) : anchor;
    }

    function transferLaneGeometries(item) {
        if (item.geometry?.type !== "LineString") return item.geometry?.type && item.geometry.type !== "Point" ? [item.geometry] : [];
        const heading = bearingDegrees(item.geometry.coordinates[0], item.geometry.coordinates.at(-1));
        const sign = ["left", "end", "internal"].includes(item.side) ? 1 : -1;
        return Array.from({ length: item.lane_count || 1 }, (_, lane) => ({
            type: "LineString", coordinates: item.geometry.coordinates.map((point) => orientedPoint(point, heading, 0, -sign * lane * Number(item.lane_spacing_m ?? 3.5))),
        }));
    }

    function yardTransferRanges(grid, settings) {
        const frame = yardFrame(grid);
        const ranges = {};
        const full = { bay_start: 1, bay_end: Number(grid.max_bays), row_start: 1, row_end: Number(grid.max_rows) };
        const sides = settings.sides || {};
        Object.keys(sides).filter((side) => sides[side]?.enabled).forEach((side) => { ranges[side] = { ...full }; });

        function splitPair(firstSide, secondSide, axis, value, lowerSide) {
            if (!ranges[firstSide] || !ranges[secondSide]) return;
            const maximum = axis === "bay" ? Number(grid.max_bays) : Number(grid.max_rows);
            const split = Math.trunc(Number(value));
            if (!(split >= 1 && split < maximum)) return;
            const lower = lowerSide === secondSide ? secondSide : firstSide;
            const upper = lower === firstSide ? secondSide : firstSide;
            ranges[lower][`${axis}_start`] = 1;
            ranges[lower][`${axis}_end`] = split;
            ranges[upper][`${axis}_start`] = split + 1;
            ranges[upper][`${axis}_end`] = maximum;
        }

        splitPair("left", "right", frame.bayUsesHeading ? "row" : "bay", settings.left_right_split, settings.left_right_lower_side);
        splitPair("bottom", "top", frame.bayUsesHeading ? "bay" : "row", settings.top_bottom_split, settings.top_bottom_lower_side);
        return ranges;
    }

    function yardTransferGuides(anchor, headingDeg, grid, settings) {
        if (!anchor || !grid || !settings) return { type: "FeatureCollection", features: [] };
        const features = [];
        for (const side of ["top", "bottom", "left", "right"]) {
            const definition = settings.sides?.[side];
            if (!definition?.enabled) continue;
            const count = definition.position_mode === "custom"
                ? Math.max(1, Number(definition.position_count || 1))
                : yardTransferDefaultCount(grid, side);
            const lanes = Math.max(1, Number(definition.lane_count || 1));
            for (let lane = 1; lane <= lanes; lane += 1) {
            const points = yardTransferLine(anchor, headingDeg, grid, side, Number(definition.offset_m || 0) + (lane - 1) * Number(definition.lane_spacing_m || 3.5));
            features.push(lineFeature(points[0], points[1], {
                role: `transfer_${side}`,
                label: `${side.toUpperCase()} L${lane} ${String(definition.interface_kind || "vehicle_lane").replaceAll("_", " ").toUpperCase()}`,
                side, lane_index: lane,
            }));
            for (let index = 0; index < count; index += 1) {
                const ratio = (index + 0.5) / count;
                features.push({
                    type: "Feature",
                    properties: { role: "transfer_position", label: `${side[0].toUpperCase()}${lanes > 1 ? `L${lane}.` : ""}${index + 1}`, side, lane_index: lane, index: index + 1 },
                    geometry: {
                        type: "Point",
                        coordinates: [
                            points[0][0] + (points[1][0] - points[0][0]) * ratio,
                            points[0][1] + (points[1][1] - points[0][1]) * ratio,
                        ],
                    },
                });
            }
            }
        }
        return { type: "FeatureCollection", features };
    }

    function headingGuide(anchor, headingDeg, lengthM) {
        if (!anchor) return { type: "FeatureCollection", features: [] };
        const heading = normalizeHeading(headingDeg) * Math.PI / 180;
        const forward = [Math.sin(heading), Math.cos(heading)];
        const right = [Math.cos(heading), -Math.sin(heading)];
        const guideLength = Math.max(12, Math.min(Number(lengthM) || 40, 90));
        const tip = offsetLonLat(anchor, forward[0] * guideLength, forward[1] * guideLength);
        const neck = guideLength - Math.min(10, guideLength * 0.28);
        const headWidth = Math.min(5, guideLength * 0.16);
        const left = offsetLonLat(
            anchor,
            forward[0] * neck - right[0] * headWidth,
            forward[1] * neck - right[1] * headWidth,
        );
        const rightPoint = offsetLonLat(
            anchor,
            forward[0] * neck + right[0] * headWidth,
            forward[1] * neck + right[1] * headWidth,
        );
        return {
            type: "FeatureCollection",
            features: [
                { type: "Feature", properties: { role: "heading" }, geometry: { type: "LineString", coordinates: [anchor, tip] } },
                { type: "Feature", properties: { role: "arrowhead" }, geometry: { type: "Polygon", coordinates: [[tip, left, rightPoint, tip]] } },
                { type: "Feature", properties: { role: "anchor" }, geometry: { type: "Point", coordinates: anchor } },
            ],
        };
    }

    function applyCursor(canvas, tool, options = {}) {
        if (!canvas) return;
        if (options.dragging) canvas.style.cursor = "grabbing";
        else if (tool === "select" && options.hovering) canvas.style.cursor = "pointer";
        else if (tool === "block" && options.hasAnchor) canvas.style.cursor = "crosshair";
        else canvas.style.cursor = CURSORS[tool] || "default";
    }

    return {
        applyCursor,
        appendDistinctPoint,
        basemapStyle,
        bearingDegrees,
        configuredAssetGeometry,
        configuredAssetGuides,
        drawingGuide,
        alignEquipment,
        editorRailMotion,
        equipmentFeatures,
        roadSegmentSnap,
        roadVertexSnap,
        interfaceConnectionSnap,
        insertRoadVertex,
        perpendicularPoint,
        routingEndpointOptions,
        visibleRouteNodes,
        railProjection,
        geometryCoordinates,
        geometryFromPoints,
        headingGuide,
        normalizeHeading,
        offsetLonLat,
        orientedFootprint,
        orientedPoint,
        quayLength,
        replaceGeometryPoint,
        replaceMapStyle,
        savedAssetFeatures,
        yardDimensions,
        yardFootprint,
        yardFrame,
        yardGridGuides,
        yardGridLabel,
        yardPoint,
        yardTransferDefaultCount,
        yardCraneRail,
        assetTransferGrid,
        assetTransferAnchor,
        transferLaneGeometries,
        yardTransferGuides,
        yardTransferLine,
        yardTransferRanges,
    };
}));
