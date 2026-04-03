"""SRT 자동 예매 웹 앱"""

import os
import uuid
import time
import threading
from typing import Optional
from flask import Flask, render_template, request, session, redirect, url_for, jsonify
from flask_session import Session
import srt_bot

app = Flask(__name__)
app.secret_key = os.urandom(24)
app.config["SESSION_TYPE"] = "filesystem"
app.config["SESSION_FILE_DIR"] = "/tmp/srt_sessions"
Session(app)


# ──────────────────────────────────────────────
# SRT 세션 관리
# ──────────────────────────────────────────────

_srt_instances: dict = {}


def get_srt() -> Optional[srt_bot.SRT]:
    sid = session.get("sid")
    return _srt_instances.get(sid)


# ──────────────────────────────────────────────
# 자동 예매 대기 작업 관리
# ──────────────────────────────────────────────

# task_id → {"status": "waiting"|"done"|"error", "message": str, "reservation_number": str}
_tasks: dict = {}
_task_lock = threading.Lock()


def _watch_and_reserve(task_id: str, srt, dep: str, arr: str, date: str,
                       time_str: str, train_dep_time: str,
                       adult_count: int, seat_type: str, interval: int):
    """
    백그라운드 스레드: 잔여석이 생길 때까지 폴링 후 자동 예매.
    train_dep_time: 사용자가 선택한 열차의 출발 시각 (HHMMSS)
    """
    attempt = 0
    while True:
        with _task_lock:
            task = _tasks.get(task_id)
            if task is None or task["status"] == "cancelled":
                return

        attempt += 1
        try:
            trains = srt_bot.search_trains(srt, dep, arr, date, time_str, available_only=False)

            # 사용자가 선택한 열차를 출발 시각으로 찾기
            target = next((t for t in trains if t.dep_time == train_dep_time), None)

            if target is None:
                with _task_lock:
                    _tasks[task_id]["message"] = f"[{attempt}회 시도] 해당 열차를 찾을 수 없습니다. 재시도 중..."
            elif target.seat_available():
                # 잔여석 생김 → 예매
                reservation = srt_bot.make_reservation(srt, target, adult_count, seat_type)
                with _task_lock:
                    _tasks[task_id]["status"] = "done"
                    _tasks[task_id]["reservation_number"] = str(reservation.reservation_number)
                    _tasks[task_id]["message"] = f"예매 완료! 예약번호: {reservation.reservation_number}"
                return
            else:
                general = "일반실 매진" if not target.general_seat_available else "일반실 가능"
                special = "특실 매진" if not target.special_seat_available else "특실 가능"
                with _task_lock:
                    _tasks[task_id]["attempt"] = attempt
                    _tasks[task_id]["message"] = (
                        f"[{attempt}회 시도] 잔여석 없음 ({general} / {special}) — {interval}초 후 재시도"
                    )

        except Exception as e:
            with _task_lock:
                _tasks[task_id]["attempt"] = attempt
                _tasks[task_id]["message"] = f"[{attempt}회 시도] 오류: {e} — 재시도 중..."

        time.sleep(interval)


# ──────────────────────────────────────────────
# 라우트
# ──────────────────────────────────────────────

@app.route("/")
def index():
    if get_srt():
        return redirect(url_for("search"))
    return render_template("login.html")


@app.route("/login", methods=["POST"])
def login():
    srt_id = request.form.get("srt_id", "").strip()
    srt_pw = request.form.get("srt_pw", "").strip()
    if not srt_id or not srt_pw:
        return render_template("login.html", error="아이디와 비밀번호를 입력해주세요.")
    try:
        srt = srt_bot.login(srt_id, srt_pw)
        sid = str(uuid.uuid4())
        session["sid"] = sid
        _srt_instances[sid] = srt
        return redirect(url_for("search"))
    except Exception as e:
        return render_template("login.html", error=f"로그인 실패: {e}")


@app.route("/logout")
def logout():
    sid = session.pop("sid", None)
    if sid:
        _srt_instances.pop(sid, None)
    return redirect(url_for("index"))


@app.route("/search")
def search():
    if not get_srt():
        return redirect(url_for("index"))
    return render_template("search.html", stations=srt_bot.STATIONS)


@app.route("/search", methods=["POST"])
def search_post():
    srt = get_srt()
    if not srt:
        return redirect(url_for("index"))

    dep = request.form.get("dep")
    arr = request.form.get("arr")
    date = request.form.get("date", "").replace("-", "")
    time_str = request.form.get("time", "00:00").replace(":", "") + "00"

    try:
        trains = srt_bot.search_trains(srt, dep, arr, date, time_str)
    except Exception as e:
        return render_template("search.html", stations=srt_bot.STATIONS, error=str(e))

    session["search_params"] = {"dep": dep, "arr": arr, "date": date, "time": time_str}
    return render_template("search.html", stations=srt_bot.STATIONS, trains=trains,
                           dep=dep, arr=arr, date=date, time=time_str[:4])


@app.route("/reserve", methods=["POST"])
def reserve():
    srt = get_srt()
    if not srt:
        return redirect(url_for("index"))

    params = session.get("search_params", {})
    dep = params.get("dep")
    arr = params.get("arr")
    date = params.get("date")
    time_str = params.get("time")
    train_idx = int(request.form.get("train_idx", 0))
    seat_type = request.form.get("seat_type", "GENERAL_FIRST")
    adult_count = int(request.form.get("adult_count", 1))
    interval = int(request.form.get("interval", 3))

    try:
        trains = srt_bot.search_trains(srt, dep, arr, date, time_str)
        train = trains[train_idx]
    except Exception as e:
        return render_template("reserve_result.html", error=str(e), success=False)

    # 잔여석 있으면 즉시 예매
    if train.seat_available():
        try:
            reservation = srt_bot.make_reservation(srt, train, adult_count, seat_type)
            session["reservation_id"] = str(reservation.reservation_number)
            return render_template("reserve_result.html", reservation=reservation, success=True)
        except Exception as e:
            return render_template("reserve_result.html", error=str(e), success=False)

    # 잔여석 없음 → 백그라운드 대기 작업 시작
    task_id = str(uuid.uuid4())
    with _task_lock:
        _tasks[task_id] = {
            "status": "waiting",
            "attempt": 0,
            "message": "잔여석 모니터링을 시작합니다...",
            "reservation_number": None,
        }

    t = threading.Thread(
        target=_watch_and_reserve,
        args=(task_id, srt, dep, arr, date, time_str,
              train.dep_time, adult_count, seat_type, interval),
        daemon=True,
    )
    t.start()

    return redirect(url_for("watch", task_id=task_id,
                            dep=dep, arr=arr,
                            dep_time=train.dep_time[:2] + ":" + train.dep_time[2:4],
                            train_name=train.train_name))


@app.route("/watch/<task_id>")
def watch(task_id: str):
    if not get_srt():
        return redirect(url_for("index"))
    dep = request.args.get("dep", "")
    arr = request.args.get("arr", "")
    dep_time = request.args.get("dep_time", "")
    train_name = request.args.get("train_name", "SRT")
    return render_template("watch.html", task_id=task_id,
                           dep=dep, arr=arr, dep_time=dep_time, train_name=train_name)


@app.route("/watch/<task_id>/status")
def watch_status(task_id: str):
    """프론트엔드 폴링용 JSON 엔드포인트"""
    with _task_lock:
        task = _tasks.get(task_id)
    if task is None:
        return jsonify({"status": "error", "message": "작업을 찾을 수 없습니다."})
    return jsonify(task)


@app.route("/watch/<task_id>/cancel", methods=["POST"])
def watch_cancel(task_id: str):
    with _task_lock:
        if task_id in _tasks:
            _tasks[task_id]["status"] = "cancelled"
    return redirect(url_for("search"))


@app.route("/pay", methods=["GET", "POST"])
def pay():
    srt = get_srt()
    if not srt:
        return redirect(url_for("index"))

    if request.method == "GET":
        res_id = request.args.get("res_id") or session.get("reservation_id")
        return render_template("pay.html", reservation_id=res_id)

    res_number = request.form.get("reservation_id")
    card_number = request.form.get("card_number", "").replace("-", "").replace(" ", "")
    card_password = request.form.get("card_password")
    card_validation = request.form.get("card_validation")
    card_expire = request.form.get("card_expire", "").replace("/", "")
    installment = int(request.form.get("installment", 0))

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


@app.route("/reservations")
def reservations():
    srt = get_srt()
    if not srt:
        return redirect(url_for("index"))
    try:
        items = srt_bot.get_reservations(srt)
    except Exception as e:
        items = []
    return render_template("reservations.html", reservations=items)


@app.route("/cancel/<res_number>", methods=["POST"])
def cancel(res_number):
    srt = get_srt()
    if not srt:
        return redirect(url_for("index"))
    try:
        reservations_list = srt_bot.get_reservations(srt)
        target = next((r for r in reservations_list if str(r.reservation_number) == res_number), None)
        if target:
            srt_bot.cancel_reservation(srt, target)
    except Exception:
        pass
    return redirect(url_for("reservations"))


if __name__ == "__main__":
    os.makedirs("/tmp/srt_sessions", exist_ok=True)
    app.run(debug=False, port=5001)
