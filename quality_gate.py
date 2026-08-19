"""
quality_gate.py
===============
섹션(기/승/전/결) 단위 품질 게이트.

full_episode_gen이 생성·재작성 직후 호출하여 위반 섹션만 1회 재시도합니다.
검사 항목:
1. 분량 (최소/최대 글자 수 — 공백 포함, 제목·구분자 제외 본문 기준)
2. 내부 구조 라벨·지시 용어 누수 ([기] 파트, 방어기제 등)
3. 동어반복 (단어 3-gram 반복 횟수)

실측 근거: EP04에서 "[기] 파트에서의", "[승] 파트에서" 라벨 누수 2건,
"나는 그녀의 허리를" 5회 / "내 옷자락을 꽉" 4회 반복, "방어기제" 5회 검출.
"""

import re

# 파트별 목표 분량 (프롬프트 지시값)
SECTION_TARGET_CHARS = {"기": 2000, "승": 3500, "전": 3500, "결": 1500}
# 허용 범위: 목표의 60% ~ 200%
MIN_RATIO = 0.6
MAX_RATIO = 2.0

# 프로즈에 노출되면 안 되는 내부 라벨·지시 용어
LEAK_PATTERNS = [
    (r"\[[기승전결]\]\s*파트", "구조 라벨"),
    (r"[기승전결]\s*파트에서", "구조 라벨"),
    (r"방어기제", "지시 용어"),
    (r"편집자(?:의)?\s*리뷰", "지시 용어"),
    (r"에피소드\s*가이드", "지시 용어"),
]

# 3-gram이 이 횟수를 초과하면 동어반복으로 판정
NGRAM_N = 3
NGRAM_MAX_REPEAT = 3


def check_section(text: str, sec_name: str = None) -> list:
    """섹션 텍스트를 검사하여 위반 사유 리스트를 반환합니다 (빈 리스트 = 통과)."""
    issues = []
    body = text.strip()

    if not body:
        return ["섹션이 비어 있음"]

    # 1. 분량
    target = SECTION_TARGET_CHARS.get(sec_name)
    if target:
        min_chars = int(target * MIN_RATIO)
        max_chars = int(target * MAX_RATIO)
        if len(body) < min_chars:
            issues.append(f"분량 미달: {len(body)}자 (목표 약 {target}자, 최소 {min_chars}자)")
        elif len(body) > max_chars:
            issues.append(f"분량 초과: {len(body)}자 (목표 약 {target}자, 최대 {max_chars}자)")

    # 2. 라벨·지시 용어 누수
    for pattern, kind in LEAK_PATTERNS:
        found = re.findall(pattern, body)
        if found:
            issues.append(f"{kind} 본문 노출: '{found[0]}' {len(found)}회 — 본문에서 언급 금지")

    # 3. 동어반복 (단어 3-gram)
    words = re.sub(r"[^가-힣a-zA-Z0-9\s]", " ", body).split()
    grams = {}
    for i in range(len(words) - NGRAM_N + 1):
        g = " ".join(words[i:i + NGRAM_N])
        grams[g] = grams.get(g, 0) + 1
    repeated = sorted(
        ((g, c) for g, c in grams.items() if c > NGRAM_MAX_REPEAT),
        key=lambda x: -x[1])
    if repeated:
        top = ", ".join(f"'{g}' {c}회" for g, c in repeated[:3])
        issues.append(f"동어반복: {top} (같은 표현 {NGRAM_MAX_REPEAT}회 초과 금지)")

    return issues


def final_check(sections: dict) -> tuple:
    """에피소드 완료 판정. sections는 {"기": 텍스트, "승": ..., "전": ..., "결": ...}.

    hard(완료 차단): 섹션 누락/공백, 최소 분량 미달, 라벨·지시 용어 누수.
    soft(경고만): 분량 초과, 동어반복 잔존.

    Returns:
        (ok: bool, hard_issues: list, soft_issues: list)
    """
    hard, soft = [], []
    for name in ("기", "승", "전", "결"):
        text = (sections.get(name) or "").strip()
        if not text:
            hard.append(f"'{name}' 섹션 누락")
            continue
        for issue in check_section(text, name):
            if "미달" in issue or "노출" in issue:
                hard.append(f"[{name}] {issue}")
            else:
                soft.append(f"[{name}] {issue}")
    return (not hard), hard, soft


def build_retry_prompt(sec_name: str, issues: list) -> str:
    """위반 사유를 해결 지시로 바꿔 재작성 프롬프트를 만듭니다."""
    issue_lines = "\n".join(f"- {i}" for i in issues)
    return f"""방금 작성한 '{sec_name}' 파트에 아래 문제가 있습니다. 문제만 해결하여 같은 파트를 처음부터 다시 작성하세요.
스토리 내용·분위기·등장인물은 유지하되, 아래 문제를 반드시 해결해야 합니다.

{issue_lines}

주의:
- '기/승/전/결', '파트' 같은 구조 용어를 본문에 절대 쓰지 마세요.
- '방어기제' 같은 분석 용어 대신 인물의 행동과 감각 묘사로 표현하세요.
- 같은 표현의 반복은 다른 동작·비유로 변주하세요.
- 재작성한 본문만 출력하세요. 설명이나 사과는 쓰지 마세요."""
