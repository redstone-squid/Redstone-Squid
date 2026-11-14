# packages/squid-ui-widgets/src/squid_ui_widgets/agreement.py:90
widgets-agreement-participant = { $mark } { $display }

# packages/squid-ui-widgets/src/squid_ui_widgets/collection.py:149
widgets-collection-too-few =
    { NUMBER($minimum) ->
        [one] Add at least { NUMBER($minimum) } entry.
       *[other] Add at least { NUMBER($minimum) } entries.
    }

# packages/squid-ui-widgets/src/squid_ui_widgets/collection.py:158
widgets-collection-too-many =
    { NUMBER($maximum) ->
        [one] Keep no more than { NUMBER($maximum) } entry.
       *[other] Keep no more than { NUMBER($maximum) } entries.
    }

# packages/squid-ui-widgets/src/squid_ui_widgets/multi_choice.py:221
widgets-multi-choice-exclusive = { $group_label } cannot be combined with an exclusive group.

# packages/squid-ui-widgets/src/squid_ui_widgets/multi_choice.py:303
widgets-multi-choice-options = Options

# packages/squid-ui-widgets/src/squid_ui_widgets/multi_choice.py:299, packages/squid-ui-widgets/src/squid_ui_widgets/multi_choice.py:371
widgets-multi-choice-select-options = Select options

# packages/squid-ui-widgets/src/squid_ui_widgets/multi_choice.py:195
widgets-multi-choice-too-few =
    { NUMBER($minimum) ->
        [one] Select at least { NUMBER($minimum) } option.
       *[other] Select at least { NUMBER($minimum) } options.
    }

# packages/squid-ui-widgets/src/squid_ui_widgets/multi_choice.py:204
widgets-multi-choice-too-many =
    { NUMBER($maximum) ->
        [one] Select no more than { NUMBER($maximum) } option.
       *[other] Select no more than { NUMBER($maximum) } options.
    }

# packages/squid-ui-widgets/src/squid_ui_widgets/multi_choice.py:214
widgets-multi-choice-unavailable = One or more selected options are no longer available.
