"""Required fields expose values; optional fields retain their absence type."""

from typing import assert_type

from squid_ui.forms import Form, IntField, TextField


class Example(Form):
    name = TextField()
    explicit = TextField(required=True)
    note = TextField(required=False)
    count = IntField()


form = Example()
assert_type(form.name, str)
assert_type(form.explicit, str)
assert_type(form.note, str | None)
assert_type(form.count, int)
