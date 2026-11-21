"""Attributes on knowledge documents, and a knowledge search that is scoped by them.

WHY
    One agent often holds the documents of many customers (vendor manuals, contracts,
    policies). Its knowledge search ranked every page of every document together and
    left it to the model to work out which customer a passage belongs to — from the
    file name and from whatever the page happens to say. File names are whatever a
    user typed ("scan_0042.pdf", "DI manual (1).pdf") and many pages never name the
    customer, so that tie cannot be relied on.

WHAT
    * A knowledge document can carry ATTRIBUTES: free name/value pairs (customer,
      region, department, ...). They are optional. They are stored in the JSON the
      Documents table already has (document_metadata, key "attributes") — no schema
      change, nothing to migrate.
    * A document that reached the agent through the Compliance pipeline has its
      customer on record already (retailer -> set -> version -> document). That is
      used when no customer attribute is stored, so existing documents are covered
      without touching them.
    * The attributes named in KNOWLEDGE_SCOPE_ATTRIBUTES (default: customer) STEER the
      search. When the agent's documents carry two or more different values of such
      an attribute, a small model reads the user's question (and the conversation)
      and says which known values it is about — tolerant of short names,
      abbreviations and misspellings, because it matches against the list of values
      that exist, it does not parse the wording.
    * The search then runs once per customer the question is about, over that
      customer's documents plus the documents nobody attached a customer to (those
      are never hidden by a missing tag; they are named as unattributed).
    * Every answer block says which customer it is, what was searched and what was
      not, so a wrong scope is visible instead of a silent "not stated".

FAIL-SAFE
    Anything that goes wrong here — no attributes, an unreadable catalog, no reply
    from the small model, a value it invents — ends in the search running exactly as
    it did before this module existed. KNOWLEDGE_SCOPE_ENABLED=false turns it off.
"""
import json
import logging
import os
import re
import threading
import time
from typing import Any, Dict, List, Optional

import config as cfg

logger = logging.getLogger(__name__)

_NAME_RE = re.compile(r'[^a-z0-9_]+')
_MAX_NAME = 60
_MAX_VALUE = 300
UNATTRIBUTED = 'unattributed'


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

def enabled() -> bool:
    return bool(getattr(cfg, 'KNOWLEDGE_SCOPE_ENABLED', True))


def scope_attribute_names() -> List[str]:
    """Attribute names that steer the search, in order (default: customer)."""
    raw = getattr(cfg, 'KNOWLEDGE_SCOPE_ATTRIBUTES', 'customer')
    names = [normalize_name(n) for n in str(raw or '').split(',')]
    return [n for n in dict.fromkeys(names) if n]


# ---------------------------------------------------------------------------
# Attributes: clean, read, write
# ---------------------------------------------------------------------------

def normalize_name(name) -> str:
    """Attribute names are lower-case words joined by '_' ("Customer Name" -> customer_name)."""
    return _NAME_RE.sub('_', str(name or '').strip().lower()).strip('_')


def clean_attributes(attributes) -> Dict[str, str]:
    """{name: value} with normalised names and trimmed one-line values. Accepts a dict,
    a JSON object string, or 'name=value' lines / ';'-separated pairs (what a person
    types into a node or a form). Empty names and empty values are dropped."""
    if attributes is None:
        return {}
    if isinstance(attributes, str):
        text = attributes.strip()
        if not text:
            return {}
        parsed = None
        if text.startswith('{'):
            try:
                parsed = json.loads(text)
            except (ValueError, TypeError):
                return {}                 # meant as JSON but unreadable: nothing, never a half-read pair
            if not isinstance(parsed, dict):
                return {}
        if not isinstance(parsed, dict):
            parsed = {}
            for piece in re.split(r'[;\n]+', text):
                if '=' in piece:
                    k, v = piece.split('=', 1)
                elif ':' in piece:
                    k, v = piece.split(':', 1)
                else:
                    continue
                parsed[k] = v
        attributes = parsed
    if not isinstance(attributes, dict):
        return {}
    out: Dict[str, str] = {}
    for k, v in attributes.items():
        name = normalize_name(k)
        if isinstance(v, (list, tuple)):
            v = ', '.join(str(x).strip() for x in v if str(x).strip())
        value = ' '.join(str(v if v is not None else '').split())
        if name and value:
            out[name] = value
    return out


def validate_attributes(attributes) -> Dict[str, str]:
    """Cleaned attributes for a WRITE. Raises ValueError (nothing is stored, nothing is
    shortened) when a name or a value is longer than an attribute can be: these are
    labels a small model reads in a list, not places for text."""
    if isinstance(attributes, dict):
        for k in attributes:
            if len(str(k).strip()) > _MAX_NAME:
                raise ValueError(f"attribute name longer than {_MAX_NAME} characters: {str(k)[:40]}…")
    if isinstance(attributes, str) and attributes.strip().startswith('{'):
        try:
            if not isinstance(json.loads(attributes), dict):
                raise ValueError("not an object")
        except (ValueError, TypeError):
            raise ValueError('attributes written as JSON must be one object, like {"customer": "Acme"}')
    cleaned = clean_attributes(attributes)
    for name in cleaned:
        if len(name) > _MAX_NAME:
            raise ValueError(f"attribute name longer than {_MAX_NAME} characters: {name[:40]}…")
    for name, value in cleaned.items():
        if len(value) > _MAX_VALUE:
            raise ValueError(f"the value of '{name}' is {len(value)} characters; an attribute value "
                             f"can be at most {_MAX_VALUE}")
    return cleaned


def attributes_from_metadata(document_metadata) -> Dict[str, str]:
    """The attributes stored with a document (Documents.document_metadata JSON)."""
    if not document_metadata:
        return {}
    try:
        meta = json.loads(document_metadata) if isinstance(document_metadata, str) else document_metadata
    except (ValueError, TypeError):
        return {}
    if not isinstance(meta, dict):
        return {}
    return clean_attributes(meta.get('attributes'))


def _db():
    from CommonUtils import get_db_connection
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
    return conn, cursor


def get_document_attributes(document_id: str) -> Dict[str, str]:
    conn, cursor = _db()
    try:
        cursor.execute("SELECT document_metadata FROM Documents WHERE document_id = ?", str(document_id))
        row = cursor.fetchone()
        return attributes_from_metadata(row[0]) if row else {}
    finally:
        conn.close()


def set_document_attributes(document_id: str, attributes, replace: bool = False) -> Optional[Dict[str, str]]:
    """Attach attributes to a document. merge (default): given names are set, others
    kept, and a name given with an empty value is removed. replace=True: the document
    ends up with exactly the given attributes.

    Returns the attributes the document now has, or None when nothing could be written
    (document not found, or its metadata is not a JSON object — it is left untouched
    rather than overwritten)."""
    given_raw = attributes if isinstance(attributes, dict) else None
    given = validate_attributes(attributes)        # raises ValueError before anything is touched
    removed = set()
    if given_raw is not None:
        removed = {normalize_name(k) for k, v in given_raw.items()
                   if normalize_name(k) and not str(v if v is not None else '').strip()}
    conn, cursor = _db()
    try:
        cursor.execute("SELECT document_metadata FROM Documents WHERE document_id = ?", str(document_id))
        row = cursor.fetchone()
        if not row:
            logger.warning(f"set_document_attributes: document {document_id} not found")
            return None
        raw = row[0]
        if raw is None or not str(raw).strip():
            meta = {}
        else:
            try:
                meta = json.loads(raw)
            except (ValueError, TypeError):
                meta = None
            if not isinstance(meta, dict):
                logger.warning(f"set_document_attributes: metadata of {document_id} is not a JSON "
                               "object — left untouched, attributes NOT stored")
                return None
        current = {} if replace else clean_attributes(meta.get('attributes'))
        current.update(given)
        for name in removed:
            current.pop(name, None)
        if current:
            meta['attributes'] = current
        else:
            meta.pop('attributes', None)
        cursor.execute("UPDATE Documents SET document_metadata = ? WHERE document_id = ?",
                       json.dumps(meta, default=str), str(document_id))
        conn.commit()
        return current
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Catalog: what attributes the agent's documents carry
# ---------------------------------------------------------------------------

def compliance_customers(document_ids: List[str]) -> Dict[str, str]:
    """{document_id: customer name} for documents the Compliance pipeline filed under a
    retailer. Empty when the compliance tables are not there or cannot be read."""
    ids = [str(d) for d in document_ids if d]
    if not ids:
        return {}
    out: Dict[str, str] = {}
    try:
        conn, cursor = _db()
    except Exception as e:
        logger.debug(f"compliance customers unavailable: {e}")
        return {}
    try:
        for start in range(0, len(ids), 500):          # batches for the IN list; every id is covered
            part = ids[start:start + 500]
            marks = ','.join('?' for _ in part)
            cursor.execute(f"""
                SELECT v.document_id, r.name
                FROM RetailerDocumentVersions v
                JOIN RetailerDocumentSets s ON s.set_id = v.set_id
                JOIN Retailers r ON r.retailer_id = s.retailer_id
                WHERE v.document_id IN ({marks})""", *part)
            for doc_id, name in cursor.fetchall():
                if name and str(name).strip():
                    out[str(doc_id)] = ' '.join(str(name).split())
    except Exception as e:
        logger.debug(f"compliance customers unavailable: {e}")
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return out


def build_catalog(documents: List[Dict], derived_customers: Optional[Dict[str, str]] = None) -> Dict[str, Dict[str, str]]:
    """{document_id: attributes} for an agent's knowledge documents: what is stored with
    each document, plus the customer the Compliance pipeline recorded when none is stored."""
    derived = derived_customers or {}
    catalog: Dict[str, Dict[str, str]] = {}
    for d in documents or []:
        doc_id = str(d.get('document_id') or '')
        if not doc_id:
            continue
        attrs = attributes_from_metadata(d.get('document_metadata'))
        if 'customer' not in attrs and derived.get(doc_id):
            attrs['customer'] = derived[doc_id]
        catalog[doc_id] = attrs
    return catalog


def known_values(catalog: Dict[str, Dict[str, str]], name: str) -> List[str]:
    """Distinct values of one attribute across the catalog (as stored), most documents first."""
    counts: Dict[str, int] = {}
    for attrs in catalog.values():
        v = attrs.get(name)
        if v:
            counts[v] = counts.get(v, 0) + 1
    return [v for v, _n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0].lower()))]


def scopable(catalog: Dict[str, Dict[str, str]]) -> Dict[str, List[str]]:
    """{attribute: values} for the steering attributes that can actually narrow a search:
    two or more different values among the agent's documents."""
    out = {}
    for name in scope_attribute_names():
        values = known_values(catalog, name)
        if len(values) >= 2:
            out[name] = values
    return out


# ---------------------------------------------------------------------------
# Resolver: which known values is the question about?
# ---------------------------------------------------------------------------

_RESOLVER_SYSTEM = (
    "You decide which of the KNOWN values a user's question is about, so that a document "
    "search can be limited to the right documents. You match meaning, not spelling: people "
    "write short names, abbreviations, initials, nicknames and typos. Reply with strict JSON "
    "only — no markdown, no prose."
)

_cache_lock = threading.Lock()
_cache: Dict[tuple, tuple] = {}
_CACHE_SECONDS = 300
_CACHE_MAX = 500


def _trim(text, limit=2000) -> str:
    s = str(text or '')
    return s if len(s) <= limit else s[:limit] + '…'


def build_resolver_prompt(values_by_attribute: Dict[str, List[str]], question: str,
                          original_user_input: Optional[str], history_block: str) -> str:
    listing = []
    for name, values in values_by_attribute.items():
        listing.append(f"{name}:")
        listing.extend(f"  - {v}" for v in values)
    if original_user_input and original_user_input.strip() and original_user_input != question:
        ask = ("USER'S LITERAL CURRENT MESSAGE (authoritative):\n" + _trim(original_user_input) + "\n\n"
               "SEARCH THE AGENT IS ABOUT TO RUN FOR IT (advisory — it may have dropped or "
               "rephrased a name):\n" + _trim(question) + "\n")
    else:
        ask = "CURRENT QUESTION:\n" + _trim(question) + "\n"
    names = list(values_by_attribute)
    shape = ",\n".join(
        f'  "{n}": [{{"label": "<how to call it>", "values": ["<KNOWN value exactly as listed>", "..."]}}]'
        for n in names)
    return (
        "KNOWN VALUES (the only ones that exist — these are attached to the documents):\n"
        + "\n".join(listing) + "\n\n"
        "RECENT CONVERSATION:\n" + (history_block or "(none)") + "\n\n"
        + ask + "\n"
        "For each attribute, list the entities the user is asking about RIGHT NOW.\n"
        "- One entry per real-world entity. Put into its \"values\" EVERY known value that "
        "denotes that entity: the same one can be stored under several spellings "
        "(for example a short form and the full name). Two different entities are two entries.\n"
        "- The user's wording need not match a known value: a short name, an abbreviation, "
        "initials or a misspelling still counts when it clearly means that entity.\n"
        "- A follow-up that names nothing inherits the entity of the conversation.\n"
        "- If the user asks about all of them, or compares named ones, list each one meant.\n"
        "- If the question names none, or you cannot tell which is meant, return an EMPTY "
        "list for that attribute. Never guess, never invent a value that is not listed.\n\n"
        "Reply with this exact JSON shape:\n{\n" + shape + ",\n"
        '  "reason": "<one short sentence>"\n}\n'
    )


def parse_resolver_reply(raw: str, values_by_attribute: Dict[str, List[str]]) -> Dict[str, List[Dict[str, Any]]]:
    """{attribute: [{label, values}]} keeping only values that really exist. A value the
    model invents is dropped; an entity left with no known value is dropped."""
    if not raw:
        return {}
    s = raw.strip()
    if s.startswith('```'):
        s = re.sub(r'^```(?:json)?\s*', '', s, flags=re.IGNORECASE)
        s = re.sub(r'\s*```\s*$', '', s)
    start, end = s.find('{'), s.rfind('}')
    if start < 0 or end <= start:
        return {}
    try:
        data = json.loads(s[start:end + 1])
    except (ValueError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}
    out: Dict[str, List[Dict[str, Any]]] = {}
    for name, known in values_by_attribute.items():
        by_fold = {v.casefold(): v for v in known}
        entities, taken = [], set()
        for ent in data.get(name) or []:
            if isinstance(ent, str):
                ent = {"label": ent, "values": [ent]}
            if not isinstance(ent, dict):
                continue
            vals = []
            for v in ent.get('values') or []:
                real = by_fold.get(str(v).strip().casefold())
                if real and real not in taken and real not in vals:
                    vals.append(real)
            if not vals:
                continue
            taken.update(vals)
            label = ' '.join(str(ent.get('label') or '').split()) or vals[0]
            entities.append({"label": label[:120], "values": vals})
        if entities:
            out[name] = entities
    return out


def resolve(values_by_attribute: Dict[str, List[str]], question: str,
            original_user_input: Optional[str] = None, chat_history=None,
            llm_call=None, cache_key=None) -> Dict[str, List[Dict[str, Any]]]:
    """Ask the small model which known values the question is about. {} = no scope."""
    if not values_by_attribute:
        return {}
    key = None
    if cache_key is not None:
        key = (cache_key, str(original_user_input or ''), str(question or ''),
               json.dumps(values_by_attribute, sort_keys=True))
        with _cache_lock:
            hit = _cache.get(key)
            if hit and time.time() - hit[0] < _CACHE_SECONDS:
                return hit[1]
    import agent_knowledge_integration as aki
    history_block = ''
    try:
        history_block = aki._format_chat_history_for_detector(chat_history)
    except Exception:
        history_block = ''
    prompt = build_resolver_prompt(values_by_attribute, question, original_user_input, history_block)
    call = llm_call or (lambda p, s: aki._haiku_call_with_fallback(p, s, max_tokens=600, temp=0.0))
    raw = call(prompt, _RESOLVER_SYSTEM)
    result = parse_resolver_reply(raw or '', values_by_attribute)
    if key is not None and raw:
        with _cache_lock:
            if len(_cache) >= _CACHE_MAX:
                _cache.clear()
            _cache[key] = (time.time(), result)
    return result


# ---------------------------------------------------------------------------
# Plan: which documents each search covers
# ---------------------------------------------------------------------------

def plan_groups(documents: List[Dict], catalog: Dict[str, Dict[str, str]],
                selection: Dict[str, List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """One group per entity of the FIRST steering attribute that was resolved (for
    customers: one search per customer). Further resolved attributes narrow every group.

    A document is in a group when, for each resolved attribute, its value is one of the
    selected ones OR it has no value for that attribute (unattributed documents are
    never hidden by a missing tag)."""
    names = [n for n in scope_attribute_names() if selection.get(n)]
    if not names:
        return []
    primary, others = names[0], names[1:]
    other_allowed = {n: {v for e in selection[n] for v in e['values']} for n in others}
    groups = []
    for ent in selection[primary]:
        wanted = set(ent['values'])
        docs, unattributed = [], []
        for d in documents:
            doc_id = str(d.get('document_id') or '')
            attrs = catalog.get(doc_id, {})
            mine = attrs.get(primary)
            if mine and mine not in wanted:
                continue
            if any(attrs.get(n) and attrs.get(n) not in allowed for n, allowed in other_allowed.items()):
                continue
            docs.append(d)
            if not mine:
                unattributed.append(d)
        groups.append({"attribute": primary, "label": ent['label'], "values": list(ent['values']),
                       "documents": docs, "unattributed": unattributed,
                       "narrowed_by": {n: sorted(a) for n, a in other_allowed.items()}})
    return groups


def decide(agent_id, documents: List[Dict], question: str, original_user_input: Optional[str] = None,
           chat_history=None, llm_call=None) -> Optional[Dict[str, Any]]:
    """The scope for one knowledge search, or None to search as before.

    Returns {"groups": [...], "catalog": {...}, "values": {attribute: [...]},
             "unresolved": bool}. groups is empty when the documents carry steering
    values but the question names none of them (the caller searches everything and
    says which customer each source belongs to). Never raises."""
    try:
        if not enabled() or not documents or len(documents) < 2:
            return None
        doc_ids = [str(d.get('document_id') or '') for d in documents]
        needs_derived = any('customer' not in attributes_from_metadata(d.get('document_metadata'))
                            for d in documents) and 'customer' in scope_attribute_names()
        derived = compliance_customers(doc_ids) if needs_derived else {}
        catalog = build_catalog(documents, derived)
        if not any(catalog.values()):
            return None                                   # no attributes anywhere: nothing to do
        values = scopable(catalog)
        if not values:
            return {"groups": [], "catalog": catalog, "values": {}, "unresolved": False}
        selection = resolve(values, question, original_user_input, chat_history,
                            llm_call=llm_call, cache_key=str(agent_id))
        groups = plan_groups(documents, catalog, selection)
        groups = [g for g in groups if g["documents"]]
        return {"groups": groups, "catalog": catalog, "values": values, "unresolved": not groups}
    except Exception as e:
        logger.warning(f"knowledge scope unavailable ({e}) — searching without it")
        return None


# ---------------------------------------------------------------------------
# What the agent is told
# ---------------------------------------------------------------------------

def _doc_label(d: Dict) -> str:
    return str(d.get('filename') or d.get('document_id') or '')


def _attr_text(attrs: Dict[str, str]) -> str:
    return '; '.join(f"{k}: {v}" for k, v in attrs.items()) if attrs else 'no attributes attached'


def source_attributes_block(text: str, documents: List[Dict], catalog: Dict[str, Dict[str, str]]) -> str:
    """For the documents named in a search result: what is attached to each. Lets the
    agent tell whose passage it is reading when the file name does not say."""
    lines = []
    for d in documents or []:
        label = _doc_label(d)
        if not label or label not in (text or ''):
            continue
        lines.append(f"- {label} -> {_attr_text(catalog.get(str(d.get('document_id') or ''), {}))}")
    if not lines:
        return ''
    return ("\n\n[WHAT IS ATTACHED TO THE SOURCES ABOVE — trust this over a file name]\n"
            + "\n".join(lines))


def format_group(group: Dict[str, Any], text: str, catalog: Dict[str, Dict[str, str]]) -> str:
    """One customer's block: a heading that says what was searched, then the search result."""
    name = group["attribute"]
    stored = ', '.join(f'"{v}"' for v in group["values"])
    n_own = len(group["documents"]) - len(group["unattributed"])
    head = [f"===== {name.upper()}: {group['label']} =====",
            f"[Searched ONLY the {n_own} document(s) attached to this {name} (stored as {stored})"
            + (f" and {len(group['unattributed'])} document(s) with no {name} attached" if group["unattributed"] else "")
            + (''.join(f"; narrowed to {n}: {', '.join(v)}" for n, v in group["narrowed_by"].items()))
            + ".]"]
    body = text if (text and str(text).strip()) else f"(nothing found in this {name}'s documents)"
    out = "\n".join(head) + "\n" + str(body)
    named = [_doc_label(d) for d in group["unattributed"] if _doc_label(d) and _doc_label(d) in out]
    if named:
        out += (f"\n[No {name} is attached to: {'; '.join(named)}. A passage from one of these is NOT "
                f"confirmed to be {group['label']}'s — say so if you use it.]")
    return out


def format_scoped(decision: Dict[str, Any], parts: List[str]) -> str:
    groups = decision["groups"]
    name = groups[0]["attribute"]
    searched = {v for g in groups for v in g["values"]}
    others = [v for v in decision["values"].get(name, []) if v not in searched]
    lines = [f"[Knowledge search scoped by {name}: " + ", ".join(g["label"] for g in groups) + "]", ""]
    lines.append("\n\n".join(parts))
    if others:
        lines.append("")
        lines.append(f"[NOT searched — documents of other {name}s: {', '.join(others)}. If the user meant "
                     f"one of them, or the scope above is not what they asked about, say which {name} "
                     "you searched and ask.]")
    lines.append(f"[Answer for each {name} from its own block only. Name the {name} in your answer. "
                 f"If a block has nothing on the question, say it is not stated for that {name} — do not "
                 "fill it from another block.]")
    return "\n".join(lines)


def format_unscoped(decision: Dict[str, Any], text: str, documents: List[Dict]) -> str:
    """Search ran over everything (the question named no known customer): say so, list the
    customers that exist, and say whose each source is."""
    out = str(text or '')
    values = decision.get("values") or {}
    if values:
        name = next(iter(values))
        out = (f"[No {name} could be identified in the question, so ALL documents were searched. "
               f"{name.capitalize()}s with documents here: {', '.join(values[name])}. Before you answer, make "
               f"sure which {name} the user means — ask if it is not clear — and use only that {name}'s "
               "sources.]\n" + out)
    return out + source_attributes_block(out, documents, decision.get("catalog") or {})
