"""Locks for flow-level logic: email validation, offerable formats, dedup."""
import asyncio

import pytest

from librarian.clients import flow


# --- email validation ------------------------------------------------------
@pytest.mark.parametrize(
    "value,ok",
    [
        ("user@example.com", True),
        ("a.b+tag@sub.domain.co", True),
        ("no-at-sign", False),
        ("two@@at.com", False),
        ("space in@x.y", False),
        ("back`tick@x.y", False),
        ("nodot@domain", False),
        ("x" * 250 + "@a.bc", False),
        ("", False),
    ],
)
def test_is_valid_email(value, ok):
    assert flow._is_valid_email(value) is ok


# --- offerable formats (mirror of the selection logic in run_search) -------
def _offerable(src_ext, allowed):
    return list(allowed) if src_ext == "epub" else [src_ext]


def test_epub_source_offers_all_allowed_formats():
    assert _offerable("epub", ["epub", "pdf", "mobi", "azw3"]) == ["epub", "pdf", "mobi", "azw3"]


def test_pdf_source_offers_only_pdf():
    assert _offerable("pdf", ["epub", "pdf", "mobi", "azw3"]) == ["pdf"]


# --- volume sub-title recovery (label must follow the downloaded file) ------
class _Cand:
    def __init__(self, title):
        self.title = title


_KNOWN = ["Le Vaisseau magique", "L'Éveil des eaux dormantes", "Prisons d'eau et de bois",
          "Les Marches du trône", "Le Seigneur des trois règnes"]


def test_volume_title_relabels_from_file_and_snaps_to_wikidata_casing():
    # Wikidata put « L'Éveil » at slot 5, but the file that IS tome 5 is « Prisons » — the
    # label must follow the file, snapped to Wikidata's clean casing.
    cand = _Cand("Les Aventuriers de la mer (Tome 5) - Prisons d'eau et de bois")
    assert flow._volume_title(5, "L'Éveil des eaux dormantes", cand, "Les Aventuriers de la Mer", _KNOWN) \
        == "Prisons d'eau et de bois"


def test_volume_title_keeps_wikidata_when_file_agrees_or_number_differs():
    agree = _Cand("Les Aventuriers de la mer (Tome 1) - Le vaisseau magique")
    assert flow._volume_title(1, "Le Vaisseau magique", agree, "Les Aventuriers de la Mer", _KNOWN) \
        == "Le Vaisseau magique"  # same volume → keep Wikidata spelling
    # A file whose tome number can't be read (or ≠ num) must never override the label.
    murky = _Cand("L'assassin royal [002] – L'assassin du roi")
    assert flow._volume_title(2, "L'Assassin du roi", murky, "L'Assassin Royal", ["L'Assassin du roi"]) \
        == "L'Assassin du roi"


# --- series disambiguation (pick the right cycle of a saga) -----------------
def test_sig_words_drops_articles_and_cycle_words():
    assert flow._sig_words("l'assassin royal deuxième cycle") == {"assassin", "royal", "deuxieme"}
    assert flow._sig_words("Les Aventuriers de la Mer") == {"aventuriers", "mer"}
    assert flow._sig_words("Cycle du Prophète Blanc") == {"prophete", "blanc"}


_HOBB_SERIES = [
    ("Q2197634", "Les Cités des Anciens", 11),
    ("Q2113889", "Cycle du Prophète Blanc", 9),
    ("Q2211517", "Les Aventuriers de la mer", 7),
    ("Q2512889", "Cycle de l'Assassin royal", 7),
    ("Q99372737", "Cycle du Fou et de l'Assassin", 6),
]


def test_series_decision_picks_one_clear_match():
    mode, hit = flow._series_decision("les aventuriers de la mer", _HOBB_SERIES)
    assert mode == "pick" and hit[1] == "Les Aventuriers de la mer"
    # « l'assassin royal » alone → only « Cycle de l'Assassin royal » has both words
    mode, hit = flow._series_decision("l'assassin royal", _HOBB_SERIES)
    assert mode == "pick" and hit[1] == "Cycle de l'Assassin royal"


def test_series_decision_asks_when_ambiguous():
    # no label contains « deuxieme » → 0 strong match → ask
    assert flow._series_decision("l'assassin royal deuxième cycle", _HOBB_SERIES) == ("ask", None)
    # « assassin » matches TWO cycles → ask
    assert flow._series_decision("assassin", _HOBB_SERIES) == ("ask", None)


def test_series_decision_noop_for_single_series():
    assert flow._series_decision("dune", [("Q1", "Dune", 6)]) == (None, None)


def test_series_decision_ignores_language_and_intent_noise():
    # « en vf » / « intégrale » must not force the menu when the query names one series
    mode, hit = flow._series_decision("l'intégrale des aventuriers de la mer en vf", _HOBB_SERIES)
    assert mode == "pick" and hit[1] == "Les Aventuriers de la mer"


# --- author identification from the catalogue (+ menu when ambiguous) --------
class _Res:
    def __init__(self, author):
        self.author = author


def test_catalogue_authors_groups_name_variants():
    results = [_Res("Robin Hobb")] * 14 + [_Res("Hobb, Robin")] * 2 + [_Res("George R.R. Martin")] * 3
    authors = flow._catalogue_authors(results)
    assert authors[0][1] == 16 and {"robin", "hobb"} <= flow._sig_words(authors[0][0])  # variants merged
    assert authors[1][1] == 3


def test_pick_author_returns_dominant_without_asking():
    class Ctx:
        async def ask_choice(self, *a):
            raise AssertionError("dominant author → must not ask")

    res = [_Res("Robin Hobb")] * 10 + [_Res("Autre Auteur")] * 1
    assert asyncio.run(flow._pick_author(Ctx(), "q", res)) == "Robin Hobb"


def test_pick_author_asks_when_ambiguous():
    class Ctx:
        def __init__(self):
            self.asked = False

        async def ask_choice(self, card, choices):
            self.asked = True
            return choices[0].value

    ctx = Ctx()
    res = [_Res("Auteur Alpha")] * 5 + [_Res("Auteur Beta")] * 4  # 5 vs 4 → no clear dominant
    picked = asyncio.run(flow._pick_author(ctx, "q", res))
    assert ctx.asked and picked in ("Auteur Alpha", "Auteur Beta")


# --- missing-volume note (unavailable tomes shown, not silently dropped) ----
def test_missing_note_lists_unavailable_volumes():
    assert flow._missing_note([]) == ""
    note = flow._missing_note([(6, "L'Homme noir"), (9, "Les Marches")])
    assert "Tome 6 — L'Homme noir (indisponible)" in note
    assert "Tome 9 — Les Marches (indisponible)" in note


def test_missing_note_handles_unnumbered_volume():
    assert flow._missing_note([(None, "Wool")]) == "\n\n⚠️ Introuvable(s) dans le catalogue :\n⚪ Wool (indisponible)"


# --- batch sub-status line (live feedback under the current tome) -----------
def test_status_line_surfaces_download_percent():
    # the percent lives on the 2nd line of the progress message — it must be surfaced
    assert flow._status_line("⬇️ « Titre »\n▓▓░░░ 45%  (1.2 / 2.6 MB)") == "⬇️ téléchargement 45%"


def test_status_line_takes_first_line_and_strips_spinner_dots():
    assert flow._status_line("⏳ Recherche du fichier ...") == "⏳ Recherche du fichier"
    assert flow._status_line("🔄 Essai du résultat suivant : « X »…").startswith("🔄 Essai")


def test_status_line_ignores_non_string():
    assert flow._status_line(None) == ""
    assert flow._status_line(object()) == ""


def test_clean_subtitle_strips_series_tome_and_edition_cruft():
    S = "Les Aventuriers de la Mer"
    assert flow._clean_subtitle("Ombres et flammes (Les Aventuriers de la mer, 8) (French Edition)", S) \
        == "Ombres et flammes"
    assert flow._clean_subtitle("Brumes et tempêtes (Les Aventuriers de la mer (4)) (French Edition)", S) \
        == "Brumes et tempêtes"
