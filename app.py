"""SRT 자동 예매 웹 앱"""

import os
import uuid
import time
import threading
from datetime import datetime
from typing import Optional
from flask import Flask, render_template, request, session, redirect, url_for, jsonify, flash
import srt_bot

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

def get_srt() -> Optional[srt_bot.SRT]:
    srt_id = session.get("srt_id")
    srt_pw = session.get("srt_pw")
    if not srt_id or not srt_pw:
        return None
    try:
        return srt_bot.login(srt_id, srt_pw)
    except Exception:
        return None


def auto_pay_if_saved(srt, reservation):
    """저장된 카드로 자동 결제. True=성공, None=카드없음, str=에러"""
    if not session.get("card_saved"):
        return None
    try:
        srt_bot.pay_reservation(
            srt, reservation,
            card_number=session["card_number"],
            card_password=session["card_password"],
            card_validation_number=session["card_validation"],
            card_expire_date=session["card_expire"],
            installment=int(session.get("card_installment", 0)),
        )
        return True
    except Exception as e:
        return str(e)


def _get_card_info_from_session():
    """현재 세션의 카드 정보를 dict로 반환 (백그라운드 스레드에 전달용)"""
    if not session.get("card_saved"):
        return None
    return {
        "card_number": session.get("card_number"),
        "card_password": session.get("card_password"),
        "card_validation": session.get("card_validation"),
        "card_expire": session.get("card_expire"),
        "card_installment": int(session.get("card_installment", 0)),
    }


# ──────────────────────────────────────────────
# 백그라운드 모니터 스레드
# ──────────────────────────────────────────────

def _run_monitor(monitor_id: str, srt_id: str, srt_pw: str,
                 dep: str, arr: str, date: str, time_str: str,
                 train_dep_time: str, adult_count: int,
                 seat_type: str, mode: str, interval: int,
                 card_info: Optional[dict]):
    attempt = 0
    while True:
        with _monitor_lock:
            if _monitors.get(monitor_id, {}).get("status") != "running":
                return

        attempt += 1
        try:
            srt = srt_bot.login(srt_id, srt_pw)
            trains = srt_bot.search_trains(srt, dep, arr, date, time_str, available_only=False)
            target = next((t for t in trains if t.dep_time == train_dep_time), None)

            if target is None:
                _update_monitor(monitor_id, attempt=attempt,
                                message="해당 열차를 찾을 수 없습니다. 재시도 중...")
            elif target.seat_available():
                if mode == "notify":
                    g = "일반실 가능" if target.general_seat_available else ""
                    s = "특실 가능"  if target.special_seat_available  else ""
                    seat_info = " / ".join(filter(None, [g, s]))
                    _update_monitor(monitor_id, status="available",
                                    seat_info=seat_info,
                                    message=f"빈좌석 감지: {seat_info}")
                    return
                else:
                    # 자동 예매
                    reservation = srt_bot.make_reservation(srt, target, adult_count, seat_type)
                    res_num = str(reservation.reservation_number)
                    auto_paid = False
                    pay_error = None
                    if card_info:
                        try:
                            srt_bot.pay_reservation(
                                srt, reservation,
                                card_number=card_info["card_number"],
                                card_password=card_info["card_password"],
                                card_validation_number=card_info["card_validation"],
                                card_expire_date=card_info["card_expire"],
                                installment=card_info["card_installment"],
                            )
                            auto_paid = True
                        except Exception as e:
                            pay_error = str(e)
                    _update_monitor(monitor_id, status="done",
                                    reservation_number=res_num,
                                    auto_paid=auto_paid, pay_error=pay_error,
                                    message="예매 완료" + (" + 결제 완료" if auto_paid else ""))
                    return
            else:
                g = "일반실 매진" if not target.general_seat_available else "일반실 가능"
                s = "특실 매진"  if not target.special_seat_available  else "특실 가능"
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


def _start_monitor(srt_id, srt_pw, dep, arr, date, time_str,
                   train_dep_time, adult_count, seat_type,
                   mode, interval, card_info,
                   train_name, dep_time_display) -> str:
    monitor_id = str(uuid.uuid4())
    with _monitor_lock:
        _monitors[monitor_id] = {
            "srt_id": srt_id,
            "status": "running",
            "mode": mode,
            "dep": dep, "arr": arr,
            "date": date, "time_str": time_str,
            "train_name": train_name,
            "dep_time_display": dep_time_display,
            "train_dep_time_raw": train_dep_time,
            "seat_type": seat_type,
            "adult_count": adult_count,
            "interval": interval,
            "attempt": 0,
            "message": "모니터링 시작 중...",
            "reservation_number": None,
            "auto_paid": False,
            "pay_error": None,
            "seat_info": None,
            "created_at": datetime.now().strftime("%m/%d %H:%M"),
        }
    t = threading.Thread(
        target=_run_monitor,
        args=(monitor_id, srt_id, srt_pw, dep, arr, date, time_str,
              train_dep_time, adult_count, seat_type, mode, interval, card_info),
        daemon=True,
    )
    t.start()
    return monitor_id


# ──────────────────────────────────────────────
# 라우트
# ──────────────────────────────────────────────

@app.route("/")
def index():
    if session.get("srt_id"):
        return redirect(url_for("search"))
    return render_template("login.html")


@app.route("/login", methods=["POST"])
def login():
    srt_id = request.form.get("srt_id", "").strip()
    srt_pw = request.form.get("srt_pw", "").strip()
    if not srt_id or not srt_pw:
        return render_template("login.html", error="아이디와 비밀번호를 입력해주세요.")
    try:
        srt_bot.login(srt_id, srt_pw)
        session["srt_id"] = srt_id
        session["srt_pw"] = srt_pw
        return redirect(url_for("search"))
    except Exception as e:
        return render_template("login.html", error=f"로그인 실패: {e}")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


@app.route("/search")
def search():
    if not session.get("srt_id"):
        return redirect(url_for("index"))
    return render_template("search.html", stations=srt_bot.STATIONS)


@app.route("/search", methods=["POST"])
def search_post():
    if not session.get("srt_id"):
        return redirect(url_for("index"))

    dep = request.form.get("dep")
    arr = request.form.get("arr")
    date = request.form.get("date", "").replace("-", "")
    time_str = request.form.get("time", "00:00").replace(":", "") + "00"

    srt = get_srt()
    if not srt:
        return render_template("login.html", error="세션이 만료됐습니다. 다시 로그인해주세요.")

    try:
        trains = srt_bot.search_trains(srt, dep, arr, date, time_str)
    except Exception as e:
        return render_template("search.html", stations=srt_bot.STATIONS, error=str(e))

    session["search_params"] = {"dep": dep, "arr": arr, "date": date, "time": time_str}
    return render_template("search.html", stations=srt_bot.STATIONS, trains=trains,
                           dep=dep, arr=arr, date=date, time=time_str[:4])


@app.route("/reserve", methods=["POST"])
def reserve():
    if not session.get("srt_id"):
        return redirect(url_for("index"))

    # 중복 제출 방지
    submit_token = request.form.get("submit_token", "")
    if not submit_token or submit_token == session.get("last_submit_token"):
        return render_template("reserve_result.html",
                               error="중복 제출이 감지됐습니다. 검색 화면에서 다시 시도해주세요.",
                               success=False)
    session["last_submit_token"] = submit_token

    params = session.get("search_params", {})
    dep      = params.get("dep")
    arr      = params.get("arr")
    date     = params.get("date")
    time_str = params.get("time")
    train_idx   = int(request.form.get("train_idx", 0))
    seat_type   = request.form.get("seat_type", "GENERAL_FIRST")
    adult_count = int(request.form.get("adult_count", 1))
    interval    = int(request.form.get("interval", 3))
    mode        = request.form.get("mode", "reserve")

    srt = get_srt()
    if not srt:
        return render_template("login.html", error="세션이 만료됐습니다. 다시 로그인해주세요.")

    try:
        trains = srt_bot.search_trains(srt, dep, arr, date, time_str)
        train  = trains[train_idx]
    except Exception as e:
        return render_template("reserve_result.html", error=str(e), success=False)

    # 잔여석 있고 자동예매 모드 → 즉시 예매
    if train.seat_available() and mode == "reserve":
        try:
            reservation = srt_bot.make_reservation(srt, train, adult_count, seat_type)
            session["reservation_id"] = str(reservation.reservation_number)
            pay_result = auto_pay_if_saved(srt, reservation)
            return render_template(
                "reserve_result.html", reservation=reservation, success=True,
                pay_success=(pay_result is True),
                pay_error=(pay_result if isinstance(pay_result, str) else None),
            )
        except Exception as e:
            return render_template("reserve_result.html", error=str(e), success=False)

    # 잔여석 있고 알림 모드 → 바로 알림
    if train.seat_available() and mode == "notify":
        flash(f"이미 잔여석이 있습니다! {train.dep_station_name}→{train.arr_station_name} "
              f"{train.dep_time[:2]}:{train.dep_time[2:4]} 출발", "success")
        return redirect(url_for("search"))

    # 잔여석 없음 → 백그라운드 모니터 등록
    card_info = _get_card_info_from_session()
    monitor_id = _start_monitor(
        srt_id=session["srt_id"], srt_pw=session["srt_pw"],
        dep=dep, arr=arr, date=date, time_str=time_str,
        train_dep_time=train.dep_time,
        adult_count=adult_count, seat_type=seat_type,
        mode=mode, interval=interval, card_info=card_info,
        train_name=train.train_name,
        dep_time_display=train.dep_time[:2] + ":" + train.dep_time[2:4],
    )
    return redirect(url_for("monitors"))


# ──────────────────────────────────────────────
# 모니터 관리 페이지
# ──────────────────────────────────────────────

@app.route("/monitors")
def monitors():
    if not session.get("srt_id"):
        return redirect(url_for("index"))
    srt_id = session["srt_id"]
    with _monitor_lock:
        user_monitors = [
            {"id": mid, **m}
            for mid, m in _monitors.items()
            if m.get("srt_id") == srt_id
        ]
    # 최신순 정렬
    user_monitors.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return render_template("monitors.html", monitors=user_monitors)


@app.route("/api/monitor/<monitor_id>/status")
def monitor_status(monitor_id: str):
    if not session.get("srt_id"):
        return jsonify({"status": "error", "message": "로그인 필요"})
    with _monitor_lock:
        m = _monitors.get(monitor_id)
    if not m or m.get("srt_id") != session["srt_id"]:
        return jsonify({"status": "error", "message": "모니터를 찾을 수 없습니다."})
    return jsonify({k: v for k, v in m.items() if k != "srt_id"})


@app.route("/api/monitor/<monitor_id>/cancel", methods=["POST"])
def monitor_cancel(monitor_id: str):
    if not session.get("srt_id"):
        return jsonify({"ok": False})
    with _monitor_lock:
        m = _monitors.get(monitor_id)
        if m and m.get("srt_id") == session["srt_id"]:
            m["status"] = "cancelled"
            m["message"] = "사용자가 취소했습니다."
    return jsonify({"ok": True})


@app.route("/api/monitor/<monitor_id>/delete", methods=["POST"])
def monitor_delete(monitor_id: str):
    if not session.get("srt_id"):
        return jsonify({"ok": False})
    with _monitor_lock:
        m = _monitors.get(monitor_id)
        if m and m.get("srt_id") == session["srt_id"]:
            del _monitors[monitor_id]
    return jsonify({"ok": True})


# ──────────────────────────────────────────────
# 카드 설정
# ──────────────────────────────────────────────

@app.route("/card", methods=["GET", "POST"])
def card_settings():
    if not session.get("srt_id"):
        return redirect(url_for("index"))

    if request.method == "POST":
        card_number    = request.form.get("card_number", "").replace("-", "").replace(" ", "")
        card_password  = request.form.get("card_password", "")
        card_validation = request.form.get("card_validation", "")
        card_expire    = request.form.get("card_expire", "").replace("/", "")
        installment    = request.form.get("installment", "0")

        if len(card_number) != 16 or not card_number.isdigit():
            return render_template("card.html", error="카드번호는 16자리 숫자여야 합니다.",
                                   saved=session.get("card_saved"))
        if len(card_password) != 2:
            return render_template("card.html", error="카드 비밀번호는 2자리여야 합니다.",
                                   saved=session.get("card_saved"))
        if len(card_expire) != 4 or not card_expire.isdigit():
            return render_template("card.html", error="유효기간은 YYMM 4자리 숫자여야 합니다.",
                                   saved=session.get("card_saved"))

        session["card_number"]     = card_number
        session["card_password"]   = card_password
        session["card_validation"] = card_validation
        session["card_expire"]     = card_expire
        session["card_installment"] = installment
        session["card_saved"]      = True
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
# 결제
# ──────────────────────────────────────────────

@app.route("/pay", methods=["GET", "POST"])
def pay():
    if not session.get("srt_id"):
        return redirect(url_for("index"))

    if request.method == "GET":
        res_id = request.args.get("res_id") or session.get("reservation_id")
        return render_template("pay.html", reservation_id=res_id)

    srt = get_srt()
    if not srt:
        return render_template("login.html", error="세션이 만료됐습니다. 다시 로그인해주세요.")

    res_number     = request.form.get("reservation_id")
    card_number    = request.form.get("card_number", "").replace("-", "").replace(" ", "")
    card_password  = request.form.get("card_password")
    card_validation = request.form.get("card_validation")
    card_expire    = request.form.get("card_expire", "").replace("/", "")
    installment    = int(request.form.get("installment", 0))

    try:
        reservations = srt_bot.get_reservations(srt)
        target = next((r for r in reservations if str(r.reservation_number) == str(res_number)), None)
        if not target:
            return render_template("pay.html", reservation_id=res_number,
                                   error="예매 내역을 찾을 수 없습니다.")
        srt_bot.pay_reservation(srt, target, card_number, card_password,
                                card_validation, card_expire, installment)
        return render_template("pay.html", reservation_id=res_number, success=True)
    except Exception as e:
        return render_template("pay.html", reservation_id=res_number, error=str(e))


# ──────────────────────────────────────────────
# 예매 내역 / 취소
# ──────────────────────────────────────────────

@app.route("/reservations")
def reservations():
    if not session.get("srt_id"):
        return redirect(url_for("index"))
    srt = get_srt()
    items = []
    error = None
    if srt:
        try:
            items = srt_bot.get_reservations(srt)
        except Exception as e:
            error = str(e)
    return render_template("reservations.html", reservations=items, error=error)


@app.route("/cancel/<res_number>", methods=["POST"])
def cancel(res_number):
    if not session.get("srt_id"):
        return redirect(url_for("index"))
    srt = get_srt()
    if not srt:
        return redirect(url_for("reservations"))
    try:
        reservations_list = srt_bot.get_reservations(srt)
        target = next((r for r in reservations_list
                       if str(r.reservation_number) == res_number), None)
        if not target:
            flash(f"예약번호 {res_number}을(를) 찾을 수 없습니다.", "error")
        else:
            srt_bot.cancel_reservation(srt, target)
            flash("예매가 취소됐습니다.", "success")
    except Exception as e:
        flash(f"취소 실패: {e}", "error")
    return redirect(url_for("reservations"))


# ──────────────────────────────────────────────
# 예매 결과
# ──────────────────────────────────────────────

@app.route("/reserve/result")
def reserve_result():
    return render_template("reserve_result.html", success=False,
                           error="잘못된 접근입니다.")


if __name__ == "__main__":
    app.run(debug=False, port=5001)
