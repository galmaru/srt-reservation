"""SRT 자동 예매 핵심 로직"""

from SRT import SRT
from SRT.passenger import Adult, Child, Senior
from SRT.seat_type import SeatType
from SRT.netfunnel import NetFunnelHelper

STATIONS = [
    "수서", "동탄", "평택지제", "천안아산", "오송",
    "대전", "김천(구미)", "동대구", "서대구", "밀양",
    "울산(통도사)", "부산", "경주", "포항",
    "광주송정", "나주", "목포", "익산", "전주", "정읍",
    "남원", "여수EXPO", "여천", "순천", "곡성", "구례구",
    "공주", "마산", "창원", "창원중앙", "진영", "진주",
]

SEAT_TYPE_MAP = {
    "GENERAL_FIRST": SeatType.GENERAL_FIRST,
    "GENERAL_ONLY": SeatType.GENERAL_ONLY,
    "SPECIAL_FIRST": SeatType.SPECIAL_FIRST,
    "SPECIAL_ONLY": SeatType.SPECIAL_ONLY,
}


def login(srt_id: str, srt_pw: str) -> SRT:
    """SRT 로그인 (NetFunnel 대기열 우회 포함)"""
    netfunnel = NetFunnelHelper()
    return SRT(srt_id, srt_pw, netfunnel_helper=netfunnel)


def _patch_train(train):
    """SRTTrain에 없는 waiting_available() 메서드를 동적으로 추가"""
    if not hasattr(train, "waiting_available"):
        # SRTrain 라이브러리가 waiting_available을 지원하지 않으면 항상 False 반환
        train.waiting_available = lambda: (
            getattr(train, "reserve_wait_possible_flag", "") == "Y"
        )
    return train


def search_trains(srt: SRT, dep: str, arr: str, date: str, time: str, available_only: bool = False):
    srt.netfunnel_helper = NetFunnelHelper()
    trains = srt.search_train(dep, arr, date=date, time=time, available_only=available_only)
    return [_patch_train(t) for t in trains]


def make_reservation(srt: SRT, train, adult_count: int = 1, seat_type_key: str = "GENERAL_FIRST"):
    """열차 예매"""
    passengers = [Adult(adult_count)]
    seat_type = SEAT_TYPE_MAP.get(seat_type_key, SeatType.GENERAL_FIRST)
    return srt.reserve(train, passengers=passengers, special_seat=seat_type)


def make_waiting_reservation(srt: SRT, train, adult_count: int = 1, seat_type_key: str = "GENERAL_FIRST"):
    """예약대기 등록"""
    passengers = [Adult(adult_count)]
    seat_type = SEAT_TYPE_MAP.get(seat_type_key, SeatType.GENERAL_FIRST)
    return srt.reserve(train, passengers=passengers, special_seat=seat_type, reserve_waiting=True)


def pay_reservation(
    srt: SRT,
    reservation,
    card_number: str,
    card_password: str,
    card_validation_number: str,
    card_expire_date: str,
    installment: int = 0,
):
    """신용카드 결제"""
    return srt.pay_with_card(
        reservation,
        number=card_number,
        password=card_password,
        validation_number=card_validation_number,
        expire_date=card_expire_date,
        installment=installment,
    )


def get_reservations(srt: SRT):
    """예매 목록 조회"""
    return srt.get_reservations()


def cancel_reservation(srt: SRT, reservation):
    """예매 취소"""
    return srt.cancel(reservation)
