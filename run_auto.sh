#!/bin/bash
cd "$(dirname "$0")"
rm -f *.log
# venv가 있으면 활성화, 없으면 현재 활성화된 파이썬 환경 사용
if [ -f ../venv/bin/activate ]; then
    source ../venv/bin/activate
elif [ -f ./venv/bin/activate ]; then
    source ./venv/bin/activate
fi
if [ -z "$OPENAI_API_KEY" ] && [ ! -f .env ]; then
    echo "오류: API 키가 없습니다. .env 파일(OPENAI_API_KEY=...)을 만들거나"
    echo "      환경변수 OPENAI_API_KEY를 설정하세요. (.env.example 참고)"
    exit 1
fi
nohup python3 llm_novel_gui.py --auto > auto_run.log 2>&1 &
echo "PID: $!"
