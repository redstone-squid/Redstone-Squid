# packages/squid-ui-widgets/src/squid_ui_widgets/agreement.py:90
widgets-agreement-participant = { $mark } { $display }

# packages/squid-ui-widgets/src/squid_ui_widgets/collection.py:148
widgets-collection-too-few =
    { NUMBER($minimum) ->
        [one] Add at least { NUMBER($minimum) } entry.
       *[other] Add at least { NUMBER($minimum) } entries.
    }

# packages/squid-ui-widgets/src/squid_ui_widgets/collection.py:157
widgets-collection-too-many =
    { NUMBER($maximum) ->
        [one] Keep no more than { NUMBER($maximum) } entry.
       *[other] Keep no more than { NUMBER($maximum) } entries.
    }

# packages/squid-ui-widgets/src/squid_ui_widgets/multi_choice.py:219
widgets-multi-choice-exclusive = { $group_label } cannot be combined with an exclusive group.

# packages/squid-ui-widgets/src/squid_ui_widgets/multi_choice.py:301
widgets-multi-choice-options = Options

# packages/squid-ui-widgets/src/squid_ui_widgets/multi_choice.py:297, packages/squid-ui-widgets/src/squid_ui_widgets/multi_choice.py:369
widgets-multi-choice-select-options = Select options

# packages/squid-ui-widgets/src/squid_ui_widgets/multi_choice.py:193
widgets-multi-choice-too-few =
    { NUMBER($minimum) ->
        [one] Select at least { NUMBER($minimum) } option.
       *[other] Select at least { NUMBER($minimum) } options.
    }

# packages/squid-ui-widgets/src/squid_ui_widgets/multi_choice.py:202
widgets-multi-choice-too-many =
    { NUMBER($maximum) ->
        [one] Select no more than { NUMBER($maximum) } option.
       *[other] Select no more than { NUMBER($maximum) } options.
    }

# packages/squid-ui-widgets/src/squid_ui_widgets/multi_choice.py:212
widgets-multi-choice-unavailable = One or more selected options are no longer available.
