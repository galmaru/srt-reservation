"""SRT / KTX 자동 예매 웹 앱"""

import os
import uuid
import time
import threading
from datetime import datetime
from typing import Optional
from flask import Flask, render_template, request, session, redirect, url_for, jsonify, flash
import srt_bot
import korail_bot
import slack_notifier
import ntfy_notifier

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", os.urandom(24))
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_HTTPONLY"] = True


# ──────────────────────────────────────────────
# 백그라운드 모니터 상태 저장소
# ──────────────────────────────────────────────

_monitors: dict = {}   # monitor_id → monitor 정보
_monitor_lock = threading.Lock()


# ──────────────────────────────────────────────
# 헬퍼
# ──────────────────────────────────────────────

def _bot_module(provider: str):
    """provider에 맞는 bot 모듈 반환"""
    return srt_bot if provider == "srt" else korail_bot


def get_client():
    """현재 세션 자격증명으로 로그인한 client 반환"""
    train_id = session.get("train_id")
    train_pw = session.get("train_pw")
    provider = session.get("provider", "srt")
    if not train_id or not train_pw:
        return None
    try:
        return _bot_module(provider).login(train_id, train_pw)
    except Exception:
        return None


def _get_card_info_from_session():
    """현재 세션의 카드 정보를 dict로 반환 (백그라운드 스레드에 전달용, SRT 전용)"""
    if not session.get("card_saved"):
        return None
    return {
        "card_number":    session.get("card_number"),
        "card_password":  session.get("card_password"),
        "card_validation": session.get("card_validation"),
        "card_expire":    session.get("card_expire"),
        "card_installment": int(session.get("card_installment", 0)),
    }


# ──────────────────────────────────────────────
# 백그라운드 모니터 스레드
# ──────────────────────────────────────────────

def _run_monitor(monitor_id: str, provider: str, train_id: str, train_pw: str,
                 dep: str, arr: str, date: str, time_str: str,
                 train_dep_time: str, train_name: str, dep_time_display: str,
                 adult_count: int, seat_type: str, interval: int,
                 card_info: Optional[dict], slack_url: str, ntfy_url: str = ""):

    bot = _bot_module(provider)
    attempt = 0

    while True:
        with _monitor_lock:
            if _monitors.get(monitor_id, {}).get("status") != "running":
                return

        attempt += 1
        try:
            client = bot.login(train_id, train_pw)
            trains = bot.search_trains(client, dep, arr, date, time_str, available_only=False)
            target = next((t for t in trains if t.dep_time == train_dep_time), None)

            if target is None:
                _update_monitor(monitor_id, attempt=attempt,
                                message="해당 열차를 찾을 수 없습니다. 재시도 중...")
                time.sleep(interval)
                continue

            reserved = False

            # ① 빈좌석 있음 → 즉시 예매 시도
            if target.seat_available():
                try:
                    reservation = bot.make_reservation(client, target, adult_count, seat_type)
                    res_num = str(reservation.reservation_number)
                    _update_monitor(monitor_id, status="done", result_type="reservation",
                                    reservation_number=res_num,
                                    message=f"예매 완료 (예약번호: {res_num})")
                    slack_notifier.send(
                        slack_url, provider, "예매",
                        dep, arr, train_name, dep_time_display, res_num,
                        is_background=True,
                    )
                    ntfy_notifier.send(
                        ntfy_url, provider, "예매",
                        dep, arr, train_name, dep_time_display, res_num,
                        is_background=True,
                    )
                    return
                except Exception:
                    pass  # 예매 실패 → 예약대기 시도

            # ② 예약대기 가능 → 예약대기 등록 시도
            waiting_ok = (
                hasattr(target, "waiting_available") and target.waiting_available()
            )
            if not reserved and waiting_ok:
                try:
                    reservation = bot.make_waiting_reservation(client, target, adult_count, seat_type)
                    res_num = str(reservation.reservation_number)
                    _update_monitor(monitor_id, status="done", result_type="waiting",
                                    reservation_number=res_num,
                                    message=f"예약대기 등록 완료 (예약번호: {res_num})")
                    slack_notifier.send(
                        slack_url, provider, "예약대기",
                        dep, arr, train_name, dep_time_display, res_num,
                        is_background=True,
                    )
                    ntfy_notifier.send(
                        ntfy_url, provider, "예약대기",
                        dep, arr, train_name, dep_time_display, res_num,
                        is_background=True,
                    )
                    return
                except Exception:
                    pass  # 예약대기도 실패 → 재시도

            # ③ 모두 불가 → 재시도
            g = "일반실 매진" if not target.general_seat_available() else "일반실 가능"
            s = "특실 매진"   if not target.special_seat_available() else "특실 가능"
            _update_monitor(monitor_id, attempt=attempt,
                            message=f"[{attempt}회] 잔여석 없음 ({g} / {s})")

        except Exception as e:
            _update_monitor(monitor_id, attempt=attempt,
                            message=f"[{attempt}회] 오류: {e}")

        time.sleep(interval)


def _update_monitor(monitor_id: str, **kwargs):
    with _monitor_lock:
        if monitor_id in _monitors:
            _monitors[monitor_id].update(kwargs)


def _start_monitor(provider, train_id, train_pw,
                   dep, arr, date, time_str,
                   train_dep_time, train_name, dep_time_display,
                   adult_count, seat_type, interval,
                   card_info, slack_url, ntfy_url="") -> str:
    monitor_id = str(uuid.uuid4())
    with _monitor_lock:
        _monitors[monitor_id] = {
            "train_id":         train_id,
            "provider":         provider,
            "status":           "running",
            "dep":              dep,
            "arr":              arr,
            "date":             date,
            "time_str":         time_str,
            "train_name":       train_name,
            "dep_time_display": dep_time_display,
            "train_dep_time_raw": train_dep_time,
            "seat_type":        seat_type,
            "adult_count":      adult_count,
            "interval":         interval,
            "attempt":          0,
            "message":          "모니터링 시작 중...",
            "reservation_number": None,
            "result_type":      None,   # "reservation" | "waiting"
            "created_at":       datetime.now().strftime("%m/%d %H:%M"),
        }
    t = threading.Thread(
        target=_run_monitor,
        args=(monitor_id, provider, train_id, train_pw,
              dep, arr, date, time_str,
              train_dep_time, train_name, dep_time_display,
              adult_count, seat_type, interval,
              card_info, slack_url, ntfy_url),
        daemon=True,
    )
    t.start()
    return monitor_id


# ──────────────────────────────────────────────
# 라우트 — 인증
# ──────────────────────────────────────────────

@app.route("/")
def index():
    if session.get("train_id"):
        return redirect(url_for("search"))
    return render_template("login.html")


@app.route("/login", methods=["POST"])
def login():
    provider = request.form.get("provider", "srt")
    train_id = request.form.get("train_id", "").strip()
    train_pw = request.form.get("train_pw", "").strip()

    if not train_id or not train_pw:
        return render_template("login.html", error="아이디와 비밀번호를 입력해주세요.", provider=provider)

    try:
        _bot_module(provider).login(train_id, train_pw)
        session["train_id"] = train_id
        session["train_pw"] = train_pw
        session["provider"] = provider
        return redirect(url_for("search"))
    except Exception as e:
        return render_template("login.html", error=f"로그인 실패: {e}", provider=provider)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


# ──────────────────────────────────────────────
# 라우트 — 열차 검색 / 예매
# ──────────────────────────────────────────────

@app.route("/search")
def search():
    if not session.get("train_id"):
        return redirect(url_for("index"))
    provider = session.get("provider", "srt")
    stations = srt_bot.STATIONS if provider == "srt" else korail_bot.STATIONS
    return render_template("search.html", stations=stations, provider=provider)


@app.route("/search", methods=["POST"])
def search_post():
    if not session.get("train_id"):
        return redirect(url_for("index"))

    provider = session.get("provider", "srt")
    stations = srt_bot.STATIONS if provider == "srt" else korail_bot.STATIONS

    dep      = request.form.get("dep")
    arr      = request.form.get("arr")
    date     = request.form.get("date", "").replace("-", "")
    time_str = request.form.get("time", "00:00").replace(":", "") + "00"

    client = get_client()
    if not client:
        return render_template("login.html", error="세션이 만료됐습니다. 다시 로그인해주세요.")

    bot = _bot_module(provider)
    try:
        trains = bot.search_trains(client, dep, arr, date, time_str)
    except Exception as e:
        return render_template("search.html", stations=stations, provider=provider,
                               error=str(e), dep=dep, arr=arr, date=date, time=time_str[:4])

    session["search_params"] = {"dep": dep, "arr": arr, "date": date, "time": time_str}
    return render_template("search.html", stations=stations, provider=provider,
                           trains=trains, dep=dep, arr=arr, date=date, time=time_str[:4])


@app.route("/reserve", methods=["POST"])
def reserve():
    if not session.get("train_id"):
        return redirect(url_for("index"))

    # 중복 제출 방지
    submit_token = request.form.get("submit_token", "")
    if not submit_token or submit_token == session.get("last_submit_token"):
        return render_template("reserve_result.html",
                               error="중복 제출이 감지됐습니다. 검색 화면에서 다시 시도해주세요.",
                               success=False)
    session["last_submit_token"] = submit_token

    provider    = session.get("provider", "srt")
    params      = session.get("search_params", {})
    dep         = params.get("dep")
    arr         = params.get("arr")
    date        = params.get("date")
    time_str    = params.get("time")
    train_idx   = int(request.form.get("train_idx", 0))
    seat_type   = request.form.get("seat_type", "GENERAL_FIRST")
    adult_count = int(request.form.get("adult_count", 1))
    interval    = int(request.form.get("interval", 3))

    client = get_client()
    if not client:
        return render_template("login.html", error="세션이 만료됐습니다. 다시 로그인해주세요.")

    bot = _bot_module(provider)
    try:
        trains = bot.search_trains(client, dep, arr, date, time_str)
        train  = trains[train_idx]
    except Exception as e:
        return render_template("reserve_result.html", error=str(e), success=False)

    slack_url = session.get("slack_webhook_url") or os.environ.get("SLACK_WEBHOOK_URL", "")
    ntfy_url  = session.get("ntfy_topic_url")  or os.environ.get("NTFY_TOPIC_URL", "")

    # 잔여석 있음 → 즉시 예매
    if train.seat_available():
        try:
            reservation = bot.make_reservation(client, train, adult_count, seat_type)
            dep_time_disp = f"{train.dep_time[:2]}:{train.dep_time[2:4]}"
            res_num = str(reservation.reservation_number)
            slack_notifier.send(
                slack_url, provider, "예매",
                dep, arr, train.train_name, dep_time_disp, res_num,
            )
            ntfy_notifier.send(
                ntfy_url, provider, "예매",
                dep, arr, train.train_name, dep_time_disp, res_num,
            )
            return render_template("reserve_result.html", reservation=reservation,
                                   success=True, provider=provider)
        except Exception as e:
            return render_template("reserve_result.html", error=str(e), success=False)

    # 잔여석 없음 → 백그라운드 모니터 등록
    card_info = _get_card_info_from_session() if provider == "srt" else None
    monitor_id = _start_monitor(
        provider=provider,
        train_id=session["train_id"],
        train_pw=session["train_pw"],
        dep=dep, arr=arr, date=date, time_str=time_str,
        train_dep_time=train.dep_time,
        train_name=train.train_name,
        dep_time_display=train.dep_time[:2] + ":" + train.dep_time[2:4],
        adult_count=adult_count, seat_type=seat_type,
        interval=interval, card_info=card_info,
        slack_url=slack_url, ntfy_url=ntfy_url,
    )
    return redirect(url_for("monitors"))


# ──────────────────────────────────────────────
# 모니터 관리
# ──────────────────────────────────────────────

@app.route("/monitors")
def monitors():
    if not session.get("train_id"):
        return redirect(url_for("index"))
    train_id = session["train_id"]
    with _monitor_lock:
        user_monitors = [
            {"id": mid, **m}
            for mid, m in _monitors.items()
            if m.get("train_id") == train_id
        ]
    user_monitors.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return render_template("monitors.html", monitors=user_monitors)


@app.route("/api/monitor/<monitor_id>/status")
def monitor_status(monitor_id: str):
    if not session.get("train_id"):
        return jsonify({"status": "error", "message": "로그인 필요"})
    with _monitor_lock:
        m = _monitors.get(monitor_id)
    if not m or m.get("train_id") != session["train_id"]:
        return jsonify({"status": "error", "message": "모니터를 찾을 수 없습니다."})
    return jsonify({k: v for k, v in m.items() if k != "train_id"})


@app.route("/api/monitor/<monitor_id>/cancel", methods=["POST"])
def monitor_cancel(monitor_id: str):
    if not session.get("train_id"):
        return jsonify({"ok": False})
    with _monitor_lock:
        m = _monitors.get(monitor_id)
        if m and m.get("train_id") == session["train_id"]:
            m["status"] = "cancelled"
            m["message"] = "사용자가 취소했습니다."
    return jsonify({"ok": True})


@app.route("/api/monitor/<monitor_id>/delete", methods=["POST"])
def monitor_delete(monitor_id: str):
    if not session.get("train_id"):
        return jsonify({"ok": False})
    with _monitor_lock:
        m = _monitors.get(monitor_id)
        if m and m.get("train_id") == session["train_id"]:
            del _monitors[monitor_id]
    return jsonify({"ok": True})


# ──────────────────────────────────────────────
# 카드 설정 (SRT 전용, 백엔드 유지)
# ──────────────────────────────────────────────

@app.route("/card", methods=["GET", "POST"])
def card_settings():
    if not session.get("train_id"):
        return redirect(url_for("index"))

    if request.method == "POST":
        card_number     = request.form.get("card_number", "").replace("-", "").replace(" ", "")
        card_password   = request.form.get("card_password", "")
        card_validation = request.form.get("card_validation", "")
        card_expire     = request.form.get("card_expire", "").replace("/", "")
        installment     = request.form.get("installment", "0")

        if len(card_number) != 16 or not card_number.isdigit():
            return render_template("card.html", error="카드번호는 16자리 숫자여야 합니다.",
                                   saved=session.get("card_saved"))
        if len(card_password) != 2:
            return render_template("card.html", error="카드 비밀번호는 2자리여야 합니다.",
                                   saved=session.get("card_saved"))
        if len(card_expire) != 4 or not card_expire.isdigit():
            return render_template("card.html", error="유효기간은 YYMM 4자리 숫자여야 합니다.",
                                   saved=session.get("card_saved"))

        session["card_number"]      = card_number
        session["card_password"]    = card_password
        session["card_validation"]  = card_validation
        session["card_expire"]      = card_expire
        session["card_installment"] = installment
        session["card_saved"]       = True
        session.modified = True
        return render_template("card.html", saved=True,
                               card_number="*" * 12 + card_number[-4:],
                               card_expire=card_expire,
                               card_installment=installment)

    return render_template("card.html",
                           saved=session.get("card_saved", False),
                           card_number=("*" * 12 + session["card_number"][-4:]) if session.get("card_saved") else "",
                           card_expire=session.get("card_expire", ""),
                           card_installment=session.get("card_installment", "0"))


@app.route("/card/clear", methods=["POST"])
def card_clear():
    for key in ["card_number", "card_password", "card_validation",
                "card_expire", "card_installment", "card_saved"]:
        session.pop(key, None)
    session.modified = True
    return redirect(url_for("card_settings"))


# ──────────────────────────────────────────────
# 결제 (SRT 전용, 백엔드 유지)
# ──────────────────────────────────────────────

@app.route("/pay", methods=["GET", "POST"])
def pay():
    if not session.get("train_id"):
        return redirect(url_for("index"))

    if request.method == "GET":
        res_id = request.args.get("res_id")
        return render_template("pay.html", reservation_id=res_id)

    client = get_client()
    if not client:
        return render_template("login.html", error="세션이 만료됐습니다. 다시 로그인해주세요.")

    res_number      = request.form.get("reservation_id")
    card_number     = request.form.get("card_number", "").replace("-", "").replace(" ", "")
    card_password   = request.form.get("card_password")
    card_validation = request.form.get("card_validation")
    card_expire     = request.form.get("card_expire", "").replace("/", "")
    installment     = int(request.form.get("installment", 0))

    def _norm(n):
        """예약번호를 숫자 문자열로 정규화 (앞자리 0, 공백 차이 허용)"""
        return str(n or "").strip().lstrip("0")

    try:
        reservations = srt_bot.get_reservations(client)
        target = next(
            (r for r in reservations if _norm(r.reservation_number) == _norm(res_number)),
            None,
        )
        if not target:
            found = ", ".join(str(r.reservation_number) for r in reservations) or "없음"
            return render_template("pay.html", reservation_id=res_number,
                                   error=f"예매 내역을 찾을 수 없습니다. (조회된 예약번호: {found})")
        srt_bot.pay_reservation(client, target, card_number, card_password,
                                card_validation, card_expire, installment)
        return render_template("pay.html", reservation_id=res_number, success=True)
    except Exception as e:
        return render_template("pay.html", reservation_id=res_number, error=str(e))


# ──────────────────────────────────────────────
# 예매 내역 / 취소
# ──────────────────────────────────────────────

@app.route("/reservations")
def reservations():
    if not session.get("train_id"):
        return redirect(url_for("index"))
    provider = session.get("provider", "srt")
    client = get_client()
    items = []
    error = None
    if client:
        try:
            items = _bot_module(provider).get_reservations(client)
        except Exception as e:
            error = str(e)
    return render_template("reservations.html", reservations=items, error=error,
                           provider=provider)


@app.route("/cancel/<res_number>", methods=["POST"])
def cancel(res_number):
    if not session.get("train_id"):
        return redirect(url_for("index"))
    provider = session.get("provider", "srt")
    client = get_client()
    if not client:
        return redirect(url_for("reservations"))
    try:
        bot = _bot_module(provider)
        reservations_list = bot.get_reservations(client)
        target = next((r for r in reservations_list
                       if str(r.reservation_number) == res_number), None)
        if not target:
            flash(f"예약번호 {res_number}을(를) 찾을 수 없습니다.", "error")
        else:
            bot.cancel_reservation(client, target)
            flash("예매가 취소됐습니다.", "success")
    except Exception as e:
        flash(f"취소 실패: {e}", "error")
    return redirect(url_for("reservations"))


# ──────────────────────────────────────────────
# 예매 결과 (직접 접근 방어)
# ──────────────────────────────────────────────

@app.route("/reserve/result")
def reserve_result():
    return render_template("reserve_result.html", success=False,
                           error="잘못된 접근입니다.")


if __name__ == "__main__":
    app.run(debug=False, port=5001)
