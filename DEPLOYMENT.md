# 서스데반의 AI 판독기 웹사이트 배포

이 프로젝트는 브라우저에서 쓰는 Streamlit 웹사이트입니다. 소스를 GitHub에 올린 뒤 호스팅 앱을 생성하면 인터넷 접속 주소가 발급됩니다. 이 클라우드 개발 환경을 게시하는 것만으로 인터넷 웹사이트 주소가 생기지는 않습니다.

## 가장 간단한 방법: Streamlit Community Cloud

1. 본인 GitHub 저장소의 배포할 브랜치에 `app.py`, `requirements.txt`, `.streamlit/config.toml`, `textlab/` 패키지와 폰트·라이선스가 있는지 확인합니다. 이 프로젝트의 대상 저장소는 `ssdb1218/123`이며 배포 브랜치는 `main`입니다. `.env`, `.venv`, `.runs`, `.streamlit/secrets.toml`은 업로드하지 마세요.
2. [Streamlit Community Cloud](https://share.streamlit.io/)에 GitHub 계정으로 로그인해 **Create app**에서 저장소와 코드를 올린 브랜치, 메인 파일 `app.py`를 선택합니다. Python 버전은 **3.12**로 설정합니다.
3. Advanced settings의 Secrets에 아래 호스팅 설정을 넣습니다. 비밀값은 이 패널에 직접 입력하고 저장소나 채팅에 올리지 마세요.

```toml
TEXTLAB_HOSTED = "1"
TEXTLAB_SAVE_HISTORY = "0"
TEXTLAB_MAX_CONCURRENT_RUNS = "2"
```

4. 배포를 실행하면 서비스가 발급하는 HTTPS 주소로 접속할 수 있습니다. 여기 적힌 주소는 배포 화면의 주소이며 현재 프로젝트의 실서비스 주소는 아닙니다. 실제로 발급된 주소만 공유하세요.
5. 처음에는 키 없이 **테스트용 Mock**으로 시작해 글 입력·예시 글·수정 이력·PDF/DOCX 다운로드를 확인합니다. Mock 점수는 실제 탐지 결과가 아닙니다.

### 실제 API와 비공개 접근

실제 API를 쓰려면 같은 Secrets에 다음 항목도 추가합니다. 예시 문자열을 실제 키로 사용하지 마세요.

```toml
APP_ACCESS_PASSWORD = "본인이-정한-긴-사이트-비밀번호"
LLM_API_KEY = "본인의-OpenAI-API-키"
GPTZERO_API_KEY = "본인의-GPTZero-API-키"
LLM_MODEL = "gpt-4o-mini"
```

키는 `[openai]` 같은 섹션 아래가 아닌 **최상위 항목**으로 입력합니다. 저장 후 앱을 재시작하고 **실제 API** 모드의 `LLM_API_KEY`, `GPTZERO_API_KEY`가 모두 **설정됨**인지 확인하세요. 개발 환경에 저장한 키는 GitHub push나 Streamlit 배포로 자동 전달되지 않습니다. 기존의 비어 있지 않은 서버 환경변수가 있으면 Secrets보다 우선하므로, 해당 환경변수도 함께 확인하세요.

비밀번호가 설정되면 사이트 본문에 접근하기 전에 로그인해야 합니다. 호스팅 모드의 실제 API 실행에는 `APP_ACCESS_PASSWORD`가 필수입니다. 키 없는 공개 Mock 체험에는 비밀번호가 필요 없습니다. 이 방식은 공유 사이트 비밀번호이며 사용자별 계정이나 과금 기능은 아닙니다. 웹사이트 방문자에게 API 키를 입력받거나 브라우저로 키를 전달하지 않습니다.

외부 전송에 동의한 텍스트로 실제 API 모드를 실행해 초기 점수와 후보 점수가 실제 응답인지 확인합니다. API 권한·잔액·지원 언어·길이 제한은 서비스에서 확인하세요. 클라우드의 프록시 비밀 바인딩은 다른 호스팅으로 자동 이전되지 않으므로 새 서비스에 본인 키를 별도로 설정해야 합니다. 실제 청구 제한은 공급자 계정에서 설정하세요.

## Docker 또는 기존 서버

Docker와 Compose 없이도 Streamlit을 직접 실행할 수 있습니다. 컨테이너를 쓴다면 프로젝트 루트에서:

```bash
docker build -t textlab-website .
docker run --rm -p 8501:8501 --name textlab-website textlab-website
```

이미지는 Python 3.12와 일반 사용자로 실행합니다. 기본 포트는 8501이며 `PORT` 환경변수를 따릅니다. 호스팅 설정과 파일 이력 비활성화가 기본입니다. 실제 API를 쓸 경우 키와 사이트 비밀번호를 호스트의 비밀값 기능으로 주입하세요. 이미지나 Dockerfile에 값을 적지 마세요.

원격 서버에서 제공할 때는 도메인·HTTPS 역방향 프록시와 WebSocket 전달을 설정합니다. Streamlit의 CORS·XSRF 보호를 끄지 마세요. 관리형 호스팅은 HTTPS를 서비스가 제공하며, 직접 서버를 운영한다면 서버 운영자가 인증서와 프록시를 관리해야 합니다.

관리형 개발 환경의 프록시 CA가 필요할 때만 선택적 BuildKit 비밀 마운트를 사용합니다. 일반 호스팅에서는 필요 없습니다.

```bash
docker build --secret id=proxy_ca,src=/etc/ssl/certs/ca-certificates.crt -t textlab-website .
```

빌드 검증은 인증서를 유지하며, 빌드용 CA나 API 키는 이미지 레이어에 저장하지 않습니다. 컨테이너에서 별도 프록시 인증서가 필요한 HTTPS 요청은 런타임에도 해당 서비스가 요구하는 CA 설정을 제공해야 합니다.

## Render

저장소를 올린 후 Render에서 해당 저장소를 Blueprint로 연결하면 `render.yaml`을 사용할 수 있습니다. 무료 플랜의 제공 여부·사용량·휴면 조건은 해당 서비스에서 확인하세요. 자동 배포는 꺼져 있으며 실제 배포는 사용자가 실행합니다. 기본 Mock 배포에는 비밀값이 필요 없습니다.

실제 API를 쓰려면 Render의 Environment에서 `APP_ACCESS_PASSWORD`, `LLM_API_KEY`, `GPTZERO_API_KEY`를 비밀값으로 입력한 뒤 배포합니다. 서비스가 제공하는 `PORT`를 자동 사용하고 `/_stcore/health`로 상태를 확인합니다. 임의의 공개 주소를 미리 만들어 성공한 것으로 표시하지 않습니다.

## 운영과 확인

- 동시 실행은 한 프로세스당 기본 2개입니다. 초과하면 잠시 후 다시 시작하라는 메시지를 표시합니다. 서버가 여러 개면 이 한도는 각 프로세스에 적용됩니다.
- 호스팅 기본값은 서버 평문 이력 파일 저장을 끕니다. 세션에서 결과와 전체 이력을 내려받을 수 있습니다. 파일 저장을 켜려면 `TEXTLAB_SAVE_HISTORY=1`을 명시하고 저장·보관 정책을 따로 관리하세요. 기존 파일이 자동 삭제되지는 않습니다.
- 서버 휴면·재시작·배포 시 실행 중 작업과 승인 대기는 사라집니다. 브라우저를 닫는 것만으로 서버 작업이 즉시 중지되지는 않으므로 중지 버튼을 사용하세요. 결과는 다운로드해 보관하세요.
- 배포 후 Mock 실행·수정 승인/거절·중지·PDF/DOCX/JSON 다운로드를 확인하세요. 실제 API는 별도로 인증·서비스 응답을 검증해야 합니다.
- `python -m pytest -q`는 API 네트워크를 Mock 처리합니다. 통과해도 공개 배포나 유료 API 연결 성공을 뜻하지 않습니다.

이 파일은 배포 절차를 안내하는 문서이며, 문서에 적힌 명령이 자동으로 실행되지는 않습니다. 실제 사이트 주소와 실행 상태는 호스팅 서비스에서 확인하세요.
