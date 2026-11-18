# How a screen updates

In the [Discord quickstart](squid-ui-quickstart.md), a counter holds an integer,
describes a message, and handles button presses. Those three pieces are enough
to explain what Squid UI does when the count changes.

## From state to message { #end-to-end-flow }

1. **Render the component.** The runtime calls `render()`. The counter reads its
   count and returns nodes describing a heading, a paragraph, and an action.
   Keep this method focused on describing the interface. Put writes and network
   requests in handlers or application services.
2. **Plan the layout.** The planner arranges those nodes for a *target*, which
   describes what the platform supports and what limits apply. The result
   contains a *scene* describing what to display and *bindings* that connect
   controls to Python handlers.
3. **Draw and deliver.** The Discord renderer turns the scene into a message
   payload. The runtime sends or edits the message and makes the new bindings
   current when delivery succeeds.

When Discord reports an **Add** press, the runtime uses the current binding
to call the handler. Updating state read by `render()` makes the screen stale,
so the runtime can repeat the cycle. Use stable keys for controls that are
rendered repeatedly so the runtime can track their identity.

## Share state between components { #reusable-reactive-models }

Use `sl.state()` on a component for values that belong to that instance.
When multiple components need to read or change the same values, put them on
an `sl.Reactive` model and give the components the same model instance. Reads
from the model during `render()` are tracked in the same way as reads from the
component.

## Choose who can see and use a screen

A screen's `ResponseSpec` controls opening behavior. `audience` governs who can
see the message. `access` governs who can interact with it. For example, a shared
counter needs both a public message and access for everyone who can see it.

```python
class SharedCounter(Counter):
    policy = sd.ResponseSpec(audience="public", access=sd.Everyone())
```

Use the [standalone rendering guide](squid-ui-slack-quickstart.md) if your
application already handles delivery and needs to produce output for another target.
