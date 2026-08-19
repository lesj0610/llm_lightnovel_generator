#!/bin/bash
cd "$(dirname "$0")"
rm -f *.log
# venv가 있으면 활성화, 없으면 현재 활성화된 파이썬 환경 사용
if [ -f ../venv/bin/activate ]; then
    source ../venv/bin/activate
elif [ -f ./venv/bin/activate ]; then
    source ./venv/bin/activate
fi
if [ -z "$OPENAI_API_KEY" ]; then
    echo "오류: OPENAI_API_KEY 환경변수가 필요합니다. (서버 --api-key와 같은 값)"
    exit 1
fi
nohup python3 llm_novel_gui.py --auto > auto_run.log 2>&1 &
echo "PID: $!"
