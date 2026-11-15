"""Keep field types through descriptor storage and heterogeneous transaction staging."""

from typing import assert_type

from squid_reactivity import Reactive, transaction
from squid_reactivity._cells import _MISSING, _Cell, _Missing
from squid_reactivity._core import _State
from squid_reactivity.transactions._engine import _Staged
from squid_reactivity.transactions._state import _current_transaction


class Model(Reactive):
    count = _State[int](0)
    text = _State[str]("initial")


def test_missing_cell_value_is_distinct_from_none() -> None:
    cell = _Cell[int | None]()
    assert_type(cell.read(), int | None | _Missing)
    assert cell.read() is _MISSING
    cell.write(None)
    assert cell.read() is None
    cell.write(1)
    value = cell.read()
    if value is not _MISSING:
        assert_type(value, int | None)
    assert value == 1


def test_field_types_survive_staging_and_publication() -> None:
    model = Model()
    assert_type(Model.count, _State[int])
    assert_type(model.count, int)
    assert_type(model.text, str)
    count = Model.count.cell(model)
    text = Model.text.cell(model)
    assert_type(count, _Cell[int])
    assert_type(text, _Cell[str])
    with transaction():
        model.count = 7
        model.text = "staged"
        current = _current_transaction()
        assert current is not None
        assert_type(current.staged(count), _Staged[int] | None)
        assert_type(current.staged(text), _Staged[str] | None)
        assert_type(count.staged_value(), int | _Missing)
        assert (model.count, model.text) == (7, "staged")
        assert count.value == 0
        assert text.value == "initial"
    assert (count.read(), text.read()) == (7, "staged")
    assert count.staged_value() is _MISSING
