"""Default Greek public-holiday calendar.

The list below is a *default seed* that the company administrator can edit
per year in Settings → Holidays. Which of these days carry the statutory
holiday premium for a given employment relationship must be confirmed by the
company's accountant; see docs/LIMITATIONS.md.
"""
from __future__ import annotations

from datetime import date, timedelta


def orthodox_easter(year: int) -> date:
    """Orthodox Easter (Gregorian date) via Meeus' Julian algorithm.

    Valid for 1900-2099 (Julian→Gregorian offset of 13 days).
    """
    if not 1900 <= year <= 2099:
        raise ValueError("orthodox_easter supports years 1900-2099")
    a, b, c = year % 4, year % 7, year % 19
    d = (19 * c + 15) % 30
    e = (2 * a + 4 * b - d + 34) % 7
    month = (d + e + 114) // 31
    day = ((d + e + 114) % 31) + 1
    return date(year, month, day) + timedelta(days=13)


def greek_holidays(year: int) -> list[tuple[date, str]]:
    easter = orthodox_easter(year)
    return sorted(
        [
            (date(year, 1, 1), "Πρωτοχρονιά"),
            (date(year, 1, 6), "Θεοφάνεια"),
            (easter - timedelta(days=48), "Καθαρά Δευτέρα"),
            (date(year, 3, 25), "Εθνική Επέτειος 25ης Μαρτίου"),
            (easter - timedelta(days=2), "Μεγάλη Παρασκευή"),
            (easter, "Κυριακή του Πάσχα"),
            (easter + timedelta(days=1), "Δευτέρα του Πάσχα"),
            (date(year, 5, 1), "Πρωτομαγιά"),
            (easter + timedelta(days=50), "Αγίου Πνεύματος"),
            (date(year, 8, 15), "Κοίμηση της Θεοτόκου"),
            (date(year, 10, 28), "Επέτειος του Όχι"),
            (date(year, 12, 25), "Χριστούγεννα"),
            (date(year, 12, 26), "Σύναξη της Θεοτόκου"),
        ]
    )
