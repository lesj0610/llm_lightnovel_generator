안녕하세요. 한국어로 라이트노벨을 쓸 수 있는 간단한 GUI 프로그램입니다.

(등장인물은 전부 성인입니다. 오픈소스입니다)



이 프로그램의 설정은 아래와 같이 해 주시면 됩니다.

1. plot.json을 열어서 아래 항목을 현재 사용중인 로컬 LLM에 맞춰주세요.

"ip_main": "localhost",

"port_main": "8089",

"model_main": "서버의 --served-model-name과 완전히 같은 이름",

"mainLLM": "gemma",

2. API 키는 plot.json이 아니라 .env 파일에 넣어 주세요. (저장소에 키가 커밋되는 사고 방지 — .env는 gitignore 대상)

cp .env.example .env 후 .env 안의 OPENAI_API_KEY에 서버를 띄울 때 쓴 --api-key 값을 넣으면 됩니다.

(환경변수 OPENAI_API_KEY가 설정돼 있으면 그쪽이 우선합니다)

3. 디렉토리에서 파이썬 virtual 환경을 만들어 주세요.


python3 -m venv ./venv

4. virtual환경을 활성화 시켜 주세요.


source venv/bin/activate

5. 필수 요소를 인스톨해 주세요


pip install -r requirements.txt

6. 아래 명령을 이용하여 실행시켜 주세요.

(job/job2는 취향에 맞게 수정하시면 됩니다)

./run_main.sh -id 1 -job 4 -job2 1

생성 결과는 result/실행시각/ 디렉토리에 저장되고, result/latest 파일이 가장 최근 실행을 가리킵니다.

## 캐릭터 설정

캐릭터를 직접 지정하려면 캐릭터 편집기를 쓰세요.

python3 character_editor.py

- 주인공과 상대방을 각각 만들어 characters/ 풀에 ID로 쌓아둡니다.
- 이름/성별/나이/직업은 직접 입력하고, 외모·성격은 한국어·영어·일본어 아무 언어로나
  자유롭게 서술하면 됩니다. LLM이 의미를 보존해 한국어로 정리하고 태그를 분류합니다.
- 비워둔 항목은 지정한 값들을 근거로 LLM이 채웁니다. (F5)
  LLM 서버 없이 작업하려면 F6으로 랜덤하게 채울 수 있습니다.
- 풀에서 주인공 1명(F7) + 상대방 1명(F8)을 지정하면 character.json이 만들어지고,
  소설 생성기가 그 조합으로 집필합니다.
- character.json이 없으면 예전처럼 전부 랜덤으로 생성됩니다.

<img width="2830" height="1394" alt="image" src="https://github.com/user-attachments/assets/cf872b48-42b4-4401-b888-31b67e2e167c" />


7. 현재 실행 순서는 아래와 같습니다.
결과 확인 필요없으면 10번을 바로 실행하세요. 
아닌 경우 0번 -> 1번 -> 2번 -> 5번 -> 7번입니다.

초기 개발환경이므로 버그가 많을 것입니다. feedback 주시면 감사드립니다.

......혹시 불쌍한 중생에게 커피라도 사주실 분은 아래 링크 클릭이라도...TT

https://buymeacoffee.com/aigengen5
