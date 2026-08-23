"""Seasonal Calendar — dates and themes for seasonal content opportunities.

This is a simple, extensible structure that maps dates/months to seasonal
themes that can inspire Kids content. It is NOT a complex editorial
calendar — just a lookup of "what's relevant now" for the ideation agent.

Structure:
    SeasonalEntry → LLM expansion → KidsIdeas with source="seasonal"

The calendar is intentionally minimal for the MVP. New entries can be
added without code changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional


@dataclass
class SeasonalEntry:
    """A seasonal content opportunity."""
    name: str  # e.g. "Dia das Crianças"
    date: str  # ISO format: "MM-DD" (recurring yearly) or "YYYY-MM-DD" (specific)
    description: str = ""
    category: str = "curiosity"  # which topic category it relates to
    age_ranges: list[str] = field(default_factory=lambda: ["all"])
    lead_days: int = 7  # how many days before the date to start generating ideas


# ── The calendar ─────────────────────────────────────────────────────────────

_SEASONAL_CALENDAR: list[SeasonalEntry] = [
    SeasonalEntry(
        name="Dia das Crianças",
        date="10-12",
        description="Dia internacional das crianças — temas sobre infância, brincadeiras, direitos",
        category="curiosity",
        age_ranges=["3-6", "7-10", "all"],
        lead_days=14,
    ),
    SeasonalEntry(
        name="Natal",
        date="12-25",
        description="Natal — temas sobre inverno, presentes, renas, neve (cultural, não religioso)",
        category="curiosity",
        age_ranges=["3-6", "7-10", "all"],
        lead_days=21,
    ),
    SeasonalEntry(
        name="Ano Novo",
        date="01-01",
        description="Ano novo — temas sobre tempo, calendário, resoluções, começos",
        category="curiosity",
        age_ranges=["3-6", "7-10", "all"],
        lead_days=7,
    ),
    SeasonalEntry(
        name="Dia do Planeta Terra",
        date="04-22",
        description="Dia da Terra — temas sobre meio ambiente, reciclagem, natureza",
        category="nature",
        age_ranges=["3-6", "7-10", "all"],
        lead_days=14,
    ),
    SeasonalEntry(
        name="Dia das Mães",
        date="05-12",  # Brazil: second Sunday of May (approximate)
        description="Dia das mães — temas sobre famílias, amor, gratidão",
        category="curiosity",
        age_ranges=["3-6", "7-10", "all"],
        lead_days=14,
    ),
    SeasonalEntry(
        name="Dia dos Pais",
        date="08-11",  # Brazil: second Sunday of August (approximate)
        description="Dia dos pais — temas sobre famílias, amor, gratidão",
        category="curiosity",
        age_ranges=["3-6", "7-10", "all"],
        lead_days=14,
    ),
    SeasonalEntry(
        name="Férias de Verão",
        date="12-20",  # Approximate start of summer break in Brazil
        description="Férias de verão — temas sobre praia, sol, viagens, brincadeiras",
        category="curiosity",
        age_ranges=["3-6", "7-10", "all"],
        lead_days=7,
    ),
    SeasonalEntry(
        name="Volta às Aulas",
        date="02-01",  # Approximate start of school year in Brazil
        description="Volta às aulas — temas sobre escola, aprendizado, amizade",
        category="curiosity",
        age_ranges=["3-6", "7-10", "all"],
        lead_days=14,
    ),
    SeasonalEntry(
        name="Páscoa",
        date="04-05",  # Approximate (varies by year)
        description="Páscoa — temas sobre coelhos, ovos, primavera (cultural, não religioso)",
        category="curiosity",
        age_ranges=["3-6", "7-10", "all"],
        lead_days=14,
    ),
    SeasonalEntry(
        name="Dia do Meio Ambiente",
        date="06-05",
        description="Dia do meio ambiente — temas sobre natureza, animais, conservação",
        category="nature",
        age_ranges=["3-6", "7-10", "all"],
        lead_days=14,
    ),
    SeasonalEntry(
        name="Festa Junina",
        date="06-24",
        description="Festa junina — temas sobre tradições brasileiras, comidas típicas, quadrilha",
        category="curiosity",
        age_ranges=["3-6", "7-10", "all"],
        lead_days=14,
    ),
    SeasonalEntry(
        name="Independência do Brasil",
        date="09-07",
        description="Independência do Brasil — temas sobre história, bandeira, símbolos nacionais",
        category="history",
        age_ranges=["7-10", "all"],
        lead_days=14,
    ),
]


def get_active_seasonal(
    ref_date: Optional[date] = None,
    lookahead_days: int = 30,
) -> list[SeasonalEntry]:
    """Get seasonal entries that are active (within lead_days before their date).

    Args:
        ref_date: Reference date (defaults to today).
        lookahead_days: How many days ahead to look for upcoming dates.

    Returns:
        List of seasonal entries that are currently relevant.
    """
    if ref_date is None:
        ref_date = date.today()

    active: list[SeasonalEntry] = []
    for entry in _SEASONAL_CALENDAR:
        # Parse the entry date (MM-DD format, recurring yearly)
        try:
            month, day = entry.date.split("-")
            entry_date = date(ref_date.year, int(month), int(day))
        except (ValueError, KeyError):
            continue

        # Check if the entry date is within the next lookahead_days
        days_until = (entry_date - ref_date).days
        if 0 <= days_until <= lookahead_days:
            active.append(entry)
        # Also check if we're in the lead-up period
        elif -entry.lead_days <= days_until < 0:
            active.append(entry)
        # Handle year wrap (e.g. entry in January, ref in December)
        elif days_until < -300:
            entry_date_next_year = date(ref_date.year + 1, int(month), int(day))
            days_until_next = (entry_date_next_year - ref_date).days
            if 0 <= days_until_next <= lookahead_days:
                active.append(entry)

    return active


def get_all_entries() -> list[SeasonalEntry]:
    """Return all seasonal entries."""
    return list(_SEASONAL_CALENDAR)


def get_entries_for_month(month: int) -> list[SeasonalEntry]:
    """Get seasonal entries for a specific month (1-12)."""
    return [
        entry for entry in _SEASONAL_CALENDAR
        if int(entry.date.split("-")[0]) == month
    ]
