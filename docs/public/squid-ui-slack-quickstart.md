# Render to Slack or HTML

Use the planner and a renderer directly when your application already handles
delivery. The same portable nodes can produce Slack blocks or native HTML.
A target describes the controls and layout rules available to the planner. The
planner produces a scene describing what to display. A renderer turns that scene
into a payload or markup.

## Slack

Install the Slack adapter.

```console
python -m pip install squid-ui-slack==0.1.0a1
```

Plan a message, then draw the scene into a payload for the Slack SDK.

```python
import squid_ui as sl
import squid_ui.ext.slack as slack

document = sl.stack(
    sl.heading("Build review"),
    sl.paragraph("The submission is ready to review."),
)
planned = sl.planning.plan(document, target=slack.SLACK_MESSAGE_SDK343)
message = slack.MessageRenderer().draw(planned.scene, plan=planned)
```

With your Slack client and channel ID, call
`await client.chat_postMessage(channel=channel_id, **message.to_kwargs())`.
The payload includes blocks and fallback text. If you add interactive controls,
your app handles Slack acknowledgements and dispatches the interaction using
the plan's bindings.

## HTML

Install the HTML adapter.

```console
python -m pip install squid-ui-html==0.1.0a1
```

Describe the content and plan it for the HTML target.

```python
import squid_ui as sl
import squid_ui.ext.html as html

document = sl.paragraph("The submission is ready to review.")
planned = sl.planning.plan(document, target=html.target())
fragment = html.Renderer().draw(planned.scene, plan=planned)
```

Embed `fragment` in a page, or use `html.Renderer(standalone=True)` to generate
a complete page. If you add controls, your application connects browser events
to the handlers.

The [scene schema](schema/scene-v1.schema.json) describes the serialized display
data for applications that build their own renderer. Python handlers are kept
in the plan's bindings.
