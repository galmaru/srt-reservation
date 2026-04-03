"""SRT 자동 예매 웹 앱 (Vercel 서버리스 호환)"""

import os
import uuid
from flask import Flask, render_template, request, session, redirect, url_for, jsonify
import srt_bot

app = Flask(__name__)
# 환경변수 SECRET_KEY 필수 (Vercel 대시보드에서 설정)
app.secret_key = os.environ.get("SECRET_KEY", os.urandom(24))
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_HTTPONLY"] = True


# ──────────────────────────────────────────────
# 헬퍼
# ──────────────────────────────────────────────

def get_srt():
    """세션 쿠키의 자격증명으로 SRT 연결 생성 (매 요청마다 재로그인)"""
    srt_id = session.get("srt_id")
    srt_pw = session.get("srt_pw")
    if not srt_id or not srt_pw:
        return None
    try:
        return srt_bot.login(srt_id, srt_pw)
    except Exception:
        return None


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
        srt_bot.login(srt_id, srt_pw)  # 로그인 검증
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

    params = session.get("search_params", {})
    dep = params.get("dep")
    arr = params.get("arr")
    date = params.get("date")
    time_str = params.get("time")
    train_idx = int(request.form.get("train_idx", 0))
    seat_type = request.form.get("seat_type", "GENERAL_FIRST")
    adult_count = int(request.form.get("adult_count", 1))
    interval = int(request.form.get("interval", 3))

    srt = get_srt()
    if not srt:
        return render_template("login.html", error="세션이 만료됐습니다. 다시 로그인해주세요.")

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

    # 잔여석 없음 → 클라이언트 사이드 폴링 대기 화면
    return render_template(
        "watch.html",
        dep=dep, arr=arr,
        dep_time=train.dep_time[:2] + ":" + train.dep_time[2:4],
        train_name=train.train_name,
        train_dep_time_raw=train.dep_time,
        date=date,
        time_str=time_str,
        seat_type=seat_type,
        adult_count=adult_count,
        interval=interval,
    )


@app.route("/api/watch-poll", methods=["POST"])
def watch_poll():
    """
    클라이언트 폴링 엔드포인트.
    브라우저가 N초마다 호출 → 잔여석 확인 및 예매 시도.
    """
    if not session.get("srt_id"):
        return jsonify({"status": "error", "message": "로그인이 필요합니다."})

    data = request.get_json()
    dep = data.get("dep")
    arr = data.get("arr")
    date = data.get("date")
    time_str = data.get("time_str")
    train_dep_time = data.get("train_dep_time")   # HHMMSS
    seat_type = data.get("seat_type", "GENERAL_FIRST")
    adult_count = int(data.get("adult_count", 1))

    srt = get_srt()
    if not srt:
        return jsonify({"status": "error", "message": "로그인 실패. 다시 로그인해주세요."})

    try:
        trains = srt_bot.search_trains(srt, dep, arr, date, time_str, available_only=False)
        target = next((t for t in trains if t.dep_time == train_dep_time), None)

        if target is None:
            return jsonify({"status": "waiting", "message": "해당 열차를 찾을 수 없습니다."})

        if target.seat_available():
            reservation = srt_bot.make_reservation(srt, target, adult_count, seat_type)
            session["reservation_id"] = str(reservation.reservation_number)
            return jsonify({
                "status": "done",
                "reservation_number": str(reservation.reservation_number),
            })

        general = "일반실 가능" if target.general_seat_available else "일반실 매진"
        special = "특실 가능" if target.special_seat_available else "특실 매진"
        return jsonify({
            "status": "waiting",
            "message": f"잔여석 없음 ({general} / {special})",
        })

    except Exception as e:
        return jsonify({"status": "waiting", "message": f"오류: {e} — 재시도 중..."})


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
    if not session.get("srt_id"):
        return redirect(url_for("index"))
    srt = get_srt()
    items = []
    if srt:
        try:
            items = srt_bot.get_reservations(srt)
        except Exception:
            pass
    return render_template("reservations.html", reservations=items)


@app.route("/cancel/<res_number>", methods=["POST"])
def cancel(res_number):
    if not session.get("srt_id"):
        return redirect(url_for("index"))
    srt = get_srt()
    if srt:
        try:
            reservations_list = srt_bot.get_reservations(srt)
            target = next((r for r in reservations_list if str(r.reservation_number) == res_number), None)
            if target:
                srt_bot.cancel_reservation(srt, target)
        except Exception:
            pass
    return redirect(url_for("reservations"))


if __name__ == "__main__":
    app.run(debug=True, port=5001)
