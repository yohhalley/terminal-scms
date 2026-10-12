#!/usr/bin/env python3
"""Prepare older SketchUp Collada scenes for Godot's Collada importer."""

from __future__ import annotations

import argparse
import copy
import xml.etree.ElementTree as ET
from pathlib import Path


COLLADA_NAMESPACE = "http://www.collada.org/2005/11/COLLADASchema"
INSTANCE_PREFIX = f"{{{COLLADA_NAMESPACE}}}instance_"
NODE_TAG = f"{{{COLLADA_NAMESPACE}}}node"
INSTANCE_NODE_TAG = f"{{{COLLADA_NAMESPACE}}}instance_node"
VISUAL_SCENE_TAG = f"{{{COLLADA_NAMESPACE}}}visual_scene"


def _remove_node_ids(node: ET.Element) -> None:
    if node.tag == NODE_TAG:
        node.attrib.pop("id", None)
    for child in node:
        _remove_node_ids(child)


def _expand_node_references(
    node: ET.Element, nodes_by_id: dict[str, ET.Element], stack: frozenset[str]
) -> int:
    expanded_count = 0
    for index, child in reversed(list(enumerate(list(node)))):
        if child.tag != INSTANCE_NODE_TAG:
            if child.tag == NODE_TAG:
                expanded_count += _expand_node_references(child, nodes_by_id, stack)
            continue
        reference = child.attrib.get("url", "").removeprefix("#")
        target = nodes_by_id.get(reference)
        if target is None or reference in stack:
            continue
        replacement = copy.deepcopy(target)
        expanded_count += _expand_node_references(
            replacement, nodes_by_id, stack | {reference}
        )
        _remove_node_ids(replacement)
        node.remove(child)
        node.insert(index, replacement)
        expanded_count += 1
    return expanded_count


def prepare(source: Path, destination: Path) -> tuple[int, int]:
    ET.register_namespace("", COLLADA_NAMESPACE)
    tree = ET.parse(source)
    root = tree.getroot()
    nodes_by_id = {
        node.attrib["id"]: node
        for node in root.iter(NODE_TAG)
        if "id" in node.attrib
    }
    expanded_count = 0
    for visual_scene in root.iter(VISUAL_SCENE_TAG):
        for node in visual_scene.findall(NODE_TAG):
            expanded_count += _expand_node_references(node, nodes_by_id, frozenset())
    split_count = 0

    for node in root.iter(NODE_TAG):
        instances = [child for child in list(node) if child.tag.startswith(INSTANCE_PREFIX)]
        for index, instance in enumerate(instances[1:], start=2):
            node.remove(instance)
            wrapper = ET.Element(NODE_TAG, {"name": f"godot_instance_{index}"})
            wrapper.append(instance)
            node.append(wrapper)
            split_count += 1

    ET.indent(tree, space="    ")
    tree.write(destination, encoding="utf-8", xml_declaration=True)
    return expanded_count, split_count


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    expanded, split = prepare(args.source, args.destination)
    print(
        f"Prepared {args.destination}: expanded {expanded} node references, "
        f"split {split} additional instances"
    )


if __name__ == "__main__":
    main()
