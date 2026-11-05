"""Contract validation and offline reference renderer. No document auto-matching."""
from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import html
import json
import re
import struct
import zlib
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parent


class ContractError(ValueError):
    pass


def validate_png(raw):
    if not raw.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ContractError("only embedded PNG supported in v1")
    pos, kinds = 8, []
    while pos + 12 <= len(raw):
        size = struct.unpack(">I", raw[pos:pos + 4])[0]
        end = pos + 12 + size
        if end > len(raw):
            raise ContractError("truncated PNG")
        kind = raw[pos + 4:pos + 8]
        checksum = struct.unpack(">I", raw[end - 4:end])[0]
        if zlib.crc32(raw[pos + 4:end - 4]) != checksum:
            raise ContractError("PNG checksum mismatch")
        kinds.append(kind)
        pos = end
    if pos != len(raw) or not kinds or kinds[0] != b"IHDR" or kinds[-1] != b"IEND" or b"IDAT" not in kinds:
        raise ContractError("invalid PNG structure")


def canonical(value):
    """v1: Unicode (no normalization), sorted keys, compact JSON, UTF-8, no NaN.

    Frontend must use schema key sorting, preserve array order, and hash UTF-8.
    All numeric contract fields are safe integers; confidence uses integer percent.
    """
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def state_hash(data):
    value = copy.deepcopy(data)
    value["working_copy"].pop("state_hash", None)
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def seal(data):
    data["working_copy"]["state_hash"] = state_hash(data)
    return data


def load(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ContractError(f"duplicate JSON key: {key}")
            result[key] = value
        return result
    return json.loads(Path(path).read_text(encoding="utf-8-sig"),
                      object_pairs_hook=unique,
                      parse_constant=lambda x: (_ for _ in ()).throw(ContractError(x)))


def save(path, data):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def cardinality(old, new):
    if not old and not new:
        raise ContractError("relation cannot have two empty sides")
    if not old:
        return "addition"
    if not new:
        return "deletion"
    if len(old) == len(new) == 1:
        return "correspondence"
    if len(old) == 1:
        return "split"
    if len(new) == 1:
        return "merge"
    return "reorganization"


def validate(data):
    schema = load(ROOT / "schema" / "package.schema.json")
    errors = sorted(Draft202012Validator(schema, format_checker=FormatChecker())
                    .iter_errors(data), key=lambda e: str(list(e.path)))
    if errors:
        raise ContractError("; ".join(f"{'/'.join(map(str, e.path))}: {e.message}"
                                      for e in errors[:12]))

    def require(ok, message):
        if not ok:
            raise ContractError(message)

    def index(items, label):
        result = {x["id"]: x for x in items}
        require(len(result) == len(items), f"duplicate {label} id")
        return result

    docs = index(data["source_documents"], "document")
    require(sorted(d["role"] for d in docs.values()) == ["new", "old"], "need one old and one new document")
    blocks = index(data["blocks"], "block")
    relations = index(data["relations"], "relation")
    historical = {x for e in data["relation_history"] for x in e["retired_ids"]}
    all_relations = set(relations) | historical
    reasons = index(data["reasons"], "reason")
    changes = index(data["changes"], "change")
    opinions = index(data["opinions"], "opinion")
    decisions = index(data["decisions"], "decision")
    index(data["operations"], "operation")
    orders = set()
    for b in blocks.values():
        require(b["document_id"] in docs, f"unknown document: {b['id']}")
        order = (b["document_id"], b["order"])
        require(order not in orders, "duplicate block order in document")
        orders.add(order)
        require(not b["editable"] or (docs[b["document_id"]]["role"] == "new" and
                b["kind"] in ("paragraph", "list_item") and b["content"]["type"] == "text"), "illegal editable block")
        require((b["kind"] == "table") == (b["content"]["type"] == "table"), "table kind/content mismatch")
        require((b["kind"] == "image") == (b["content"]["type"] == "image"), "image kind/content mismatch")
        if b["content"]["type"] == "table":
            require(all(len(row) == len(b["content"]["headers"]) for row in b["content"]["rows"]), "nonrectangular table")
        if b["content"]["type"] == "image":
            raw = base64.b64decode(b["content"]["data_url"].split(",", 1)[1], validate=True)
            validate_png(raw)
    owned = set()
    for r in relations.values():
        require(r["kind"] == cardinality(r["from_ids"], r["to_ids"]), "relation cardinality mismatch")
        for side, role in (("from_ids", "old"), ("to_ids", "new")):
            for bid in r[side]:
                require(bid in blocks, f"dangling block: {bid}")
                require(docs[blocks[bid]["document_id"]]["role"] == role, "wrong relation side")
                require(bid not in owned, f"block owned by multiple relations: {bid}")
                owned.add(bid)
    def refs(ids, target, label):
        require(all(x in target for x in ids), f"dangling {label} reference")
    for reason in reasons.values():
        refs(reason["relation_ids"], all_relations, "reason relation")
        require(not historical.intersection(reason["relation_ids"]) or reason["review_status"] == "needs_review", "retired reason anchor requires review")
    for c in changes.values():
        refs([c["relation_id"]], all_relations, "change relation")
        require(c["relation_id"] not in historical or c["review_status"] == "needs_review", "retired change anchor requires review")
        refs(c["reason_ids"], reasons, "reason")
        require(all(c["relation_id"] in reasons[r]["relation_ids"] for r in c["reason_ids"]), "reason not linked to change relation")
        refs(c["opinion_ids"], opinions, "opinion")
        refs(c["decision_ids"], decisions, "decision")
    for d in decisions.values():
        refs(d["change_ids"], changes, "decision change")
    refs(data["summary"]["change_ids"], changes, "summary change")
    for op in opinions.values():
        target = op["target"]
        require(target["document_id"] in docs, "opinion missing document")
        refs(target["block_ids"], blocks, "opinion block")
        require(all(blocks[x]["document_id"] == target["document_id"] for x in target["block_ids"]), "opinion document mismatch")
        require(target["report_version"] == docs[target["document_id"]]["report_version"], "opinion report version mismatch")
        require(target["review_revision"] is None or target["review_revision"] <= data["review_revision"], "opinion targets future review")
    for bid, overlay in data["working_copy"]["overrides"].items():
        require(bid in blocks and blocks[bid]["editable"], "overlay must target editable new text")
        require(overlay["version"] > blocks[bid]["version"], "overlay version must advance source version")
    if data["working_copy"]["overrides"]:
        require(data["working_copy"]["report_sync"] == "unsynced", "edited text cannot claim synced report")
    for event in data["relation_history"]:
        require(not set(event["retired_ids"]) & set(relations), "retired relation still active")
        # Replacement may itself have been replaced; resolve through the complete history.
        known = set(relations) | {x for e in data["relation_history"] for x in e["retired_ids"]}
        refs(event["replacement_ids"], known, "replacement relation")
        refs(event["unresolved_reason_ids"], reasons, "unresolved reason")
        refs(event["unresolved_opinion_ids"], opinions, "unresolved opinion")
    for doc in docs.values():
        require(not (doc["coverage"]["status"] == "complete_supported" and doc["coverage"]["omissions"]), "complete coverage cannot list omissions")
    for docid, preview in data.get('page_previews', {}).items():
        require(docid in docs, 'page preview missing source document')
        require(preview['source_sha256'] == docs[docid]['sha256'], 'page preview source identity mismatch')
        pages = preview['pages']
        require([p['number'] for p in pages] == list(range(1, len(pages)+1)), 'preview page numbers must be contiguous from one')
        for page in pages:
            raw = base64.b64decode(page['data_url'].split(',', 1)[1], validate=True)
            require(raw.startswith(b'\xff\xd8') and raw.endswith(b'\xff\xd9'), 'invalid page JPEG')
            require(page['width'] > 0 and page['height'] > 0, 'invalid page dimensions')
        for bid, location in preview['locations'].items():
            require(bid in blocks and blocks[bid]['document_id'] == docid, 'preview location on wrong document')
            require(bool(location['boxes']), 'empty preview location')
            for box in location['boxes']:
                require(1 <= box['page'] <= len(pages), 'location page out of range')
                require(box['w'] > 0 and box['h'] > 0 and box['x']+box['w'] <= 100000 and box['y']+box['h'] <= 100000, 'location outside page')
        for bid, glyphs in preview.get('glyphs', {}).items():
            require(bid in blocks and blocks[bid]['document_id'] == docid and blocks[bid]['content']['type']=='text', 'glyphs on wrong source block')
            length=len(blocks[bid]['content']['text'].encode('utf-16-le'))//2
            for glyph in glyphs:
                box=glyph['box']
                require(0 <= glyph['start'] < glyph['end'] <= length, 'glyph source offset invalid')
                require(1 <= box['page'] <= len(pages) and box['w']>0 and box['h']>0 and box['x']+box['w']<=100000 and box['y']+box['h']<=100000, 'glyph outside page')
        refs(preview['unmapped_ids'], blocks, 'preview unmapped block')
        require(set(preview['unmapped_ids']).isdisjoint(preview['locations']), 'preview mapped/unmapped overlap')
        require(set(preview['unmapped_ids']) | set(preview['locations']) == {b['id'] for b in blocks.values() if b['document_id']==docid}, 'preview coverage must account for every source block')
    require(data["parent_revision"] is None or data["parent_revision"] < data["review_revision"], "invalid parent revision")
    require(data["working_copy"]["state_hash"] == state_hash(data), "state hash mismatch")
    return data


def effective_text(data, block):
    return data["working_copy"]["overrides"].get(block["id"], {}).get("text", block["content"].get("text", ""))


def _invalidate(data, relation_ids):
    affected = {c["id"] for c in data["changes"] if c["relation_id"] in relation_ids}
    for r in data["relations"]:
        if r["id"] in relation_ids:
            r["review_status"] = "needs_review"
            r["diff_status"] = "stale"
    for r in data["reasons"]:
        if set(r["relation_ids"]) & relation_ids:
            r["review_status"] = "needs_review"
    opids = set()
    for c in data["changes"]:
        if c["id"] in affected:
            c["review_status"] = c["impact"]["review_status"] = "needs_review"
            opids.update(c["opinion_ids"])
    bids = {b for r in data["relations"] if r["id"] in relation_ids for b in r["from_ids"] + r["to_ids"]}
    for op in data["opinions"]:
        if op["id"] in opids or bids & set(op["target"]["block_ids"]):
            op["review_status"] = "needs_review"
    for d in data["decisions"]:
        if affected & set(d["change_ids"]):
            d["review_status"] = "needs_review"
    data["summary"]["review_status"] = "needs_review"


def edit(data, *, target_type, target_id, text, expected_version, actor="unknown"):
    """Immutable reference transaction: failure never mutates caller data."""
    validate(data)
    out = copy.deepcopy(data)
    if target_type == "block":
        item = next((b for b in out["blocks"] if b["id"] == target_id), None)
        if not item or not item["editable"]:
            raise ContractError("block is not editable")
        version = out["working_copy"]["overrides"].get(target_id, item)["version"]
        old = effective_text(out, item)
        out["working_copy"]["overrides"][target_id] = {"text": text, "version": version + 1}
        out["working_copy"]["report_sync"] = "unsynced"
        relids = {r["id"] for r in out["relations"] if target_id in r["to_ids"]}
    elif target_type == "reason":
        item = next((r for r in out["reasons"] if r["id"] == target_id), None)
        if not item:
            raise ContractError("reason does not exist")
        old, version = item["text"], item["version"]
        item.update(text=text, version=version + 1, review_status="needs_review")
        relids = set(item["relation_ids"])
    else:
        raise ContractError("unknown edit target")
    if version != expected_version:
        raise ContractError("expected version conflict")
    _invalidate(out, relids)
    if target_type == "block":
        blocks = {b["id"]: b for b in out["blocks"]}
        for relation in out["relations"]:
            if relation["id"] in relids:
                members = relation["from_ids"] + relation["to_ids"]
                if all(blocks[bid]["content"]["type"] == "text" for bid in members):
                    old_text = "\n".join(effective_text(out, blocks[bid]) for bid in relation["from_ids"])
                    new_text = "\n".join(effective_text(out, blocks[bid]) for bid in relation["to_ids"])
                    relation["content_changed"] = old_text != new_text
    out["parent_revision"] = data["review_revision"]
    out["review_revision"] += 1
    out["operations"].append({"id": "op-" + uuid4().hex, "kind": "edit_text",
        "actor": actor, "at": datetime.now(timezone.utc).isoformat(),
        "target_type": target_type, "target_id": target_id,
        "expected_version": expected_version, "old_value": old, "new_value": text,
        "base_revision": data["review_revision"], "base_hash": data["working_copy"]["state_hash"]})
    return validate(seal(out))


def export_patch(base, current):
    validate(base)
    validate(current)
    if base["package_id"] != current["package_id"] or base["source_documents"] != current["source_documents"]:
        raise ContractError("patch source/package identity mismatch")
    prefix = len(base["operations"])
    if current["operations"][:prefix] != base["operations"]:
        raise ContractError("operation history is not an extension of baseline")
    ops = current["operations"][prefix:]
    # Only reference edit transactions implemented here; reject unlogged metadata drift.
    replay = copy.deepcopy(base)
    for op in ops:
        if op["base_hash"] != replay["working_copy"]["state_hash"] or op["base_revision"] != replay["review_revision"]:
            raise ContractError("operation chain conflict")
        if op["kind"] == "confirm_review":
            candidate = confirm_review(replay, op["target_type"], op["target_id"], actor=op["actor"])
            if candidate["operations"][-1]["old_status"] != op["old_status"]:
                raise ContractError("review confirmation status conflict")
        elif op["kind"] == "remap":
            candidate = commit_remap(replay, op)
        else:
            candidate = edit(replay, target_type=op["target_type"], target_id=op["target_id"],
                             text=op["new_value"], expected_version=op["expected_version"], actor=op["actor"])
            if candidate["operations"][-1]["old_value"] != op["old_value"]:
                raise ContractError("operation old value conflict")
        candidate["operations"][-1] = op
        replay = seal(candidate)
    if replay != current:
        raise ContractError("unlogged state changes; patch export refused")
    return {"schema_version": "1.0", "package_id": base["package_id"],
            "base_revision": base["review_revision"], "base_hash": base["working_copy"]["state_hash"],
            "source_documents": copy.deepcopy(base["source_documents"]),
            "result_revision": current["review_revision"], "result_hash": current["working_copy"]["state_hash"],
            "operations": copy.deepcopy(ops)}


def confirm_review(data, target_type, target_id, actor="unknown"):
    """Reviewer's own explicit acknowledgement; never expert disposition approval."""
    validate(data)
    out = copy.deepcopy(data)
    if target_type == "reason":
        item = next((r for r in out["reasons"] if r["id"] == target_id), None)
        active = {r["id"] for r in out["relations"]}
        if item and not set(item["relation_ids"]) <= active:
            raise ContractError("retired reason anchors require upstream reassociation")
    elif target_type == "impact":
        change = next((c for c in out["changes"] if c["id"] == target_id), None)
        if change and change["relation_id"] not in {r["id"] for r in out["relations"]}:
            raise ContractError("retired impact anchor requires reassociation")
        item = change["impact"] if change else None
    else:
        raise ContractError("only self-reviewed reason/impact may be confirmed")
    if item is None:
        raise ContractError("confirmation target missing")
    old = item["review_status"]
    item["review_status"] = "reviewed"
    out["parent_revision"] = data["review_revision"]
    out["review_revision"] += 1
    out["operations"].append(dict(id="op-" + uuid4().hex, kind="confirm_review", actor=actor,
        at=datetime.now(timezone.utc).isoformat(), base_revision=data["review_revision"],
        base_hash=data["working_copy"]["state_hash"], target_type=target_type, target_id=target_id, old_status=old))
    return validate(seal(out))


def preview_remap(data, from_ids, to_ids, *, mode="replace", moved=False,
                  confidence=50, actor="unknown"):
    """Preview is inert. Keep each affected group's unselected remainder together.

    release removes mappings for selected blocks; all body blocks remain intact.
    Reasons/changes retain retired anchors for explicit human review, not guessing.
    """
    validate(data)
    if not from_ids and not to_ids:
        raise ContractError("empty selection")
    if mode not in ("replace", "release"):
        raise ContractError("unknown remap mode")
    blocks = {b["id"]: b for b in data["blocks"]}
    roles = {d["id"]: d["role"] for d in data["source_documents"]}
    selected = from_ids + to_ids
    if len(set(selected)) != len(selected):
        raise ContractError("duplicate selection")
    for ids, role in ((from_ids, "old"), (to_ids, "new")):
        if any(x not in blocks or roles[blocks[x]["document_id"]] != role for x in ids):
            raise ContractError("invalid remap selection side")
    picked = set(selected)
    affected = [r for r in data["relations"] if picked & set(r["from_ids"] + r["to_ids"])]
    new = []
    def create(old, new_ids, is_moved):
        # Caller selection order does not override document reading order.
        old = sorted(old, key=lambda bid: blocks[bid]["order"])
        new_ids = sorted(new_ids, key=lambda bid: blocks[bid]["order"])
        new.append(dict(id="r-" + uuid4().hex, from_ids=old, to_ids=new_ids,
            kind=cardinality(old, new_ids), moved=is_moved, content_changed=True,
            method="human", confidence=confidence, review_status="needs_review", diff_status="stale"))
    if mode == "replace":
        create(from_ids, to_ids, moved)
    for r in affected:
        old = [bid for bid in r["from_ids"] if bid not in picked]
        new_ids = [bid for bid in r["to_ids"] if bid not in picked]
        if old or new_ids:
            create(old, new_ids, r["moved"])
    return dict(id="op-" + uuid4().hex, kind="remap", actor=actor,
        at=datetime.now(timezone.utc).isoformat(), base_revision=data["review_revision"],
        base_hash=data["working_copy"]["state_hash"], mode=mode,
        selected_from_ids=list(from_ids), selected_to_ids=list(to_ids),
        expected_relations=copy.deepcopy(affected), new_relations=new)


def commit_remap(data, preview):
    validate(data)
    if preview["base_hash"] != data["working_copy"]["state_hash"] or preview["base_revision"] != data["review_revision"]:
        raise ContractError("remap preview base conflict")
    # Recompute affected set rather than trusting a partially supplied transaction.
    expected = preview_remap(data, preview["selected_from_ids"], preview["selected_to_ids"], mode=preview["mode"])
    if preview["expected_relations"] != expected["expected_relations"]:
        raise ContractError("remap expected relations conflict")
    selected = set(preview["selected_from_ids"] + preview["selected_to_ids"])
    original = {b for r in preview["expected_relations"] for b in r["from_ids"] + r["to_ids"]}
    replacement = [b for r in preview["new_relations"] for b in r["from_ids"] + r["to_ids"]]
    required = (original | selected) if preview["mode"] == "replace" else original - selected
    if set(replacement) != required or len(replacement) != len(set(replacement)):
        raise ContractError("remap loses or duplicates mapping members")
    out = copy.deepcopy(data)
    retired = {r["id"] for r in preview["expected_relations"]}
    # Invalidate while original anchors and members are still resolvable.
    _invalidate(out, retired)
    out["relations"] = [r for r in out["relations"] if r["id"] not in retired] + copy.deepcopy(preview["new_relations"])
    out["relation_history"].append(dict(retired_ids=sorted(retired),
        replacement_ids=[r["id"] for r in preview["new_relations"]], at=preview["at"], actor=preview["actor"],
        unresolved_reason_ids=[r["id"] for r in out["reasons"] if retired & set(r["relation_ids"])],
        unresolved_opinion_ids=[o["id"] for o in out["opinions"] if original & set(o["target"]["block_ids"])]))
    out["parent_revision"] = data["review_revision"]
    out["review_revision"] += 1
    out["operations"].append(copy.deepcopy(preview))
    return validate(seal(out))


def script_json(data):
    # HTML parser recognizes closing script tags even in application/json.
    return canonical(data).replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def render(data):
    validate(data)
    esc = lambda x: html.escape(str(x), quote=True)
    def block(b):
        c = b["content"]
        if c["type"] == "text":
            body = f'<p>{esc(effective_text(data, b))}</p>'
        elif c["type"] == "table":
            body = '<table><thead><tr>' + ''.join(f'<th>{esc(x)}</th>' for x in c["headers"]) + '</tr></thead><tbody>'
            body += ''.join('<tr>' + ''.join(f'<td>{esc(x)}</td>' for x in row) + '</tr>' for row in c["rows"]) + '</tbody></table>'
        else:
            body = f'<figure><img src="{esc(c["data_url"])}" alt="{esc(c["alt"])}"><figcaption>{esc(c["caption"])}</figcaption></figure>'
        return f'<article id="{esc(b["id"])}" data-block-id="{esc(b["id"])}"><small>{esc(b["id"])} · {esc(b["source_locator"])}</small>{body}</article>'
    panes = []
    owned = {bid for r in data["relations"] for bid in r["from_ids"] + r["to_ids"]}
    for role in ("old", "new"):
        d = next(x for x in data["source_documents"] if x["role"] == role)
        bs = sorted((b for b in data["blocks"] if b["document_id"] == d["id"]), key=lambda b: b["order"])
        panes.append(f'<section class="pane"><h2>{"旧稿" if role == "old" else "新稿"} · {esc(d["report_version"])}</h2>'
                     + ''.join(block(b) + ('<small>未配对</small>' if b['id'] not in owned else '') for b in bs) + '</section>')
    notes = []
    for r in data["relations"]:
        links = ' / '.join(f'<a href="#{esc(b)}">{esc(b)}</a>' for b in r["from_ids"] + r["to_ids"])
        notes.append(f'<article><h3>{esc(r["id"])} · {esc(r["kind"])}</h3><p>{links}</p><p>移动：{esc(r["moved"])}；内容变化：{esc(r["content_changed"])}；匹配：{esc(r["method"])} / {r["confidence"]}%（非专业正确率）；差异：{esc(r["diff_status"])}</p></article>')
    for reason in data["reasons"]:
        notes.append(f'<article><h3>原因 {esc(reason["id"])}</h3><p>{esc(reason["text"])}</p><p>关联：{esc(", ".join(reason["relation_ids"]))} · {esc(reason["review_status"])}</p><p>依据：{esc(reason["evidence"])}</p></article>')
    for c in data["changes"]:
        notes.append(f'<article><h3>影响 {esc(c["id"])}</h3><p>{esc(c["relation_id"])} · {esc(", ".join(c["impact"]["labels"]))} · {esc(c["impact"]["review_status"])}</p><p>{esc(c["impact"]["source"])}</p></article>')
    for op in data["opinions"]:
        notes.append(f'<article><h3>意见 {esc(op["id"])} · {esc(op["source_type"])}</h3><p>{esc(op["quote"])}</p><p>来源：{esc(op["source"])} · {esc(op["date"] or "日期未知")}</p><p>目标：{esc(op["target"])}；处置：{esc(op["disposition"])} · {esc(op["review_status"])}</p><p>未完成：{esc(op["outstanding"])}</p><pre>{esc(json.dumps(op["history"], ensure_ascii=False, indent=2))}</pre></article>')
    for d in data["decisions"]:
        notes.append(f'<article><h3>待决定 {esc(d["id"])}</h3><p>{esc(d["question"])} · {esc(d["status"])} · {esc(d["review_status"])}</p><p>{esc(d["basis"])}</p></article>')
    if data["relation_history"]:
        notes.append('<article><h3>历史对应替换（原因/意见锚点待复核）</h3><pre>' + esc(json.dumps(data["relation_history"], ensure_ascii=False, indent=2)) + '</pre></article>')
    coverage = ''.join(f'<p>{esc(d["filename"])} · 输入SHA-256：{esc(d["sha256"])}<br>覆盖：{esc(d["coverage"])}</p>' for d in data["source_documents"])
    return '<!doctype html>\n<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">' + \
        '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; img-src data:; style-src \'unsafe-inline\'; script-src \'none\'; base-uri \'none\'; form-action \'none\'">' + \
        f'<title>{esc(data["title"])}</title><style>{(ROOT / "viewer" / "baseline.css").read_text(encoding="utf-8")}</style></head><body>' + \
        f'<header><h1>{esc(data["title"])}</h1><p>审阅 r{data["review_revision"]} · {esc(data["working_copy"]["report_sync"])} · 静态参考页，交互尚未接入</p><p>{esc(data["summary"]["text"])} · {esc(data["summary"]["review_status"])}</p><details><summary>来源与覆盖</summary>{coverage}</details></header>' + \
        '<main>' + ''.join(panes) + '</main><section class="notes"><h2>对应关系、修改说明与意见</h2>' + ''.join(notes) + \
        '</section><script id="revision-package" type="application/json">' + script_json(data) + '</script></body></html>\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("validate", "build", "app"):
        p = sub.add_parser(name)
        p.add_argument("input", type=Path)
        if name in ("build", "app"):
            p.add_argument("output", type=Path)
    p = sub.add_parser("patch")
    p.add_argument("base", type=Path)
    p.add_argument("current", type=Path)
    p.add_argument("output", type=Path)
    args = parser.parse_args()
    try:
        if args.cmd == "patch":
            save(args.output, export_patch(load(args.base), load(args.current)))
        else:
            data = validate(load(args.input))
            if args.cmd in ("build", "app"):
                args.output.parent.mkdir(parents=True, exist_ok=True)
                if args.cmd == "app":
                    from interactive import build
                    output = build(data)
                else:
                    output = render(data)
                args.output.write_text(output, encoding="utf-8")
        print("OK", args.cmd)
    except (ValueError, KeyError, OSError) as exc:
        parser.exit(2, f"ERROR: {exc}\n")


if __name__ == "__main__":
    main()
