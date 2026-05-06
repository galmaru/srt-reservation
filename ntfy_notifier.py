"""ntfy 푸시 알림 전송 모듈

예매 또는 예약대기 성공 시 ntfy 토픽으로 알림을 전송한다.
전송 실패는 예매 결과에 영향을 주지 않는다 (best-effort).

ntfy 사용법:
  - 앱 설치 후 토픽 구독: https://ntfy.sh/내토픽명
  - NTFY_TOPIC 환경변수에 "https://ntfy.sh/내토픽명" 형식으로 설정
  - 자체 호스팅 서버도 동일한 형식으로 지원
"""

import logging
import urllib.request
import urllib.error

logger = logging.getLogger(__name__)


def send(
    topic_url: str,
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
    ntfy 토픽으로 예매 결과 푸시 알림을 전송한다.

    Args:
        topic_url: ntfy 토픽 URL (예: "https://ntfy.sh/my-topic")
        provider: "srt" | "ktx"
        result_type: "예매" | "예약대기"
        dep: 출발역
        arr: 도착역
        train_name: 열차명 (예: "SRT 313", "KTX 103")
        dep_time: 출발 시각 (예: "09:00")
        reservation_number: 예약번호

    Returns:
        True: 전송 성공, False: 전송 실패 (예매에는 영향 없음)
    """
    if not topic_url:
        return False

    provider_label = provider.upper()
    emoji = "🚄"

    title = f"{emoji} {provider_label} {result_type} 완료"
    if is_background:
        title = f"{emoji} {provider_label} 자동 {result_type} 완료"

    payment_note = (
        "" if provider == "srt"
        else " | 코레일 앱에서 결제 필요"
    )

    body = (
        f"{dep} → {arr}  {train_name}  {dep_time} 출발\n"
        f"예약번호: {reservation_number}{payment_note}"
    )

    # 우선순위: 예매=high, 예약대기=default
    priority = "high" if result_type == "예매" else "default"
    tags = "white_check_mark" if result_type == "예매" else "hourglass_flowing_sand"

    try:
        req = urllib.request.Request(
            topic_url,
            data=body.encode("utf-8"),
            headers={
                "Title": title.encode("utf-8"),
                "Priority": priority,
                "Tags": tags,
                "Content-Type": "text/plain; charset=utf-8",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status == 200
    except Exception as e:
        logger.warning("ntfy 알림 전송 실패 (예매에는 영향 없음): %s", e)
        return False
