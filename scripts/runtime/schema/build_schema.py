"""Authoritative schema source; regenerate package.schema.json after changes."""
import json
from pathlib import Path

S = {"type": "string"}
ID = {"type": "string", "pattern": "^[A-Za-z][A-Za-z0-9_-]*$"}
N = {"type": "integer", "minimum": 0, "maximum": 9007199254740991}
B = {"type": "boolean"}
HASH = {"type": "string", "pattern": "^[a-f0-9]{64}$"}
DATE = {"type": "string", "format": "date-time"}
NULL = {"type": "null"}


def enum(*values):
    return {"enum": list(values)}


def arr(item, unique=False):
    return {"type": "array", "items": item, **({"uniqueItems": True} if unique else {})}


def obj(**fields):
    return {"type": "object", "properties": fields, "required": list(fields), "additionalProperties": False}


def nullable(item):
    return {"anyOf": [item, NULL]}


IDS = arr(ID, True)
REVIEW = enum("reviewed", "needs_review", "unreviewed")
DISPOSITION = enum("open", "accepted", "partially_accepted", "declined", "moot")
TEXT = obj(type={"const": "text"}, text=S)
TABLE = obj(type={"const": "table"}, headers=arr(S), rows=arr(arr(S)))
IMAGE = obj(type={"const": "image"}, data_url={"type": "string", "pattern": "^data:image/png;base64,[A-Za-z0-9+/]+={0,2}$"}, alt=S, caption=S)
SOURCE = obj(id=ID, role=enum("old", "new"), filename=S, report_version=S,
             sha256=HASH, identity_kind=enum("file_bytes", "synthetic"),
             coverage=obj(status=enum("complete_supported", "partial"), omissions=arr(S), notes=S))
BLOCK = obj(id=ID, document_id=ID, order=N,
            kind=enum("heading", "paragraph", "list_item", "table", "image", "caption"),
            content={"oneOf": [TEXT, TABLE, IMAGE]}, source_locator=S, version={**N, "minimum": 1}, editable=B)
RELATION = obj(id=ID, from_ids=IDS, to_ids=IDS,
               kind=enum("correspondence", "split", "merge", "reorganization", "addition", "deletion"),
               moved=B, content_changed=B, method=enum("stable_id", "similarity_candidate", "human", "explicit_fixture"),
               confidence={"type": "integer", "minimum": 0, "maximum": 100},
               review_status=REVIEW, diff_status=enum("not_computed", "current", "stale", "unsupported"))
REASON = obj(id=ID, relation_ids=IDS, text=S, evidence=S, source=S, version={**N, "minimum": 1}, review_status=REVIEW)
IMPACT = obj(labels={**arr(enum("judgement", "evidence", "wording", "structure", "unknown"), True), "minItems": 1}, source=S, review_status=REVIEW)
CHANGE = obj(id=ID, relation_id=ID, reason_ids=IDS, impact=IMPACT, opinion_ids=IDS, decision_ids=IDS, review_status=REVIEW)
OPINION = obj(id=ID, source_type=enum("human_expert", "cited_expert", "ai_review"),
              source=S, date=nullable(DATE), quote=S,
              target=obj(document_id=ID, report_version=S, review_revision=nullable(N), block_ids=IDS),
              disposition=DISPOSITION, outstanding=S,
              history=arr(obj(disposition=DISPOSITION, actor=S, at=nullable(DATE), reason=S, review_revision=N)),
              review_status=REVIEW)
DECISION = obj(id=ID, question=S, options=arr(S), status=enum("open", "decided", "deferred"),
               basis=S, owner=nullable(S), change_ids=IDS, review_status=REVIEW)
OPERATION = obj(id=ID, kind={"const": "edit_text"}, actor=S, at=DATE,
                target_type=enum("block", "reason"), target_id=ID,
                expected_version={**N, "minimum": 1}, old_value=S, new_value=S, base_revision=N, base_hash=HASH)
REMAP = obj(id=ID, kind={"const": "remap"}, actor=S, at=DATE,
            base_revision=N, base_hash=HASH, mode=enum("replace", "release"),
            selected_from_ids=IDS, selected_to_ids=IDS,
            expected_relations=arr(RELATION), new_relations=arr(RELATION))
CONFIRM = obj(id=ID, kind={"const": "confirm_review"}, actor=S, at=DATE,
              base_revision=N, base_hash=HASH, target_type=enum("reason", "impact"),
              target_id=ID, old_status=REVIEW)

SCHEMA = obj(schema_version={"const": "1.0"}, package_id=ID, title=S, review_revision=N,
             parent_revision=nullable(N), generated_at=DATE,
             source_documents={**arr(SOURCE), "minItems": 2, "maxItems": 2},
             blocks=arr(BLOCK), relations=arr(RELATION), reasons=arr(REASON), changes=arr(CHANGE),
             opinions=arr(OPINION), decisions=arr(DECISION),
             summary=obj(text=S, change_ids=IDS, review_status=REVIEW),
             working_copy=obj(overrides={"type": "object", "propertyNames": ID,
                 "additionalProperties": obj(text=S, version={**N, "minimum": 2})},
                 state_hash=HASH, report_sync=enum("source_baseline", "unsynced")),
             operations=arr({"oneOf": [OPERATION, REMAP, CONFIRM]}),
             relation_history=arr(obj(retired_ids=IDS, replacement_ids=IDS, at=DATE, actor=S,
                 unresolved_reason_ids=IDS, unresolved_opinion_ids=IDS)))
SCHEMA.update({"$schema": "https://json-schema.org/draft/2020-12/schema",
               "$id": "urn:nolva:report-revision:package:1.0",
               "title": "Report revision comparison package 1.0 (foundation subset)"})
# Optional source-page preview, additive to the frozen text-only contract.
BOX = obj(page=N, x={**N,"maximum":100000}, y={**N,"maximum":100000},
          w={**N,"maximum":100000}, h={**N,"maximum":100000})
SCHEMA['properties']['page_previews'] = {
    'type':'object','propertyNames':ID,'additionalProperties':obj(
        pdf_filename=S,pdf_sha256=HASH,source_sha256=HASH,
        pages=arr(obj(number=N,width=N,height=N,data_url={"type":"string","pattern":"^data:image/jpeg;base64,[A-Za-z0-9+/]+={0,2}$"})),
        locations={'type':'object','propertyNames':ID,'additionalProperties':obj(
            method=enum('exact_text','image_pixels','partial_anchor'),boxes=arr(BOX))},
        unmapped_ids=IDS,notes=arr(S))}

# Optional source glyph coordinates; offsets use UTF-16 to match browser strings.
SCHEMA['properties']['page_previews']['additionalProperties']['properties']['glyphs'] = {
    'type':'object','propertyNames':ID,'additionalProperties':arr(obj(start=N,end=N,box=BOX))}

if __name__ == "__main__":
    Path(__file__).with_name("package.schema.json").write_text(
        json.dumps(SCHEMA, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
