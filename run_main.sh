#!/bin/bash
cd "$(dirname "$0")"
rm -f *.log
rm -f log/*.*

# venv가 있으면 활성화 (없으면 현재 활성화된 파이썬 환경 사용)
if [ -f ./venv/bin/activate ]; then
    source ./venv/bin/activate
elif [ -f ../venv/bin/activate ]; then
    source ../venv/bin/activate
fi

if [ -z "$OPENAI_API_KEY" ] && [ ! -f .env ]; then
    echo "오류: API 키가 없습니다. .env 파일(OPENAI_API_KEY=...)을 만들거나"
    echo "      환경변수 OPENAI_API_KEY를 설정하세요. (.env.example 참고)"
    exit 1
fi

# 인자 파싱
JINSHUGAI_ID=""
INC_FLAG=""
JOB=""
JOB2=""
while [[ $# -gt 0 ]]; do
    case $1 in
        -id)
            JINSHUGAI_ID="$2"
            shift 2
            ;;
        -inc_flag)
            INC_FLAG="$2"
            shift 2
            ;;
        -job)
            JOB="$2"
            shift 2
            ;;
        -job2)
            JOB2="$2"
            shift 2
            ;;
        *)
            shift
            ;;
    esac
done

CMD="python3 llm_novel_gui_textual.py"
if [ -n "$JINSHUGAI_ID" ]; then
    CMD="$CMD -id $JINSHUGAI_ID"
fi
if [ -n "$INC_FLAG" ]; then
    CMD="$CMD -inc_flag $INC_FLAG"
fi
if [ -n "$JOB" ]; then
    CMD="$CMD -job $JOB"
fi
if [ -n "$JOB2" ]; then
    CMD="$CMD -job2 $JOB2"
fi

$CMD
