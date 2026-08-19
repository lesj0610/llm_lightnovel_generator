import config
from openai import OpenAI
import json

def default_setup():
    # 설정 파일 존재/문법 검증만 수행.
    # config.json_value에 대입하면 모듈 속성이 고정 스냅샷으로 덮여
    # live 읽기(config.__getattr__)가 무력화되므로 대입하지 않는다.
    config.get_json_value()
