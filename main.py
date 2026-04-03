#!/usr/bin/env python3
"""SRT 자동 예매 앱

사용법:
  대화형 모드: python main.py
  자동 모드:   python main.py --auto  (.env 파일의 설정으로 자동 실행)
"""

import os
import sys
import argparse
from datetime import datetime, timedelta

from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.prompt import Prompt, Confirm
from rich import box
import questionary

import srt_bot

load_dotenv()
console = Console()


# ──────────────────────────────────────────────
# 유틸
# ──────────────────────────────────────────────

def print_banner():
    console.print(Panel.fit(
        "[bold cyan]🚄 SRT 자동 예매 앱[/bold cyan]\n[dim]Super Rapid Train Reservation Bot[/dim]",
        border_style="cyan"
    ))


def print_trains_table(trains):
    table = Table(title="검색된 열차 목록", box=box.ROUNDED, show_lines=True)
    table.add_column("번호", style="bold yellow", width=4)
    table.add_column("열차번호", style="cyan")
    table.add_column("출발", style="green")
    table.add_column("도착", style="red")
    table.add_column("출발시각", style="bold green")
    table.add_column("도착시각", style="bold red")
    table.add_column("소요시간")
    table.add_column("일반실", style="magenta")
    table.add_column("특실", style="yellow")

    for i, t in enumerate(trains):
        general = "✅ 예매가능" if t.general_seat_available else "❌ 매진"
        special = "✅ 예매가능" if t.special_seat_available else "❌ 매진"
        table.add_row(
            str(i + 1),
            t.train_name,
            t.dep_station_name,
            t.arr_station_name,
            t.dep_time[:2] + ":" + t.dep_time[2:4],
            t.arr_time[:2] + ":" + t.arr_time[2:4],
            t.running_time,
            general,
            special,
        )
    console.print(table)


# ──────────────────────────────────────────────
# 대화형 모드
# ──────────────────────────────────────────────

def interactive_mode():
    print_banner()

    # 로그인
    console.print("\n[bold]─── 로그인 ───[/bold]")
    srt_id = Prompt.ask("[cyan]SRT 아이디[/cyan] (전화번호/이메일/멤버십번호)")
    srt_pw = Prompt.ask("[cyan]SRT 비밀번호[/cyan]", password=True)

    console.print("[dim]로그인 중...[/dim]")
    try:
        srt = srt_bot.login(srt_id, srt_pw)
        console.print("[bold green]✅ 로그인 성공![/bold green]")
    except Exception as e:
        console.print(f"[bold red]❌ 로그인 실패: {e}[/bold red]")
        sys.exit(1)

    # 메뉴 선택
    while True:
        console.print()
        action = questionary.select(
            "무엇을 하시겠습니까?",
            choices=[
                "열차 검색 및 예매",
                "내 예매 목록 보기",
                "예매 취소",
                "종료",
            ]
        ).ask()

        if action == "열차 검색 및 예매":
            search_and_reserve(srt)
        elif action == "내 예매 목록 보기":
            show_reservations(srt)
        elif action == "예매 취소":
            cancel_reservation_interactive(srt)
        elif action == "종료":
            console.print("[dim]앱을 종료합니다.[/dim]")
            break


def search_and_reserve(srt):
    console.print("\n[bold]─── 열차 검색 ───[/bold]")

    dep = questionary.select("출발역을 선택하세요:", choices=srt_bot.STATIONS).ask()
    arr = questionary.select("도착역을 선택하세요:", choices=srt_bot.STATIONS).ask()

    # 날짜 입력
    default_date = datetime.now().strftime("%Y%m%d")
    date_str = Prompt.ask("[cyan]날짜[/cyan] (yyyyMMdd)", default=default_date)

    # 시간 입력
    time_str = Prompt.ask("[cyan]출발 시각 이후[/cyan] (HHmm)", default="0800")
    if len(time_str) == 4:
        time_str = time_str + "00"

    # 좌석 타입
    seat_choice = questionary.select(
        "좌석 종류를 선택하세요:",
        choices=[
            "일반실 우선 (GENERAL_FIRST)",
            "일반실만 (GENERAL_ONLY)",
            "특실 우선 (SPECIAL_FIRST)",
            "특실만 (SPECIAL_ONLY)",
        ]
    ).ask()
    seat_type_key = seat_choice.split("(")[1].rstrip(")")

    # 인원
    adult_count = int(Prompt.ask("[cyan]어른 인원수[/cyan]", default="1"))

    console.print("[dim]열차를 검색 중...[/dim]")
    try:
        trains = srt_bot.search_trains(srt, dep, arr, date_str, time_str)
    except Exception as e:
        console.print(f"[bold red]❌ 검색 실패: {e}[/bold red]")
        return

    if not trains:
        console.print("[yellow]⚠ 예매 가능한 열차가 없습니다.[/yellow]")
        return

    print_trains_table(trains)

    # 열차 선택
    train_num = int(Prompt.ask(
        f"[cyan]예매할 열차 번호[/cyan] (1~{len(trains)})"
    ))
    if not 1 <= train_num <= len(trains):
        console.print("[red]잘못된 번호입니다.[/red]")
        return
    selected_train = trains[train_num - 1]

    # 예매
    if not Confirm.ask(
        f"[bold]{selected_train.dep_station_name} → {selected_train.arr_station_name} "
        f"{selected_train.dep_time[:2]}:{selected_train.dep_time[2:4]} 열차를 예매하시겠습니까?[/bold]"
    ):
        return

    console.print("[dim]예매 중...[/dim]")
    try:
        reservation = srt_bot.make_reservation(srt, selected_train, adult_count, seat_type_key)
        console.print(f"[bold green]✅ 예매 성공![/bold green]")
        console.print(f"  예약번호: [bold]{reservation.reservation_number}[/bold]")
    except Exception as e:
        console.print(f"[bold red]❌ 예매 실패: {e}[/bold red]")
        return

    # 결제 여부
    if Confirm.ask("결제를 진행하시겠습니까?"):
        pay_interactive(srt, reservation)


def pay_interactive(srt, reservation):
    console.print("\n[bold]─── 카드 결제 ───[/bold]")
    console.print("[yellow]⚠ 카드 정보는 저장되지 않으며 결제 후 즉시 폐기됩니다.[/yellow]")

    card_number = Prompt.ask("[cyan]카드번호[/cyan] (하이픈 없이 16자리)", password=True)
    card_password = Prompt.ask("[cyan]카드 비밀번호[/cyan] (앞 2자리)", password=True)
    card_validation = Prompt.ask("[cyan]생년월일/사업자번호[/cyan] (6자리 또는 10자리)", password=True)
    card_expire = Prompt.ask("[cyan]카드 유효기간[/cyan] (YYMM, 예: 2612)")
    installment = int(Prompt.ask("[cyan]할부 개월[/cyan] (0=일시불)", default="0"))

    console.print("[dim]결제 중...[/dim]")
    try:
        srt_bot.pay_reservation(
            srt, reservation,
            card_number=card_number,
            card_password=card_password,
            card_validation_number=card_validation,
            card_expire_date=card_expire,
            installment=installment,
        )
        console.print("[bold green]✅ 결제 완료![/bold green]")
    except Exception as e:
        console.print(f"[bold red]❌ 결제 실패: {e}[/bold red]")


def show_reservations(srt):
    console.print("\n[bold]─── 예매 목록 ───[/bold]")
    try:
        reservations = srt_bot.get_reservations(srt)
    except Exception as e:
        console.print(f"[bold red]❌ 조회 실패: {e}[/bold red]")
        return

    if not reservations:
        console.print("[yellow]예매 내역이 없습니다.[/yellow]")
        return

    table = Table(box=box.ROUNDED, show_lines=True)
    table.add_column("번호", style="yellow", width=4)
    table.add_column("예약번호", style="cyan")
    table.add_column("노선", style="green")
    table.add_column("날짜/시각", style="bold")
    table.add_column("상태")

    for i, r in enumerate(reservations):
        table.add_row(
            str(i + 1),
            str(r.reservation_number),
            f"{r.dep_station_name} → {r.arr_station_name}",
            str(r.train_date) + " " + str(r.dep_time),
            "결제완료" if r.is_paid else "미결제",
        )
    console.print(table)


def cancel_reservation_interactive(srt):
    console.print("\n[bold]─── 예매 취소 ───[/bold]")
    try:
        reservations = srt_bot.get_reservations(srt)
    except Exception as e:
        console.print(f"[bold red]❌ 조회 실패: {e}[/bold red]")
        return

    if not reservations:
        console.print("[yellow]취소할 예매 내역이 없습니다.[/yellow]")
        return

    show_reservations(srt)
    num = int(Prompt.ask(f"취소할 예매 번호 (1~{len(reservations)})"))
    if not 1 <= num <= len(reservations):
        console.print("[red]잘못된 번호입니다.[/red]")
        return

    target = reservations[num - 1]
    if not Confirm.ask(f"[bold red]정말 예매를 취소하시겠습니까?[/bold red]"):
        return

    try:
        srt_bot.cancel_reservation(srt, target)
        console.print("[bold green]✅ 취소 완료![/bold green]")
    except Exception as e:
        console.print(f"[bold red]❌ 취소 실패: {e}[/bold red]")


# ──────────────────────────────────────────────
# 자동 모드 (.env 설정 기반)
# ──────────────────────────────────────────────

def auto_mode():
    """환경변수(.env) 설정으로 자동 예매 실행"""
    print_banner()
    console.print("[bold yellow]⚙ 자동 모드 실행[/bold yellow]")

    srt_id = os.environ["SRT_ID"]
    srt_pw = os.environ["SRT_PW"]
    dep = os.environ.get("DEP_STATION", "수서")
    arr = os.environ.get("ARR_STATION", "부산")
    date = os.environ.get("TRAVEL_DATE", datetime.now().strftime("%Y%m%d"))
    time = os.environ.get("TRAVEL_TIME", "080000")
    seat_type = os.environ.get("SEAT_TYPE", "GENERAL_FIRST")
    adult_count = int(os.environ.get("ADULT_COUNT", "1"))
    auto_pay = os.environ.get("AUTO_PAY", "false").lower() == "true"

    console.print(f"  노선: [bold]{dep} → {arr}[/bold]")
    console.print(f"  날짜: [bold]{date}[/bold]  시각: [bold]{time}[/bold]")
    console.print(f"  좌석: [bold]{seat_type}[/bold]  인원: [bold]{adult_count}명[/bold]")

    # 로그인
    console.print("\n[dim]로그인 중...[/dim]")
    try:
        srt = srt_bot.login(srt_id, srt_pw)
        console.print("[green]✅ 로그인 성공[/green]")
    except Exception as e:
        console.print(f"[bold red]❌ 로그인 실패: {e}[/bold red]")
        sys.exit(1)

    # 검색
    console.print("[dim]열차 검색 중...[/dim]")
    try:
        trains = srt_bot.search_trains(srt, dep, arr, date, time)
    except Exception as e:
        console.print(f"[bold red]❌ 검색 실패: {e}[/bold red]")
        sys.exit(1)

    if not trains:
        console.print("[yellow]⚠ 예매 가능한 열차가 없습니다.[/yellow]")
        sys.exit(0)

    print_trains_table(trains)

    # 첫 번째 열차 자동 예매
    selected = trains[0]
    console.print(
        f"\n[bold]첫 번째 열차 자동 선택: "
        f"{selected.dep_time[:2]}:{selected.dep_time[2:4]} 출발[/bold]"
    )

    try:
        reservation = srt_bot.make_reservation(srt, selected, adult_count, seat_type)
        console.print(f"[bold green]✅ 예매 성공! 예약번호: {reservation.reservation_number}[/bold green]")
    except Exception as e:
        console.print(f"[bold red]❌ 예매 실패: {e}[/bold red]")
        sys.exit(1)

    # 자동 결제
    if auto_pay:
        card_number = os.environ["CARD_NUMBER"]
        card_password = os.environ["CARD_PASSWORD"]
        card_validation = os.environ["CARD_VALIDATION_NUMBER"]
        card_expire = os.environ["CARD_EXPIRE_DATE"]
        installment = int(os.environ.get("CARD_INSTALLMENT", "0"))

        console.print("[dim]카드 결제 중...[/dim]")
        try:
            srt_bot.pay_reservation(
                srt, reservation,
                card_number=card_number,
                card_password=card_password,
                card_validation_number=card_validation,
                card_expire_date=card_expire,
                installment=installment,
            )
            console.print("[bold green]✅ 결제 완료![/bold green]")
        except Exception as e:
            console.print(f"[bold red]❌ 결제 실패: {e}[/bold red]")
            console.print(f"[yellow]예매는 완료되었으나 결제가 실패했습니다. SRT 앱에서 직접 결제해 주세요.[/yellow]")
            sys.exit(1)
    else:
        console.print("[yellow]💡 AUTO_PAY=false 설정입니다. SRT 앱에서 직접 결제해 주세요.[/yellow]")


# ──────────────────────────────────────────────
# 진입점
# ──────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="SRT 자동 예매 앱")
    parser.add_argument(
        "--auto",
        action="store_true",
        help=".env 파일 설정으로 자동 예매 실행",
    )
    args = parser.parse_args()

    if args.auto:
        auto_mode()
    else:
        interactive_mode()


if __name__ == "__main__":
    main()
