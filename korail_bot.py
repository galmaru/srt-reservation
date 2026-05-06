"""KTX 자동 예매 핵심 로직 (korail2 라이브러리 래핑)"""

from korail2 import Korail

STATIONS = [
    "서울", "용산", "영등포", "수원", "광명", "천안아산", "오송",
    "대전", "김천구미", "동대구", "경주", "울산", "부산",
    "광주송정", "나주", "목포", "익산", "전주", "정읍",
    "남원", "순천", "여수EXPO", "마산", "창원", "창원중앙",
    "진주", "청량리", "강릉",
]

# korail2 좌석 상태 문자열
_SEAT_AVAILABLE = "있음"
_SEAT_WAITING   = "예약대기"


class KTXTrainAdapter:
    """korail2 Train 객체를 SRT Train과 동일한 인터페이스로 래핑"""

    def __init__(self, train):
        self._train = train

    @property
    def train_name(self):
        return getattr(self._train, "train_type_name", "") or getattr(self._train, "train_type", "")

    @property
    def dep_station_name(self):
        return getattr(self._train, "dep_name", "")

    @property
    def arr_station_name(self):
        return getattr(self._train, "arr_name", "")

    @property
    def dep_time(self):
        """HHMMSS 형식"""
        return getattr(self._train, "dep_time", "")

    @property
    def arr_time(self):
        """HHMMSS 형식"""
        return getattr(self._train, "arr_time", "")

    @property
    def running_time(self):
        return getattr(self._train, "run_time", "")

    def general_seat_available(self) -> bool:
        state = getattr(self._train, "general_seat_state", "")
        return state == _SEAT_AVAILABLE

    def special_seat_available(self) -> bool:
        state = getattr(self._train, "special_seat_state", "")
        return state == _SEAT_AVAILABLE

    def seat_available(self) -> bool:
        return self.general_seat_available() or self.special_seat_available()

    def waiting_available(self) -> bool:
        """일반실 또는 특실이 예약대기 상태인 경우"""
        gen = getattr(self._train, "general_seat_state", "")
        spe = getattr(self._train, "special_seat_state", "")
        return _SEAT_WAITING in gen or _SEAT_WAITING in spe

    def __getattr__(self, name):
        return getattr(self._train, name)

    def __repr__(self):
        return (f"KTXTrainAdapter({self.train_name} "
                f"{self.dep_station_name}→{self.arr_station_name} "
                f"{self.dep_time[:2]}:{self.dep_time[2:4]})")


class KTXReservationAdapter:
    """korail2 Reservation 객체를 SRT Reservation과 동일한 인터페이스로 래핑"""

    def __init__(self, rsv):
        self._rsv = rsv

    @property
    def reservation_number(self):
        # korail2 버전에 따라 속성명이 다를 수 있음
        return (getattr(self._rsv, "rsv_id", None)
                or getattr(self._rsv, "pnr_no", None)
                or getattr(self._rsv, "reservation_number", None)
                or "")

    @property
    def dep_station_name(self):
        return getattr(self._rsv, "dep_name", "")

    @property
    def arr_station_name(self):
        return getattr(self._rsv, "arr_name", "")

    @property
    def train_name(self):
        return (getattr(self._rsv, "train_type_name", "")
                or getattr(self._rsv, "train_type", ""))

    @property
    def is_paid(self):
        """KTX는 항상 미결제 — 코레일 앱에서 직접 결제"""
        return False

    def __getattr__(self, name):
        return getattr(self._rsv, name)


# ──────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────

def login(ktx_id: str, ktx_pw: str) -> Korail:
    """KTX 로그인"""
    return Korail(ktx_id, ktx_pw, auto_login=True)


def search_trains(korail: Korail, dep: str, arr: str, date: str, time: str,
                  available_only: bool = False):
    """
    열차 검색
    date: yyyyMMdd 형식 (예: 20260403)
    time: HHmmss 형식 (예: 080000)
    """
    trains = korail.search_train(dep, arr, date, time, available_only=available_only)
    return [KTXTrainAdapter(t) for t in trains]


def make_reservation(korail: Korail, train_adapter: KTXTrainAdapter,
                     adult_count: int = 1, seat_type_key: str = "GENERAL_FIRST"):
    """열차 예매"""
    rsv = korail.reserve(train_adapter._train)
    return KTXReservationAdapter(rsv)


def make_waiting_reservation(korail: Korail, train_adapter: KTXTrainAdapter,
                              adult_count: int = 1, seat_type_key: str = "GENERAL_FIRST"):
    """예약대기 등록"""
    # korail2 라이브러리의 waiting 예약 방식
    # 라이브러리 버전에 따라 파라미터명이 다를 수 있어 순차 시도
    try:
        rsv = korail.reserve(train_adapter._train, is_waiting=True)
    except TypeError:
        try:
            rsv = korail.reserve(train_adapter._train, waiting=True)
        except TypeError:
            raise NotImplementedError("현재 korail2 버전에서 예약대기를 지원하지 않습니다.")
    return KTXReservationAdapter(rsv)


def get_reservations(korail: Korail):
    """예매 목록 조회"""
    rsvs = korail.reservations()
    return [KTXReservationAdapter(r) for r in rsvs]


def cancel_reservation(korail: Korail, reservation_adapter: KTXReservationAdapter):
    """예매 취소"""
    return korail.cancel(reservation_adapter._rsv)
