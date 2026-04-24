"""Slack Incoming Webhook 알림 전송 모듈

예매 또는 예약대기 성공 시 Slack으로 메시지를 전송한다.
전송 실패는 예매 결과에 영향을 주지 않는다 (best-effort).
"""

import logging
import urllib.request
import urllib.error
import json

logger = logging.getLogger(__name__)


def send(
    webhook_url: str,
    provider: str,
    result_type: str,        # "예매" | "예약대기"
    dep: str,
    arr: str,
    train_name: str,
    dep_time: str,           # "HH:MM" 형식
    reservation_number: str,
    is_background: bool = False,
) -> bool:
    """
    Slack Incoming Webhook으로 예매 결과 알림을 전송한다.

    Args:
        webhook_url: Slack Incoming Webhook URL
        provider: "srt" | "ktx"
        result_type: "예매" | "예약대기"
        dep: 출발역
        arr: 도착역
        train_name: 열차명 (예: "SRT 313", "KTX 103")
        dep_time: 출발 시각 (예: "09:00")
        reservation_number: 예약번호
        is_background: True이면 백그라운드 자동 예매 알림

    Returns:
        True: 전송 성공, False: 전송 실패 (예매에는 영향 없음)
    """
    if not webhook_url:
        return False

    provider_label = provider.upper()
    emoji = "🚄"

    if is_background:
        header = f"{emoji} {provider_label} 자동 {result_type} 완료"
    else:
        header = f"{emoji} {provider_label} {result_type} 완료"

    payment_info = (
        "완료 ✅" if provider == "srt"
        else "코레일 앱에서 진행 필요"
    )

    text = (
        f"*{header}*\n"
        f"노선: {dep} → {arr}\n"
        f"열차: {train_name}  출발: {dep_time}\n"
        f"예약번호: {reservation_number}\n"
        f"결제: {payment_info}"
    )

    payload = json.dumps({"text": text}).encode("utf-8")

    try:
        req = urllib.request.Request(
            webhook_url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status == 200
    except Exception as e:
        logger.warning("Slack 알림 전송 실패 (예매에는 영향 없음): %s", e)
        return False
