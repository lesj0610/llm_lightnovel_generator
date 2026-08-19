"""
character_gen.py
================
사용자가 character.json에 적은 캐릭터 설정을 config에 반영한다.

우선순위:
    1. 사용자 지정      — 적은 값을 그대로 사용
    2. LLM 추론         — 비워둔 값을 '지정된 값들을 근거로' 채움
    3. 랜덤 / 사용자 선택 — LLM이 실패했을 때만 (on_mapping_failure 정책)

필드는 두 종류다.
- 제약 필드: 후보 목록에 있는 값이어야 downstream(ANIMA danbooru 태그,
  personality.txt 조회, body_dic 인덱스)이 동작한다. LLM은 '선택'만 한다.
- 자유 필드: 직업, 인생 목표, 상대방 외모/성격/말투 등. LLM이 생성한다.

자유 서술(appearance)에서 태그로 표현되지 않는 디테일은
appearance_note로 뽑아 캐릭터 시트에 실어 소설 본문 묘사에 쓴다.
"""

import json
import os
import random as rand

import config
from openAPI_control import call_openai_for_plot, LLMRequestError

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))

CHARACTER_FILE = os.path.join(_BASE_DIR, "character.json")

# 제약 필드 -> 후보 목록 파일 (data_comfyui 계열은 한 줄 = 한 후보)
_TAG_SOURCES = {
    "hair_color": "data_comfyui/Hair_04.Color.txt",
    "hair_style": "data_comfyui/hairlength.txt",
    "eye_color": "data_comfyui/Eye_Color.txt",
}

# body_tag.txt 섹션에서 고르는 필드 -> 섹션 이름
_BODY_SECTION_FIELDS = {
    "face_style": "face",
    "breasts_size": "breasts_size",
    "hip_size": "hip_size",
    "body_size": "body_size",
}

# body_dic 인덱스(정수)로 저장해야 하는 필드.
# face_style은 문자열로 저장한다 — character_init이 눈썹 태그를 문자열로 이어붙이고
# character_sheet도 문자열로 다루기 때문에 정수를 넣으면 TypeError가 난다.
_BODY_INDEX_FIELDS = {
    "breasts_size": "breasts_size",
    "hip_size": "hip_size",
    "body_size": "body_size",
}

_SKIN_CANDIDATES = ["pale skin", "white skin", "dark skin"]


# =====================================================================
# 후보 목록 로딩
# =====================================================================

def _read_lines(relpath):
    path = os.path.join(_BASE_DIR, relpath)
    with open(path, "r", encoding="utf-8") as f:
        return [ln.strip() for ln in f if ln.strip()]


def _load_body_sections():
    """body_tag.txt를 {섹션명: [항목, ...]}로 파싱."""
    sections, tag, items = {}, None, []
    for line in _read_lines("data/body_tag.txt"):
        if line.startswith("#"):
            if tag:
                sections[tag] = items
            tag, items = line[1:].strip(), []
        else:
            items.append(line)
    if tag:
        sections[tag] = items
    return sections


def _load_personality_names():
    """personality.txt의 '##이름,...' 헤더에서 성격 명칭만 추출.

    일부 항목은 '###순수/평범'처럼 샵이 3개라 선행 '#'을 모두 제거한다.
    (personality_text 조회는 이 명칭으로 하므로 어긋나면 상세 설명이 비게 됨)
    """
    names = []
    for line in _read_lines("data/personality.txt"):
        if line.startswith("##"):
            names.append(line.lstrip("#").split(",")[0].strip())
    return names


def load_candidates():
    """LLM에 제시할 후보 목록 전체를 모은다 (중복 제거, 순서 유지)."""
    cand = {}
    for field, relpath in _TAG_SOURCES.items():
        seen, uniq = set(), []
        for item in _read_lines(relpath):
            if item not in seen:
                seen.add(item)
                uniq.append(item)
        cand[field] = uniq
    cand["skin_color"] = list(_SKIN_CANDIDATES)
    cand["personality_real"] = _load_personality_names()
    body = _load_body_sections()
    for field, section in _BODY_SECTION_FIELDS.items():
        cand[field] = body.get(section, [])
    return cand


# =====================================================================
# 사용자 설정 로딩
# =====================================================================

def _clean(value):
    """빈 문자열/None/공백은 '지정 안 함'으로 본다."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return value


def load_character_spec(path=None):
    """character.json을 읽어 (protagonist, partner, policy, exists, resolved)를 반환.

    resolved: 편집기(character_editor)가 미리 확정해 둔 태그·서술.
    이 값이 있으면 해당 항목은 LLM을 다시 부르지 않는다.

    파일이 없으면 exists=False — 호출자는 LLM을 부르지 않고
    기존 랜덤 흐름을 그대로 유지한다.
    """
    path = path or CHARACTER_FILE
    if not os.path.isfile(path):
        return {}, {}, "llm_auto", False, {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        raise ValueError(f"character.json을 읽을 수 없습니다: {e}") from e

    def _section(name):
        raw = data.get(name) or {}
        return {k: _clean(v) for k, v in raw.items() if not k.startswith("_")}

    policy = _clean(data.get("on_mapping_failure")) or "llm_auto"
    resolved = {k: v for k, v in (data.get("resolved") or {}).items()
                if not k.startswith("_")}
    return _section("protagonist"), _section("partner"), policy, True, resolved


# =====================================================================
# LLM 매핑
# =====================================================================

def _spec_to_text(spec, label):
    lines = [f"[{label} — 사용자가 지정한 값]"]
    any_set = False
    for key, value in spec.items():
        if value != "" and value is not None:
            lines.append(f"- {key}: {value}")
            any_set = True
    if not any_set:
        lines.append("- (지정된 값 없음)")
    return "\n".join(lines)


def _candidates_to_text(candidates, needed):
    blocks = []
    for field in needed:
        items = candidates.get(field, [])
        if not items:
            continue
        numbered = "\n".join(f"  {i}: {item}" for i, item in enumerate(items))
        blocks.append(f"[{field}] 아래 번호 중 하나를 고르세요\n{numbered}")
    return "\n\n".join(blocks)


def build_mapping_prompt(protagonist, partner, candidates, needed):
    """LLM이 채울 필드만 요청하는 프롬프트를 만든다."""
    free_fields = [f for f in needed if f not in candidates]
    constrained = [f for f in needed if f in candidates]

    parts = [
        "당신은 라이트노벨 캐릭터 설정을 정리하는 도우미입니다.",
        "아래 '사용자가 지정한 값'과 일관되도록 나머지 항목을 결정하세요.",
        "",
        "규칙:",
        "1. 외모 서술에 특정 속성(머리 색·길이, 눈 색, 피부 톤 등)이 명시돼 있으면,"
        " 반드시 그 속성과 가장 가까운 항목을 고르세요. 임의로 다른 값을 고르지 마세요.",
        "   (예: 서술이 '밤갈색 머리'이면 brown 계열을 고르고 black을 고르지 마세요.)",
        "2. 서술에 없는 항목은 직업·나이·성별·분위기에서 자연스럽게 추론하세요.",
        "3. 주인공과 상대방의 조합이 이야기로 성립하도록 정하세요.",
        "4. 사용자 서술이 영어·일본어 등 한국어가 아니어도 의미를 정확히 옮겨"
        " **한국어로** 출력하세요. 원문을 그대로 복사하거나 음차하지 마세요.",
        "   (선택 항목의 영어 태그는 예외 — 목록에 있는 그대로 번호로 답하세요.)",
        "",
        _spec_to_text(protagonist, "주인공"),
        "",
        _spec_to_text(partner, "상대방"),
    ]

    if constrained:
        parts += ["", "## 선택 항목 (반드시 제시된 번호 중에서만 고를 것)",
                  _candidates_to_text(candidates, constrained)]

    if free_fields:
        parts += ["", "## 자유 서술 항목", "\n".join(f"- {f}" for f in free_fields)]

    parts += [
        "",
        "## 출력 형식",
        "아래 JSON만 출력하세요. 설명·인사말·markdown 코드펜스를 쓰지 마세요.",
        "선택 항목은 반드시 정수 번호(index)로만 답하세요.",
        "자유 서술 항목의 값은 모두 한국어 문장으로 작성하세요.",
        "appearance_note: 위 선택 태그로 표현되지 않은 외모 디테일만 한국어로 적으세요.",
        "  (예: 점 위치, 머리핀 모양, 입술 형태 등. 태그로 이미 표현된 것은 쓰지 마세요.)",
        "  지정된 외모 서술이 없으면 빈 문자열로 두세요.",
        "personality_note: 사용자가 적은 성격 서술을 한국어로 정확히 옮겨 적으세요.",
        "  (personality_real은 목록에서 고른 분류이고, 이쪽은 그 인물만의 구체적인 성격입니다.)",
        "",
        json.dumps(
            {**{f: 0 for f in constrained},
             **{f: "" for f in free_fields}},
            ensure_ascii=False, indent=2),
    ]
    return "\n".join(parts)


def _parse_llm_json(text):
    """LLM 응답에서 JSON 객체를 추출한다 (코드펜스/잡설 허용)."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        cleaned = "\n".join(lines[1:])
        if cleaned.rstrip().endswith("```"):
            cleaned = cleaned.rstrip()[:-3]
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("응답에서 JSON 객체를 찾지 못했습니다")
    return json.loads(cleaned[start:end + 1])


# =====================================================================
# 검증
# =====================================================================

def validate_choice(field, value, candidates):
    """LLM 응답 1건을 검증해 최종 값을 반환.

    Returns:
        (ok: bool, resolved, reason: str)
        - 제약 필드: 인덱스 필드는 int, 태그 필드는 문자열을 돌려준다.
    """
    items = candidates.get(field)
    if items is None:  # 자유 서술 필드
        if not isinstance(value, str):
            return False, None, f"문자열이 아님: {type(value).__name__}"
        text = _clean(value)
        # 빈 응답은 성공으로 치지 않는다 (조용히 빈 값이 들어가면 시트가 비어버림).
        # appearance_note만 예외 — 특이사항이 없을 수 있다.
        if not text and field != "appearance_note":
            return False, None, "빈 값으로 응답함"
        return True, text, ""

    # 번호로 응답한 경우
    if isinstance(value, bool):
        return False, None, "불리언은 유효한 선택이 아님"
    if isinstance(value, int):
        if 0 <= value < len(items):
            return True, (value if field in _BODY_INDEX_FIELDS else items[value]), ""
        return False, None, f"번호 {value}가 후보 범위(0~{len(items)-1})를 벗어남"

    # 값 자체로 응답한 경우도 허용
    if isinstance(value, str):
        text = value.strip()
        if text.isdigit():
            return validate_choice(field, int(text), candidates)
        for i, item in enumerate(items):
            if item.strip() == text:
                return True, (i if field in _BODY_INDEX_FIELDS else item), ""
        return False, None, f"후보에 없는 값: {text[:40]}"

    return False, None, f"해석할 수 없는 형식: {type(value).__name__}"


def random_choice_for(field, candidates):
    """실패 필드를 랜덤으로 채울 때 쓰는 값."""
    items = candidates.get(field) or []
    if not items:
        return ""
    idx = rand.randint(0, len(items) - 1)
    return idx if field in _BODY_INDEX_FIELDS else items[idx]


# =====================================================================
# 적용
# =====================================================================

# 사용자가 지정하지 않았을 때 LLM이 채울 대상
_PROTAGONIST_FILL = [
    "hair_color", "hair_style", "eye_color", "skin_color",
    "face_style", "breasts_size", "hip_size", "body_size",
    "personality_real",
]
_FREE_FILL = ["job", "job2", "objective",
              "appearance_note", "personality_note",
              "appearance2", "personality2", "personality_note2",
              "talking_style2"]

# character.json 키 -> config 속성 (주인공 / 상대방).
# 주의: 자유 서술(appearance/personality)은 여기 두지 않는다.
#   - personality_real은 personality.txt의 20종 분류 키라서 자유 문장을 넣으면
#     상세 설명 조회가 실패한다. LLM이 서술을 읽고 분류하고,
#     서술 원문은 personality_note로 한국어 정규화해 저장한다.
#   - appearance도 같은 이유로 태그(제약)와 appearance_note(자유)로 나뉜다.
_SPEC_TO_CONFIG = {
    "protagonist": {"name": "name", "sex": "sex", "age": "age", "job": "job"},
    "partner": {"name": "name2", "sex": "sex2", "age": "age2", "job": "job2",
                "talking_style": "talking_style2"},
}


def _apply_value(attr, value):
    """config에 값을 넣고 이후 랜덤 로직이 덮어쓰지 못하게 잠근다."""
    setattr(config, attr, value)
    config.lock_field(attr)


def _needed_fields(protagonist, partner):
    """사용자가 지정하지 않아 LLM이 채워야 할 필드 목록.

    자유 서술(appearance/personality)은 지정돼 있어도 LLM을 거친다 —
    태그로 분류하고, 원문을 한국어로 정규화해 *_note에 담아야 하기 때문이다.
    """
    needed = list(_PROTAGONIST_FILL)      # 제약 필드(태그·분류)는 항상 선택 필요
    free = list(_FREE_FILL)

    def _drop(*fields):
        for f in fields:
            if f in free:
                free.remove(f)

    if protagonist.get("job"):
        _drop("job")
    if partner.get("job"):
        _drop("job2")
    if partner.get("talking_style"):
        _drop("talking_style2")

    # 서술이 없으면 note도 만들지 않는다 (태그/기본값만으로 충분)
    if not protagonist.get("appearance"):
        _drop("appearance_note")
    if not protagonist.get("personality"):
        _drop("personality_note")
    if not partner.get("personality"):
        _drop("personality_note2")
    # 상대방 외모/성격 요약은 서술이 있으면 그것을 한국어로 정규화해 쓰고,
    # 없으면 LLM이 새로 만든다 — 어느 쪽이든 LLM을 거친다.
    return needed + free


def _request_mapping(protagonist, partner, candidates, needed, log_fn=None):
    """LLM에 매핑을 요청하고 (결과 dict, 오류 메시지)를 반환."""
    prompt = build_mapping_prompt(protagonist, partner, candidates, needed)
    try:
        result_text, _ = call_openai_for_plot(
            prompt,
            system_prompt="당신은 설정 데이터를 JSON으로만 출력하는 도우미입니다.",
            log_fn=log_fn, temperature=0.4, max_retries=2)
    except LLMRequestError as e:
        return None, f"LLM 호출 실패: {e}"
    try:
        return _parse_llm_json(result_text), ""
    except (ValueError, TypeError) as e:
        return None, f"LLM 응답 파싱 실패: {e}"


def apply_character_spec(path=None, log_fn=None, policy_override=None, force=False):
    """character.json을 읽어 config에 반영한다.

    한 실행에서 한 번만 적용한다 (force=True면 재적용).
    theme_gen_auto와 random_setup_all 양쪽에서 호출되므로 가드가 없으면
    LLM 매핑이 두 번 호출되고 먼저 확정한 값이 다시 덮인다.

    Returns:
        dict {
          "applied": {필드: 값},          # 실제로 config에 반영된 값
          "user_specified": [필드, ...],  # 사용자가 직접 지정한 필드
          "failed": [{"field","reason","candidates"}],  # 확정하지 못한 필드
          "policy": str,                  # 실패 필드에 적용된 정책
          "needs_user_choice": bool,      # True면 호출자가 사용자에게 물어야 함
        }
    """
    def log(msg):
        if log_fn:
            log_fn(f"[character_gen] {msg}")

    if config.character_spec_applied and not force:
        log("이미 적용됨 — 중복 적용/LLM 재호출 생략")
        return {"applied": {}, "user_specified": [], "failed": [],
                "policy": "", "needs_user_choice": False, "skipped": True}

    protagonist, partner, policy, exists, resolved = load_character_spec(path)
    config.character_spec_applied = True
    if not exists:
        # character.json이 없으면 기존 랜덤 흐름을 그대로 둔다 (LLM 호출 없음)
        log("character.json 없음 — 기존 랜덤 설정 사용")
        return {"applied": {}, "user_specified": [], "failed": [],
                "policy": policy, "needs_user_choice": False}
    if policy_override:
        policy = policy_override
    candidates = load_candidates()

    applied, user_specified = {}, []

    # 0) 편집기에서 이미 확정한 값 반영 — 해당 항목은 LLM을 다시 부르지 않는다
    for attr, value in resolved.items():
        _apply_value(attr, value)
        applied[attr] = value
        user_specified.append(attr)
    if resolved:
        log(f"편집기 확정값 {len(resolved)}개 반영: {sorted(resolved)}")

    # 1) 사용자 지정값 먼저 반영 (가장 높은 우선순위)
    for section, spec in (("protagonist", protagonist), ("partner", partner)):
        for key, attr in _SPEC_TO_CONFIG[section].items():
            value = spec.get(key, "")
            if value == "" or value is None:
                continue
            _apply_value(attr, value)
            applied[attr] = value
            user_specified.append(attr)
    if protagonist.get("appearance"):
        # 원문은 note의 기반이 되고, LLM이 태그로 못 옮긴 부분만 남긴다
        applied["_appearance_source"] = protagonist["appearance"]
    log(f"사용자 지정 {len(user_specified)}개: {user_specified}")

    needed = _needed_fields(protagonist, partner)
    # 사용자가 이미 지정한 config 속성은 요청 대상에서 제외
    needed = [f for f in needed if f not in user_specified]
    if not needed:
        return {"applied": applied, "user_specified": user_specified,
                "failed": [], "policy": policy, "needs_user_choice": False}

    # 2) LLM 추론
    result, error = _request_mapping(protagonist, partner, candidates, needed, log_fn)
    failed = []
    if result is None:
        log(f"매핑 실패(전체): {error}")
        failed = [{"field": f, "reason": error,
                   "candidates": candidates.get(f, [])} for f in needed]
    else:
        for field in needed:
            if field not in result:
                failed.append({"field": field, "reason": "응답에 항목 없음",
                               "candidates": candidates.get(field, [])})
                continue
            ok, value, reason = validate_choice(field, result[field], candidates)
            if ok:
                _apply_value(field, value)
                applied[field] = value
            else:
                failed.append({"field": field, "reason": reason,
                               "candidates": candidates.get(field, [])})
        log(f"LLM 매핑 성공 {len(applied) - len(user_specified)}개, 실패 {len(failed)}개")

    if not failed:
        return {"applied": applied, "user_specified": user_specified,
                "failed": [], "policy": policy, "needs_user_choice": False}

    # 3) 실패 필드 처리 — 정책에 따라
    for item in failed:
        log(f"실패: {item['field']} — {item['reason']}")

    if policy == "abort":
        raise RuntimeError(
            "캐릭터 설정 매핑 실패: "
            + ", ".join(f"{f['field']}({f['reason']})" for f in failed))

    if policy == "ask":
        # 호출자(TUI)가 사용자에게 물어야 한다. 아직 config에 반영하지 않는다.
        return {"applied": applied, "user_specified": user_specified,
                "failed": failed, "policy": policy, "needs_user_choice": True}

    if policy == "llm_auto":
        retry_fields = [f["field"] for f in failed]
        log(f"llm_auto: 실패 필드만 재요청 — {retry_fields}")
        retry, retry_error = _request_mapping(
            protagonist, partner, candidates, retry_fields, log_fn)
        still_failed = []
        for item in failed:
            field = item["field"]
            if retry and field in retry:
                ok, value, reason = validate_choice(field, retry[field], candidates)
                if ok:
                    _apply_value(field, value)
                    applied[field] = value
                    continue
                item = {**item, "reason": f"재시도 후에도 실패: {reason}"}
            elif retry_error:
                item = {**item, "reason": f"재시도 실패: {retry_error}"}
            still_failed.append(item)
        failed = still_failed
        if failed:
            log(f"재시도 후 잔여 실패 {len(failed)}개 — 랜덤으로 채움")

    # random 정책이거나, llm_auto 재시도 후에도 남은 필드
    for item in failed:
        field = item["field"]
        value = random_choice_for(field, candidates)
        _apply_value(field, value)
        applied[field] = value
        item["resolved_by"] = "random"
        item["resolved_value"] = value

    return {"applied": applied, "user_specified": user_specified,
            "failed": failed, "policy": policy, "needs_user_choice": False}


def apply_manual_choices(choices, log_fn=None):
    """policy='ask'로 반환된 실패 필드를 사용자 선택값으로 확정한다.

    Args:
        choices: {필드: 선택값(번호 또는 문자열)}
    Returns:
        (applied: dict, rejected: [{"field","reason"}])
    """
    candidates = load_candidates()
    applied, rejected = {}, []
    for field, value in choices.items():
        ok, resolved, reason = validate_choice(field, value, candidates)
        if ok:
            _apply_value(field, resolved)
            applied[field] = resolved
        else:
            rejected.append({"field": field, "reason": reason})
    if log_fn:
        log_fn(f"[character_gen] 수동 선택 반영 {len(applied)}개, 거부 {len(rejected)}개")
    return applied, rejected


def format_failure_report(failed, max_candidates=12):
    """실패 필드를 사용자에게 보여줄 텍스트로 만든다 (TUI 표시용)."""
    if not failed:
        return "매핑 실패 항목 없음"
    lines = ["[캐릭터 설정 매핑 실패 항목]", ""]
    for item in failed:
        lines.append(f"- {item['field']}: {item['reason']}")
        cands = item.get("candidates") or []
        if cands:
            shown = ", ".join(f"{i}:{c}" for i, c in enumerate(cands[:max_candidates]))
            more = f" ... (총 {len(cands)}개)" if len(cands) > max_candidates else ""
            lines.append(f"    후보 {shown}{more}")
        if item.get("resolved_by"):
            lines.append(f"    -> {item['resolved_by']}로 채움: {item.get('resolved_value')}")
    return "\n".join(lines)
