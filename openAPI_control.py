"""
OpenAI API 호출 공통 모듈

theme_gen_auto.py, full_episode_gen.py, plot_gen.py, anima_gen.py, story_gen.py에서
사용하는 API 호출 함수들을 통합 관리합니다.

함수 목록:
- get_api_key()                : API 키 (환경변수 전용)
- get_main_model()             : Main LLM 모델명 (plot.json model_main)
- get_openai_client()          : Main LLM 클라이언트 생성
- call_openai_api()            : 스트리밍 응답 (story_gen용)
- call_openai_for_plot()       : 비스트리밍 응답 (plot_gen용)
- openAI_response()            : ANIMA용 간단한 호출

오류 계약:
- 모든 실패는 LLMRequestError 예외로 전달됩니다. magic string 반환 없음.
- 대화 이력(messages)은 성공한 요청만 user/assistant 쌍으로 반영됩니다.
  실패 시 호출자가 넘긴 이력은 변형되지 않습니다.

재시도 정책 (소유 계층: 이 모듈 하나):
- SDK 자체 재시도는 max_retries=0으로 비활성화하여 중첩 재시도를 방지합니다.
- 재시도 대상: 타임아웃, 연결 오류, HTTP 408/409/429/5xx.
  Retry-After 헤더 우선, 없으면 지수 백오프 + jitter. 마지막 실패 후에는 대기하지 않습니다.
- 401/404 등 그 외 4xx(인증·모델명 불일치 등)는 재시도 없이 즉시 실패합니다.
"""

import os
import time
import random as rand
from openai import OpenAI, APITimeoutError, APIConnectionError, APIStatusError

import config


class LLMRequestError(Exception):
    """LLM 요청이 실패했을 때 발생하는 예외 (재시도 소진 또는 영구 오류)."""

    def __init__(self, message, status_code=None, attempts=0):
        super().__init__(message)
        self.status_code = status_code
        self.attempts = attempts


# 재시도 가능한 transient 상태 코드
RETRYABLE_STATUS = {408, 409, 429, 500, 502, 503, 504}


# =====================================================================
# 설정 접근
# =====================================================================

def get_api_key() -> str:
    """API 키를 환경변수 OPENAI_API_KEY에서만 읽습니다.

    설정 파일 fallback은 공개 저장소 커밋 유출 위험 때문에 지원하지 않습니다.
    키가 없으면 시작 단계에서 명시적으로 실패합니다.
    """
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise LLMRequestError(
            "OPENAI_API_KEY 환경변수가 설정되지 않았습니다. "
            "서버를 --api-key로 띄웠다면 같은 값을 export 하세요. "
            "예: export OPENAI_API_KEY=<키>")
    return key


def get_main_model(json_value=None) -> str:
    """Main LLM 모델명. 서버의 --served-model-name과 정확히 일치해야 하며,
    다르면 404 NotFoundError("The model ... does not exist")가 납니다."""
    jv = json_value if json_value is not None else config.get_json_value()
    return jv.get("model_main", "Gemma 4 Flash Uncensored")


def get_openai_client() -> OpenAI:
    """Main LLM용 OpenAI 클라이언트를 생성하여 반환합니다.

    plot.json은 한 번만 읽어 ip/port가 서로 다른 스냅샷에서 오는 것을 방지합니다.
    SDK 재시도는 끕니다 — 재시도는 _request_with_retry가 단독으로 소유합니다.
    """
    jv = config.get_json_value()
    return OpenAI(
        base_url="http://" + jv.get("ip_main", "localhost") + ":" + jv["port_main"] + "/v1",
        api_key=get_api_key(),
        max_retries=0,
    )


def _log_prompts_enabled(jv=None) -> bool:
    """프롬프트/응답 전문 로깅 여부 (plot.json log_prompts, 기본 yes).
    로그 파일이 공개 저장소에 커밋될 환경이라면 no로 두세요."""
    if jv is None:
        jv = config.get_json_value()
    return config.flag_on(jv.get("log_prompts", "yes"))


# =====================================================================
# 재시도 코어
# =====================================================================

def _retry_delay_seconds(exc, attempt, base_delay):
    """대기 시간 결정: Retry-After 헤더 우선, 없으면 지수 백오프 + jitter."""
    response = getattr(exc, "response", None)
    if response is not None:
        try:
            return float(response.headers.get("retry-after"))
        except (TypeError, ValueError):
            pass
    return base_delay * (2 ** attempt) + rand.uniform(0, 1)


def _request_with_retry(create_kwargs, log_fn=None, max_retries=3, base_delay=2.0):
    """chat.completions.create를 재시도 정책과 함께 실행합니다.

    Returns:
        response 객체.
    Raises:
        LLMRequestError: 영구 오류(즉시) 또는 재시도 소진 시.
    """
    client = get_openai_client()
    last_error = None
    for attempt in range(max_retries + 1):
        try:
            return client.chat.completions.create(**create_kwargs)
        except (APITimeoutError, APIConnectionError) as e:
            last_error = e
            reason = type(e).__name__
        except APIStatusError as e:
            if e.status_code not in RETRYABLE_STATUS:
                if log_fn:
                    log_fn(f"[API_ERROR] {e.status_code}: {e.message} (영구 오류, 재시도 안 함)")
                raise LLMRequestError(
                    f"API 영구 오류 ({e.status_code}): {e.message}",
                    status_code=e.status_code, attempts=attempt + 1) from e
            last_error = e
            reason = f"HTTP {e.status_code}"
        if attempt >= max_retries:
            break
        delay = _retry_delay_seconds(last_error, attempt, base_delay)
        if log_fn:
            log_fn(f"[RETRY] {reason}. 재시도 {attempt + 1}/{max_retries} ({delay:.1f}s 대기)")
        time.sleep(delay)
    status = getattr(last_error, "status_code", None)
    raise LLMRequestError(
        f"서버 응답 실패 ({max_retries}회 재시도 소진): {last_error}",
        status_code=status, attempts=max_retries + 1) from last_error


# =====================================================================
# call_openai_api (스트리밍 응답 - story_gen / full_episode_gen 용)
# =====================================================================

def call_openai_api(prompt_text: str, callback=None, info_lines=None, log_fn=None) -> str:
    """OpenAI API를 호출하여 (스트리밍) 응답 전체 텍스트를 반환합니다.

    전역 config.messages_history를 사용하되, 성공한 요청만 이력에 반영합니다.

    Raises:
        LLMRequestError: 요청 실패 시. 이력은 변형되지 않습니다.
    """
    temp = 0.9 + rand.randint(0, 1) / 10.0

    request_messages = list(config.messages_history) + [
        {"role": "user", "content": prompt_text}]

    create_kwargs = {
        "model": get_main_model(),
        "messages": request_messages,
        "temperature": temp,
        "top_p": 0.95,
        "stream": config.stream_enb,
        "timeout": 300.0,
        "extra_body": {"repeat_penalty": 1.15, "top_k": 64},
    }
    if config.stream_enb:
        create_kwargs["stream_options"] = {"include_usage": True}

    response = _request_with_retry(create_kwargs, log_fn=log_fn)

    if config.stream_enb:
        full_response = ""
        for chunk in response:
            if log_fn and chunk.usage:
                log_fn(f"[TOKEN] Prompt: {chunk.usage.prompt_tokens}, "
                       f"Completion: {chunk.usage.completion_tokens}, "
                       f"Total: {chunk.usage.total_tokens}")
            if chunk.choices and chunk.choices[0].delta.content:
                delta = chunk.choices[0].delta.content
                full_response += delta
                if callback:
                    callback(delta, info_lines)
    else:
        full_response = response.choices[0].message.content

    # 성공한 요청만 이력에 반영 + 상한 유지
    config.messages_history.append({"role": "user", "content": prompt_text})
    config.messages_history.append({"role": "assistant", "content": full_response})
    config.trim_messages_history()
    return full_response


# =====================================================================
# call_openai_for_plot (비스트리밍 - plot_gen 용)
# =====================================================================

def call_openai_for_plot(prompt_text: str, system_prompt: str = None, messages: list = None, log_fn=None,
                         temperature: float = None, timeout: float = None,
                         repeat_penalty: float = None, max_retries: int = None,
                         retry_delay: float = None) -> tuple:
    """OpenAI API를 호출하여 플롯 생성 응답을 반환합니다.

    messages가 제공되면 대화 이력을 유지합니다 (원본 리스트는 변형되지 않음).
    성공 시에만 user/assistant 쌍이 반영된 새 리스트를 반환합니다.

    Returns:
        (result: str, messages: list)
    Raises:
        LLMRequestError: 요청 실패 시. 호출자의 원본 messages는 변형되지 않습니다.
    """
    jv = config.get_json_value()
    main_llm = jv.get("mainLLM", "gemma").strip().lower()

    if system_prompt is None:
        system_prompt = config.system_prompt

    if messages is None:
        request_messages = [{"role": "system", "content": system_prompt}]
    else:
        request_messages = list(messages)  # 원본 보호
    request_messages.append({"role": "user", "content": prompt_text})

    if temperature is None:
        temperature = 0.9 + rand.randint(0, 1) / 10.0
    if timeout is None:
        timeout = 400.0
    if max_retries is None:
        max_retries = 3
    if retry_delay is None:
        retry_delay = 2.0

    top_p = 0.95
    log_full = _log_prompts_enabled(jv)

    # 모델별 파라미터 설정 (mainLLM은 모델명이 아니라 파라미터 프로파일 스위치)
    model = get_main_model(jv)
    if main_llm == "qwen":
        extra_body = {
            "chat_template_kwargs": {
                "enable_thinking": True,
                "preserve_thinking": True,
            },
        }
        reasoning_effort = "medium"
    else:
        if repeat_penalty is None:
            repeat_penalty = 1.15
        extra_body = {"repeat_penalty": repeat_penalty, "top_k": 64}
        reasoning_effort = None

    if log_fn:
        if log_full:
            log_fn(f"[PLOT_PROMPT] model={model}, temp={temperature:.2f}\n{prompt_text}")
        else:
            log_fn(f"[PLOT_PROMPT] model={model}, temp={temperature:.2f}, len={len(prompt_text)} (전문 로깅 off)")

    create_kwargs = {
        "model": model,
        "messages": request_messages,
        "temperature": temperature,
        "top_p": top_p,
        "stream": False,
        "timeout": timeout,
        "extra_body": extra_body,
    }
    if reasoning_effort:
        create_kwargs["reasoning_effort"] = reasoning_effort

    response = _request_with_retry(
        create_kwargs, log_fn=log_fn, max_retries=max_retries, base_delay=retry_delay)

    result = response.choices[0].message.content.strip()
    request_messages.append({"role": "assistant", "content": result})

    if log_fn:
        if log_full:
            log_fn(f"[PLOT_RESULT]\n{result}")
        else:
            log_fn(f"[PLOT_RESULT] len={len(result)} (전문 로깅 off)")

    return result, request_messages


# =====================================================================
# openAI_response (ANIMA용 간단한 호출)
# =====================================================================

def openAI_response(json_value, client, messages_history, user_input, op_mode, chat1, call_label=""):
    """OpenAI API 호출 함수 (ANIMA용).

    Args:
        json_value: 설정 JSON
        client: OpenAI 클라이언트
        messages_history: 메시지 히스토리
        user_input: 사용자 입력
        op_mode: 운영 모드 (미사용 유지)
        chat1: 채팅 모드 (미사용 유지)
        call_label: API 호출 라벨 (로깅용)

    Returns:
        (messages_history, full_response) — 성공 시에만 쌍이 반영된 새 리스트
    """
    # ANIMA 전용 모델명이 있으면 그것을, 없으면 Main LLM 모델명 사용
    # (서버 --served-model-name과 정확히 일치해야 함)
    model = json_value.get("model_anima") or get_main_model(json_value)

    messages = messages_history + [{"role": "user", "content": user_input}]

    try:
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.7
        )
        full_response = response.choices[0].message.content
    except Exception as e:
        if call_label:
            print(f"  [에러] {call_label}: {e}")
        raise

    messages_history = messages + [{"role": "assistant", "content": full_response}]
    return messages_history, full_response
