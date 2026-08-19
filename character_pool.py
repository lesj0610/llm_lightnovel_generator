"""
character_pool.py
=================
캐릭터 풀 관리. characters/ 디렉토리에 캐릭터를 ID별로 쌓아두고 조합해서 쓴다.

풀 항목은 역할(role)을 가진다:
    protagonist  주인공 후보 — 외모 태그(danbooru)까지 확정해 둔다
    partner      상대방 후보 — 자유 서술만 쓴다 (이미지 생성 대상이 아님)

구조:
    characters/0001.json  {id, label, role, character:{...}, resolved:{...}}
    character.json        주인공 1 + 상대방 1을 합쳐 만든 활성 설정
                          (소설 생성기가 읽는 유일한 파일)

주인공/상대방을 각각 골라 조합하므로, 같은 주인공에 상대방만 바꿔가며
여러 작품을 만들 수 있다.
"""

import json
import os
import time

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))

POOL_DIR = os.path.join(_BASE_DIR, "characters")
ACTIVE_FILE = os.path.join(_BASE_DIR, "character.json")

ROLE_PROTAGONIST = "protagonist"
ROLE_PARTNER = "partner"
ROLES = (ROLE_PROTAGONIST, ROLE_PARTNER)

ROLE_LABEL = {ROLE_PROTAGONIST: "주인공", ROLE_PARTNER: "상대방"}

# 캐릭터가 직접 가지는 입력 필드
CHARACTER_FIELDS = ("name", "sex", "age", "job", "appearance",
                    "personality", "talking_style")

# 주인공 항목이 확정해 두는 태그/분류 필드 (config 속성명과 동일)
PROTAGONIST_RESOLVED = (
    "hair_color", "hair_style", "eye_color", "skin_color", "face_style",
    "personality_real", "breasts_size", "hip_size", "body_size",
    "appearance_note", "personality_note",
)

# 상대방 항목이 확정해 두는 필드 (풀에는 역할 중립 이름으로 저장하고,
# 활성화할 때 config의 partner 속성명으로 옮긴다)
PARTNER_RESOLVED = ("appearance_note", "personality_note")

# 풀(역할 중립) -> character.json partner 섹션 키
_PARTNER_RESOLVED_TO_ACTIVE = {
    "appearance_note": "appearance_note2",
    "personality_note": "personality_note2",
}


def _ensure_dir():
    os.makedirs(POOL_DIR, exist_ok=True)


def _path_for(char_id):
    return os.path.join(POOL_DIR, f"{int(char_id):04d}.json")


def _atomic_write(path, data):
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def list_characters(role=None):
    """풀 목록을 반환한다. role을 주면 해당 역할만 (id 오름차순)."""
    _ensure_dir()
    items = []
    for fname in sorted(os.listdir(POOL_DIR)):
        if not fname.endswith(".json"):
            continue
        path = os.path.join(POOL_DIR, fname)
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        character = data.get("character") or {}
        item_role = data.get("role") or ROLE_PROTAGONIST
        if role and item_role != role:
            continue
        items.append({
            "id": data.get("id") or int(os.path.splitext(fname)[0] or 0),
            "role": item_role,
            "label": data.get("label") or character.get("name") or "(이름 없음)",
            "name": character.get("name") or "",
            "job": character.get("job") or "",
            "sex": character.get("sex") or "",
            "age": character.get("age"),
            "has_resolved": bool(data.get("resolved")),
            "updated": data.get("updated") or "",
            "path": path,
        })
    items.sort(key=lambda x: x["id"])
    return items


def next_id():
    """사용 가능한 다음 ID (역할 무관 전역 증가)."""
    return max((i["id"] for i in list_characters()), default=0) + 1


def load(char_id):
    path = _path_for(char_id)
    if not os.path.isfile(path):
        raise FileNotFoundError(f"캐릭터 {char_id}번이 없습니다: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save(character, role, resolved=None, char_id=None, label=None):
    """풀에 캐릭터를 저장한다. char_id가 없으면 새 ID를 부여한다.

    Args:
        character: {name, sex, age, job, appearance, personality, talking_style}
        role: "protagonist" | "partner"
        resolved: 확정된 태그/서술 (역할 중립 키)
    Returns:
        (char_id, path)
    """
    if role not in ROLES:
        raise ValueError(f"알 수 없는 역할: {role}")
    _ensure_dir()
    if char_id is None:
        char_id = next_id()
    char_id = int(char_id)
    allowed = PROTAGONIST_RESOLVED if role == ROLE_PROTAGONIST else PARTNER_RESOLVED
    record = {
        "id": char_id,
        "role": role,
        "label": (label or character.get("name")
                  or character.get("job") or f"캐릭터 {char_id}"),
        "character": {k: character.get(k) for k in CHARACTER_FIELDS},
        "resolved": {k: v for k, v in (resolved or {}).items() if k in allowed},
        "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    path = _path_for(char_id)
    _atomic_write(path, record)
    return char_id, path


def delete(char_id):
    path = _path_for(char_id)
    if not os.path.isfile(path):
        raise FileNotFoundError(f"캐릭터 {char_id}번이 없습니다")
    os.remove(path)
    return path


# =====================================================================
# 활성 조합 (character.json)
# =====================================================================

def _empty_section():
    return {k: "" for k in CHARACTER_FIELDS}


def build_active(protagonist_id=None, partner_id=None, base=None):
    """주인공/상대방 풀 항목을 합쳐 character.json 구조를 만든다.

    한쪽만 지정하면 나머지는 기존 활성 설정(base)을 유지한다.
    """
    data = dict(base or read_active() or {})
    data.setdefault("protagonist", _empty_section())
    data.setdefault("partner", _empty_section())
    data.setdefault("resolved", {})
    data.setdefault("on_mapping_failure", "llm_auto")
    source = dict(data.get("_source") or {})

    if protagonist_id is not None:
        entry = load(protagonist_id)
        if entry.get("role") != ROLE_PROTAGONIST:
            raise ValueError(f"{protagonist_id}번은 주인공 캐릭터가 아닙니다")
        data["protagonist"] = dict(entry.get("character") or _empty_section())
        # 주인공 태그는 이름 그대로 config 속성과 대응된다
        for key in PROTAGONIST_RESOLVED:
            data["resolved"].pop(key, None)
        data["resolved"].update(entry.get("resolved") or {})
        source["protagonist"] = protagonist_id

    if partner_id is not None:
        entry = load(partner_id)
        if entry.get("role") != ROLE_PARTNER:
            raise ValueError(f"{partner_id}번은 상대방 캐릭터가 아닙니다")
        data["partner"] = dict(entry.get("character") or _empty_section())
        for active_key in _PARTNER_RESOLVED_TO_ACTIVE.values():
            data["resolved"].pop(active_key, None)
        for key, value in (entry.get("resolved") or {}).items():
            active_key = _PARTNER_RESOLVED_TO_ACTIVE.get(key)
            if active_key:
                data["resolved"][active_key] = value
        source["partner"] = partner_id

    data["_source"] = source
    return data


def set_active(protagonist_id=None, partner_id=None):
    """풀에서 고른 캐릭터를 활성(character.json)으로 지정한다."""
    data = build_active(protagonist_id, partner_id)
    _atomic_write(ACTIVE_FILE, data)
    return ACTIVE_FILE, data


def read_active():
    if not os.path.isfile(ACTIVE_FILE):
        return None
    try:
        with open(ACTIVE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def get_active_ids():
    """현재 활성 설정이 어떤 풀 캐릭터에서 왔는지 (protagonist_id, partner_id)."""
    data = read_active() or {}
    source = data.get("_source") or {}
    return source.get("protagonist"), source.get("partner")
