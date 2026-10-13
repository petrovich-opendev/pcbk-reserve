"""Число мест стенда — одна константа (Д15б): PLACES/PLACE_MAX, двузначный номер строится из PLACE_MAX, не руками
в каждом файле; CALLER_RE — вызывающий student-NN."""
from pcbk_core.places import CALLER_RE, PLACE_MAX, PLACE_NN, PLACES, place_text


def test_places_constant():                                               # Д15б: 11 мест, 12 — первое недопустимое
    assert (list(PLACES), PLACE_MAX) == (list(range(1, 12)), 11)
    assert CALLER_RE.fullmatch("student-11") is not None                   # да
    assert CALLER_RE.fullmatch("student-12") is None                       # нет — первое вне диапазона
    assert CALLER_RE.fullmatch("student-00") is None                       # нет — ноль не место
    assert all(CALLER_RE.fullmatch(f"student-{n:02d}") for n in PLACES)
    assert place_text() == "01…11"


def test_place_nn_has_no_leading_zero_variant():                          # регэкс — двузначный номер, не однозначный
    import re
    nn = re.compile(PLACE_NN)
    for bad in ("1", "12", "00", "9", ""):
        assert nn.fullmatch(bad) is None, bad
    for ok in ("01", "09", "10", "11"):
        assert nn.fullmatch(ok) is not None, ok
