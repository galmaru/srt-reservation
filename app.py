"""SRT 자동 예매 웹 앱"""

import os
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
# 라우트
# ──────────────────────────────────────────────

@app.route("/")
def index():
    srt = get_srt()
    if srt:
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
        import uuid
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
    srt = get_srt()
    if not srt:
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
    time = request.form.get("time", "00:00").replace(":", "") + "00"

    try:
        trains = srt_bot.search_trains(srt, dep, arr, date, time)
    except Exception as e:
        return render_template("search.html", stations=srt_bot.STATIONS, error=str(e))

    session["search_params"] = {"dep": dep, "arr": arr, "date": date, "time": time}
    return render_template("search.html", stations=srt_bot.STATIONS, trains=trains,
                           dep=dep, arr=arr, date=date, time=time[:4])


@app.route("/reserve", methods=["POST"])
def reserve():
    srt = get_srt()
    if not srt:
        return redirect(url_for("index"))

    params = session.get("search_params", {})
    dep = params.get("dep")
    arr = params.get("arr")
    date = params.get("date")
    time = params.get("time")
    train_idx = int(request.form.get("train_idx", 0))
    seat_type = request.form.get("seat_type", "GENERAL_FIRST")
    adult_count = int(request.form.get("adult_count", 1))

    try:
        trains = srt_bot.search_trains(srt, dep, arr, date, time)
        train = trains[train_idx]
        reservation = srt_bot.make_reservation(srt, train, adult_count, seat_type)
        session["reservation_id"] = reservation.reservation_number
        return render_template("reserve_result.html", reservation=reservation, success=True)
    except Exception as e:
        return render_template("reserve_result.html", error=str(e), success=False)


@app.route("/pay", methods=["GET", "POST"])
def pay():
    srt = get_srt()
    if not srt:
        return redirect(url_for("index"))

    if request.method == "GET":
        res_id = session.get("reservation_id")
        return render_template("pay.html", reservation_id=res_id)

    # POST: 결제 처리
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
        reservations = srt_bot.get_reservations(srt)
        target = next((r for r in reservations if str(r.reservation_number) == res_number), None)
        if target:
            srt_bot.cancel_reservation(srt, target)
        return redirect(url_for("reservations"))
    except Exception as e:
        return redirect(url_for("reservations"))


if __name__ == "__main__":
    app.run(debug=True, port=5001)
