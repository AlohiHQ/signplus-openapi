#!/usr/bin/env python3
"""Normalize generated Postman collection names and folder structure."""

from __future__ import annotations

import copy
import json
import re
import sys
from pathlib import Path
from typing import Any


def is_folder(node: dict[str, Any]) -> bool:
    return isinstance(node.get("item"), list)


def is_request(node: dict[str, Any]) -> bool:
    return isinstance(node.get("request"), dict)


def singularize(word: str) -> str:
    w = word.strip().lower()
    if w.endswith("ies") and len(w) > 3:
        return w[:-3] + "y"
    if w.endswith("sses"):
        return w
    if w.endswith("ses") and len(w) > 3:
        return w[:-2]
    if w.endswith("s") and not w.endswith("ss") and len(w) > 1:
        return w[:-1]
    return w


def normalize_for_merge(name: str) -> str:
    cleaned = name.strip().lower().replace("_", " ").replace("-", " ")
    parts = [p for p in cleaned.split() if p]
    if not parts:
        return cleaned
    parts[-1] = singularize(parts[-1])
    return " ".join(parts)


def titleize_name(name: str) -> str:
    """Title-case names while preserving placeholders like {envelope_id}."""

    def _title_segment(segment: str) -> str:
        segment = segment.strip().replace("_", " ").replace("-", " ")
        words = re.findall(r"[A-Za-z0-9]+|[^A-Za-z0-9]+", segment)
        out: list[str] = []
        for token in words:
            if re.fullmatch(r"[A-Za-z0-9]+", token):
                out.append(token[0].upper() + token[1:].lower())
            else:
                out.append(token)
        return "".join(out)

    pattern = re.compile(r"\{[^{}]+\}")
    out: list[str] = []
    last = 0
    for match in pattern.finditer(name):
        out.append(_title_segment(name[last : match.start()]))
        placeholder = match.group(0)
        inner = placeholder[1:-1].replace("_", " ").replace("-", " ")
        out.append("{" + _title_segment(inner) + "}")
        last = match.end()
    out.append(_title_segment(name[last:]))
    return "".join(out).strip()


def apply_titleize(node: dict[str, Any]) -> None:
    if "name" in node and isinstance(node["name"], str):
        node["name"] = titleize_name(node["name"])
    if is_request(node):
        request = node["request"]
        if isinstance(request.get("name"), str):
            request["name"] = clean_request_name(titleize_name(request["name"]))
        if isinstance(node.get("name"), str):
            node["name"] = clean_request_name(node["name"])
    if isinstance(node.get("response"), list):
        for response in node["response"]:
            if isinstance(response, dict) and isinstance(response.get("name"), str):
                response["name"] = titleize_name(response["name"])
    if is_folder(node):
        for child in node["item"]:
            if isinstance(child, dict):
                apply_titleize(child)


def clean_request_name(name: str) -> str:
    cleaned = re.sub(r"\s*/\s*", " / ", name.strip())
    cleaned = re.sub(r"\s*:\s*", ": ", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)

    # Collapse noisy path-prefix names like:
    # "From Template: {Template Id}: Create Envelope From Template"
    match = re.match(r"^[^:]+:\s*\{[^}]+\}:\s+(.+)$", cleaned)
    if match:
        return match.group(1).strip()

    return cleaned


def merge_plural_singular_folders(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    folder_by_key: dict[str, dict[str, Any]] = {}

    for item in items:
        if not is_folder(item):
            merged.append(item)
            continue

        key = normalize_for_merge(str(item.get("name", "")))
        if key in folder_by_key:
            folder_by_key[key]["item"].extend(item.get("item", []))
        else:
            folder_by_key[key] = item
            merged.append(item)

    return merged


def flatten_single_request_folders(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    flattened: list[dict[str, Any]] = []

    for item in items:
        if not is_folder(item):
            flattened.append(item)
            continue

        children = item.get("item", [])
        requests = [c for c in children if is_request(c)]
        folders = [c for c in children if is_folder(c)]

        if len(requests) == 1 and not folders:
            request_item = requests[0]
            folder_name = str(item.get("name", "")).strip()
            request_name = str(request_item.get("name", "")).strip()
            should_prefix = (
                folder_name
                and "/" not in folder_name
                and "{" not in folder_name
                and "}" not in folder_name
            )
            if should_prefix and request_name and not request_name.startswith(folder_name):
                combined = f"{folder_name}: {request_name}"
                request_item["name"] = combined
                if is_request(request_item):
                    request_item["request"]["name"] = combined
            flattened.append(request_item)
            continue

        if not requests and len(folders) == 1:
            only_child = folders[0]
            parent_name = str(item.get("name", "")).strip()
            child_name = str(only_child.get("name", "")).strip()
            if parent_name and child_name:
                only_child["name"] = f"{parent_name} / {child_name}"
            elif parent_name:
                only_child["name"] = parent_name
            flattened.append(only_child)
            continue

        flattened.append(item)

    return flattened


def normalize_folder_tree(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for item in items:
        if is_folder(item):
            item["item"] = normalize_folder_tree(item["item"])

    items = merge_plural_singular_folders(items)

    # Flatten repeatedly to collapse deep single-path folder chains.
    previous_size = -1
    while previous_size != len(items):
        previous_size = len(items)
        items = flatten_single_request_folders(items)

    return items


def iter_requests(items: list[dict[str, Any]]):
    for item in items:
        if is_request(item):
            yield item
        if is_folder(item):
            yield from iter_requests(item["item"])


def find_request_by_name(items: list[dict[str, Any]], target_name: str) -> dict[str, Any] | None:
    for request_item in iter_requests(items):
        name = str(request_item.get("name", "")).strip().lower()
        if name == target_name.strip().lower():
            return request_item
    return None


def strip_ids(node: Any) -> None:
    if isinstance(node, dict):
        node.pop("id", None)
        for value in node.values():
            strip_ids(value)
    elif isinstance(node, list):
        for item in node:
            strip_ids(item)


def ensure_collection_defaults(collection: dict[str, Any]) -> None:
    existing_vars: dict[str, dict[str, Any]] = {}
    for var in collection.get("variable", []):
        if isinstance(var, dict) and isinstance(var.get("key"), str):
            existing_vars[var["key"]] = var

    defaults = [
        {"key": "baseUrl", "value": "https://restapi.sign.plus/v2", "type": "string"},
        {"key": "bearerToken", "value": "", "type": "string"},
        {"key": "envelopeId", "value": "", "type": "string"},
        {"key": "documentId", "value": "", "type": "string"},
    ]

    merged_vars: list[dict[str, Any]] = []
    for default in defaults:
        current = existing_vars.get(default["key"], {})
        merged_vars.append(
            {
                "key": default["key"],
                "value": current.get("value", default["value"]),
                "type": current.get("type", default["type"]),
            }
        )

    collection["variable"] = merged_vars
    collection["auth"] = {
        "type": "bearer",
        "bearer": [{"key": "token", "value": "{{bearerToken}}", "type": "string"}],
    }


def set_path_variables_for_quickstart(request_item: dict[str, Any]) -> None:
    request = request_item.get("request", {})
    url = request.get("url", {})
    variables = url.get("variable")
    if not isinstance(variables, list):
        return
    for variable in variables:
        if not isinstance(variable, dict):
            continue
        key = str(variable.get("key", "")).strip()
        if key == "envelope_id":
            variable["value"] = "{{envelopeId}}"
        elif key == "document_id":
            variable["value"] = "{{documentId}}"


def add_response_capture_test(request_item: dict[str, Any], variable_name: str) -> None:
    test_script = (
        "let body = {};\n"
        "try {\n"
        "  body = pm.response.json();\n"
        "} catch (e) {\n"
        "  body = {};\n"
        "}\n"
        f"if (body.id) pm.collectionVariables.set('{variable_name}', body.id);\n"
    )
    request_item["event"] = [
        {
            "listen": "test",
            "script": {"type": "text/javascript", "exec": test_script.splitlines()},
        }
    ]


def build_quickstart_folder(reference_items: list[dict[str, Any]]) -> dict[str, Any]:
    steps = [
        ("Create Envelope", "Create Envelope", "envelopeId"),
        ("Add Envelope Document", "Add Envelope Document", "documentId"),
        ("Add Envelope Signing Steps", "Add Envelope Signing Steps", None),
        ("Send Envelope", "Send Envelope", None),
        ("Get Envelope", "Get Envelope", None),
    ]

    quickstart_items: list[dict[str, Any]] = []
    for idx, (source_name, display_name, capture_var) in enumerate(steps, start=1):
        source = find_request_by_name(reference_items, source_name)
        if not source:
            continue
        copied = copy.deepcopy(source)
        strip_ids(copied)
        step_name = f"{idx:02d}. {display_name}"
        copied["name"] = step_name
        if is_request(copied):
            copied["request"]["name"] = step_name
        set_path_variables_for_quickstart(copied)
        if capture_var:
            add_response_capture_test(copied, capture_var)
        quickstart_items.append(copied)

    return {
        "name": "Quickstart",
        "description": (
            "Run this folder top-to-bottom to create and send an envelope. "
            "Set {{bearerToken}} first; this flow automatically captures "
            "{{envelopeId}} and {{documentId}}."
        ),
        "item": quickstart_items,
    }


def build_reference_folder(reference_items: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "name": "Reference",
        "description": "Full API reference generated from OpenAPI.",
        "item": reference_items,
    }


def extract_reference_items_for_rebuild(collection: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Return the raw reference tree regardless of whether the input is:
    - plain generated collection (no Quickstart/Reference split), or
    - already post-processed collection (Quickstart + Reference).
    """
    items = collection.get("item", [])
    if not isinstance(items, list):
        return []

    while len(items) == 2 and all(isinstance(i, dict) for i in items):
        first_name = str(items[0].get("name", "")).strip().lower()
        second_name = str(items[1].get("name", "")).strip().lower()
        if first_name == "quickstart" and second_name == "reference":
            ref_item = items[1].get("item", [])
            if isinstance(ref_item, list):
                items = ref_item
                continue
        break

    return items


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: postprocess_postman_collection.py <collection-json-path>")
        return 1

    target = Path(sys.argv[1])
    with target.open("r", encoding="utf-8") as fh:
        collection = json.load(fh)

    if not isinstance(collection.get("item"), list):
        print("Invalid Postman collection: missing top-level 'item' array")
        return 1

    reference_items = extract_reference_items_for_rebuild(collection)
    reference_items = normalize_folder_tree(reference_items)
    apply_titleize({"item": reference_items})
    ensure_collection_defaults(collection)

    quickstart_folder = build_quickstart_folder(reference_items)
    reference_folder = build_reference_folder(reference_items)
    collection["item"] = [quickstart_folder, reference_folder]

    with target.open("w", encoding="utf-8") as fh:
        json.dump(collection, fh, indent=2)
        fh.write("\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
