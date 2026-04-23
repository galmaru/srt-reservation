# 기능 정의서 — SRT / KTX 자동 예매 봇

> **문서 유형**: 기능 정의서 (Feature Specification)  
> **작성일**: 2026-04-23  
> **최종 수정**: 2026-04-23  
> **버전**: v1.1  
> **대상 시스템**: 웹 앱(Flask) + CLI(Python)

---

## 목차

1. [시스템 구성](#1-시스템-구성)
2. [F-01 로그인 / 로그아웃](#2-f-01-로그인--로그아웃)
3. [F-02 열차 검색](#3-f-02-열차-검색)
4. [F-03 예매 실행](#4-f-03-예매-실행)
5. [F-04 백그라운드 모니터링](#5-f-04-백그라운드-모니터링)
6. [F-05 카드 정보 관리 (SRT 전용)](#6-f-05-카드-정보-관리-srt-전용)
7. [F-06 결제 (SRT 전용)](#7-f-06-결제-srt-전용)
8. [F-07 예매 내역 조회 및 취소](#8-f-07-예매-내역-조회-및-취소)
9. [F-08 CLI — 대화형 모드](#9-f-08-cli--대화형-모드)
10. [F-09 CLI — 자동 모드](#10-f-09-cli--자동-모드)
11. [공통 규칙](#11-공통-규칙)
12. [지원 역 목록](#12-지원-역-목록)

---

## 1. 시스템 구성

```
┌──────────────────────────────────────────────────────┐
│              사용자 인터페이스                          │
│   웹 앱 (Flask / 브라우저)   CLI (터미널)              │
│         app.py                  main.py              │
└──────────────────┬──────────────────┬────────────────┘
                   └────────┬─────────┘
                            │  provider = "srt" | "ktx"
                   ┌────────┴────────────────────┐
                   │                             │
          ┌────────▼────────┐        ┌───────────▼──────────┐
          │   srt_bot.py    │        │    korail_bot.py      │
          │  (SRT 로직)     │        │   (KTX 로직)          │
          └────────┬────────┘        └───────────┬──────────┘
                   │                             │
          ┌────────▼────────┐        ┌───────────▼──────────┐
          │  SRTrain 라이브   │        │   korail2 라이브러리   │
          │  러리 (외부 API)  │        │   (외부 API)          │
          └─────────────────┘        └──────────────────────┘
```

### 화면(라우트) 목록

| 화면명 | URL | 설명 |
|--------|-----|------|
| 로그인 | `GET /` | SRT/KTX 선택 + 로그인 |
| 열차 검색 | `GET/POST /search` | provider별 역 목록·열차 검색 |
| 예매 실행 | `POST /reserve` | 예매 또는 모니터 등록 |
| 모니터 목록 | `GET /monitors` | 실행 중/완료 모니터 현황 |
| 예매 내역 | `GET /reservations` | 예매 목록 조회 |
| 결제 | `GET/POST /pay` | SRT 전용 카드 결제 |
| 카드 설정 | `GET/POST /card` | SRT 전용 자동결제 카드 관리 |

---

## 2. F-01 로그인 / 로그아웃

### 2.1 기능 설명

SRT 또는 KTX 계정으로 인증한다. 로그인 화면 상단에서 먼저 provider를 선택한다.

### 2.2 Provider 선택 UI

로그인 화면 상단에 **SRT / KTX 토글 탭** 배치:
- SRT 탭 선택: 파란 색상, "SRT 계정으로 로그인하세요"
- KTX 탭 선택: 빨간 색상, "KTX(코레일) 계정으로 로그인하세요"

### 2.3 입력 항목

| 항목 | 타입 | 유효성 | 비고 |
|------|------|--------|------|
| provider | hidden | 필수 | "srt" 또는 "ktx" |
| 아이디 | text | 필수 | 전화번호 / 이메일 / 멤버십번호 |
| 비밀번호 | password | 필수 | — |

### 2.4 처리 흐름

```
provider 선택 + 자격증명 입력 → 빈값 검사
  ↓
provider == "srt" → srt_bot.login()
provider == "ktx" → korail_bot.login()
  ├─ 성공 → session에 train_id / train_pw / provider 저장 → /search 리다이렉트
  └─ 실패 → 에러 메시지 + 로그인 화면 유지
```

### 2.5 세션 구조 (변경)

| 키 | 값 | 설명 |
|----|-----|------|
| `train_id` | str | 로그인 아이디 (구: srt_id) |
| `train_pw` | str | 로그인 비밀번호 (구: srt_pw) |
| `provider` | "srt" \| "ktx" | 선택한 서비스 |

### 2.6 내비게이션 변경

- 헤더에 **provider 배지** 표시: `SRT`(파랑) 또는 `KTX`(빨강)
- **카드 설정** 메뉴: SRT 로그인 시만 노출 (KTX는 결제 미지원으로 숨김)

---

## 3. F-02 열차 검색

### 3.1 기능 설명

출발역·도착역·날짜·시각 조건으로 열차를 조회한다. provider에 따라 역 목록과 API 호출이 달라진다.

### 3.2 검색 조건

| 항목 | 타입 | SRT | KTX |
|------|------|-----|-----|
| 출발역 | select | 32개 역 | 28개 역 |
| 도착역 | select | 32개 역 | 28개 역 |
| 날짜 | date | 필수 | 필수 |
| 출발 시각 이후 | time | 기본 08:00 | 기본 08:00 |

### 3.3 열차 통합 인터페이스 (KTXTrainAdapter)

korail_bot은 korail2 `Train` 객체를 래핑하여 SRT Train과 동일한 인터페이스를 제공한다. 템플릿 변경 없이 provider 무관하게 동일 렌더링 가능.

| 속성/메서드 | SRT (원본) | KTX (Adapter 매핑) |
|-------------|-----------|-------------------|
| `train_name` | 직접 | `train.train_type_name` |
| `dep_station_name` | 직접 | `train.dep_name` |
| `arr_station_name` | 직접 | `train.arr_name` |
| `dep_time` | HHMMSS | `train.dep_time` |
| `arr_time` | HHMMSS | `train.arr_time` |
| `running_time` | 직접 | `train.run_time` |
| `general_seat_available()` | 직접 | `train.has_general_seat()` |
| `special_seat_available()` | 직접 | `train.has_special_seat()` |
| `seat_available()` | 직접 | general \|\| special |

### 3.4 검색 결과 UI

SRT와 동일한 카드형 레이아웃. 열차명 좌측에 **provider 색상 점** 표시.

### 3.5 예매 버튼

| 버튼 | 동작 |
|------|------|
| 🔔 빈좌석 알림만 | mode=`notify` |
| ⚡ 자동 예매하기 | mode=`reserve` |

---

## 4. F-03 예매 실행

### 4.1 기능 설명

선택 열차를 예매한다. 잔여석 여부·모드·provider에 따라 분기한다.

### 4.2 처리 분기

```
잔여석 확인 (재검색)
  │
  ├─ 잔여석 있음 + mode=reserve
  │    → 즉시 예매
  │    ├─ provider=srt + 카드 저장 → 자동결제 → 결과 화면
  │    ├─ provider=srt + 카드 없음 → "결제하기" 버튼 표시
  │    └─ provider=ktx            → "코레일 앱에서 20분 내 결제" 안내
  │
  ├─ 잔여석 있음 + mode=notify
  │    → Flash 메시지 → 검색 화면
  │
  └─ 잔여석 없음
       → 백그라운드 모니터 등록 (provider 포함) → /monitors
```

### 4.3 예매 결과 화면 — provider별

| 케이스 | 표시 |
|--------|------|
| SRT + 자동결제 성공 | "예매 & 결제 완료" |
| SRT + 자동결제 실패 | 예약번호 + 결제 실패 사유 + "직접 결제" 버튼 |
| SRT + 카드 미저장 | 예약번호 + "결제하기" / "카드 저장하기" 버튼 |
| KTX | 예약번호 + "코레일 앱에서 20분 내 결제하세요" 안내 |
| 예매 실패 | 에러 메시지 |

---

## 5. F-04 백그라운드 모니터링

### 5.1 기능 설명

매진 열차를 대상으로 백그라운드에서 빈좌석을 자동 감시한다.

### 5.2 모니터 등록 정보 (provider 필드 추가)

| 항목 | 설명 |
|------|------|
| `provider` | "srt" / "ktx" — 스레드 내 bot 선택 기준 |
| `train_id` / `train_pw` | 폴링 시 재로그인용 |
| 열차 식별자 | dep_time (HHMMSS) |
| 카드 정보 | SRT + 카드 저장 시만 전달, KTX는 항상 None |

### 5.3 폴링 — provider 분기

```python
bot = srt_bot if provider == "srt" else korail_bot
client = bot.login(train_id, train_pw)
trains = bot.search_trains(client, dep, arr, date, time_str, available_only=False)
...
if card_info and provider == "srt":   # KTX는 결제 미지원
    srt_bot.pay_reservation(...)
```

### 5.4 모니터 카드 UI 변경

- 카드 헤더에 **`SRT`(파랑) / `KTX`(빨강) 배지** 추가
- KTX done 상태: "결제하기" 버튼 미표시, "코레일 앱에서 결제하세요" 텍스트 노출

### 5.5 상태 흐름 / API

SRT와 동일 (`running` → `available` / `done` / `cancelled`).  
API 엔드포인트(`/api/monitor/{id}/status`, `/cancel`, `/delete`) 변경 없음.

---

## 6. F-05 카드 정보 관리 (SRT 전용)

**KTX 로그인 상태에서는 카드 설정 메뉴 미노출.**

### 6.1 입력 항목 및 유효성

| 항목 | 유효성 규칙 |
|------|-------------|
| 카드번호 | 16자리 숫자 |
| 카드 비밀번호 앞 2자리 | 2자리 |
| 유효기간 | YYMM 4자리 |
| 생년월일/사업자번호 | 6 또는 10자리 |
| 할부 개월 | 0·2~12·24 |

### 6.2 저장 정책

- Flask 암호화 쿠키 전용 (DB·로그 미기록)
- 카드번호 표시: `************XXXX`
- 로그아웃 시 자동 삭제

---

## 7. F-06 결제 (SRT 전용)

**KTX는 결제 화면·버튼 없음. korail2가 결제 API 미지원.**

| 결제 경로 | SRT | KTX |
|-----------|-----|-----|
| 자동결제 (즉시 예매) | ✅ | ❌ |
| 자동결제 (모니터 예매) | ✅ | ❌ |
| 수동결제 (`/pay`) | ✅ | ❌ |

처리 흐름은 기존 SRT 결제 로직과 동일.

---

## 8. F-07 예매 내역 조회 및 취소

### 8.1 통합 인터페이스 (KTXReservationAdapter)

| 속성 | SRT (원본) | KTX (Adapter 매핑) |
|------|-----------|-------------------|
| `reservation_number` | 직접 | `rsv.rsv_id` |
| `dep_station_name` | 직접 | `rsv.dep_name` |
| `arr_station_name` | 직접 | `rsv.arr_name` |
| `train_name` | 직접 | `rsv.train_type_name` |
| `is_paid` | 직접 | 항상 `False` |

### 8.2 UI 변경

- KTX 예매 → "미결제" 배지 고정
- KTX 예매 → "결제하기" 버튼 미표시, "코레일 앱에서 결제" 문구 노출
- 취소 처리: provider에 따라 `srt_bot` 또는 `korail_bot` 호출

---

## 9. F-08 CLI — 대화형 모드

기존 메뉴 동일. **로그인 단계에서 SRT/KTX 선택 추가.**

```
[provider 선택]
  > SRT
  > KTX (코레일)

[로그인]
  → 아이디 / 비밀번호 입력
```

KTX 예매 성공 후 "코레일 앱에서 20분 내 결제하세요" 메시지 출력.

---

## 10. F-09 CLI — 자동 모드

### 추가/변경 환경변수

| 변수 | 필수 | 기본값 | 설명 |
|------|------|--------|------|
| `PROVIDER` | — | srt | "srt" 또는 "ktx" |
| `SRT_ID` | provider=srt | — | SRT 아이디 |
| `SRT_PW` | provider=srt | — | SRT 비밀번호 |
| `KTX_ID` | provider=ktx | — | KTX 아이디 |
| `KTX_PW` | provider=ktx | — | KTX 비밀번호 |
| `AUTO_PAY` | — | false | **SRT 전용** |

---

## 11. 공통 규칙

### 11.1 반봇 처리

| provider | 방식 |
|----------|------|
| SRT | 매 검색마다 `NetFunnelHelper` 재초기화 |
| KTX | korail2 기본 세션 (별도 우회 불필요) |

### 11.2 중복 예매 방지

`make_reservation()` 성공 직후 즉시 `status="done"` — provider 무관 공통 적용.

### 11.3 보안

| 항목 | SRT | KTX |
|------|-----|-----|
| 카드 정보 | Flask 암호화 쿠키 | 해당 없음 |
| 세션 쿠키 | HttpOnly, SameSite=Lax | 동일 |
| 모니터 API 소유자 검증 | train_id 기준 | 동일 |

---

## 12. 지원 역 목록

### SRT (32개역)
수서, 동탄, 평택지제, 천안아산, 오송, 대전, 김천(구미), 동대구, 서대구, 밀양, 울산(통도사), 부산, 경주, 포항, 광주송정, 나주, 목포, 익산, 전주, 정읍, 남원, 여수EXPO, 여천, 순천, 곡성, 구례구, 공주, 마산, 창원, 창원중앙, 진영, 진주

### KTX (28개역)
서울, 용산, 영등포, 수원, 광명, 천안아산, 오송, 대전, 김천구미, 동대구, 경주, 울산, 부산, 광주송정, 나주, 목포, 익산, 전주, 정읍, 남원, 순천, 여수EXPO, 마산, 창원, 창원중앙, 진주, 청량리, 강릉
