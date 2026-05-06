# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 프로젝트 개요

SRT(수서고속철도) 자동 예매 봇. 좌석 가용 여부를 모니터링하다가 빈좌석 발생 시 자동 예매 및 결제까지 처리한다. CLI와 Flask 웹 앱 두 가지 인터페이스를 제공한다.

## 실행 명령어

```bash
# 의존성 설치
pip install -r requirements.txt

# 환경변수 설정 (자동 모드용)
cp env.example .env  # 편집 후 사용

# 웹 앱 실행 (포트 5001)
python app.py

# CLI 대화형 모드
python main.py

# CLI 자동 모드 (.env 설정 사용)
python main.py --auto
```

## 아키텍처

```
main.py / app.py (인터페이스 레이어)
        ↓
srt_bot.py (비즈니스 로직 레이어)
        ↓
SRTrain 라이브러리 (외부 SRT API 래퍼)
```

**`srt_bot.py`** — 핵심 로직. login, search_trains, make_reservation, pay_reservation, cancel 등 모든 SRT 작업을 담당한다.

**`app.py`** — Flask 웹 앱 (500+ 줄). 백그라운드 모니터링 스레드를 직접 관리하며, 세션에 자격증명과 카드정보를 저장한다.

**`main.py`** — CLI 진입점. Rich(UI) + Questionary(대화형 프롬프트) 사용.

## 주요 동작 방식

### 백그라운드 모니터링
- `_monitors` dict에 모니터 상태 저장, `_monitor_lock`으로 스레드 안전 보장
- 데몬 스레드로 실행하며 3초 간격으로 SRT API 폴링
- 상태: `running` → `available` / `done` / `cancelled`
- 스레드는 `_monitors[id]["status"]`를 확인하며 종료 여부 결정

### 예매 모드
- `"reserve"`: 빈좌석 발견 즉시 자동 예매
- `"notify"`: 빈좌석 알림만 (수동 예매용)

### 세션 관리
- 로그인 자격증명과 카드정보는 Flask 암호화 쿠키 세션에 저장 (DB 없음)
- 중복 제출 방지: UUID submit token을 세션에 저장

### NetFunnel 우회
SRT는 대기열 시스템(NetFunnel)을 사용한다. 검색할 때마다 NetFunnelHelper를 리셋해야 "Wrong Server ID" 에러를 방지할 수 있다 (`srt_bot.py:39`).

## 환경 변수 (.env)

자동 모드에서 사용하는 주요 변수:
- `SRT_ID`, `SRT_PW` — 로그인 정보
- `DEPARTURE`, `ARRIVAL` — 출발/도착역
- `DATE`, `TIME` — 날짜(YYYYMMDD), 시간(HHMMSS)
- `SEAT_TYPE` — 좌석 유형
- `AUTO_PAY` — 자동 결제 여부

`env.example` 참조.

## 배포

`vercel.json`으로 Vercel 서버리스 배포 구성되어 있음. `SECRET_KEY` 환경변수를 설정하지 않으면 재시작마다 세션이 무효화된다.
