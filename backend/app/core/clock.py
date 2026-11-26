"""This server's date and time in Spanish words ("jueves 26 de noviembre de 2026", "10:59"): the clock answers
"¿qué día es hoy?" and the model gets the same words (it miscopied "2026-11-26" as "20 de noviembre")."""
import datetime

DAYS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
MONTHS = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
          "noviembre", "diciembre")


def today_text(now=None):
    now = now or datetime.datetime.now()
    return f"{DAYS[now.weekday()]} {now.day} de {MONTHS[now.month - 1]} de {now.year}"


def time_text(now=None):
    now = now or datetime.datetime.now()
    return f"{now:%H:%M}"
