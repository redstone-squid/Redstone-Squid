# Squid UI Slack compile quickstart

Squid UI requires Python 3.14 or newer and Slack SDK 3.43.

```console
python -m pip install squid-ui-slack==0.1.0a1
```

Author the same portable document used by other Squid UI targets, then select the Slack message
target and draw the planned scene:

```python
import squid_ui as sl
import squid_ui_slack as ss

document = sl.stack(
    sl.heading("Build review"),
    sl.paragraph("The submission is ready."),
    sl.action_controls(
        sl.action_control("Approve", approve, key="approve"),
        key="review-actions",
    ),
)
planned = sl.planning.plan(document, target=ss.SLACK_MESSAGE_SDK343)
payload = ss.MessageRenderer().draw(planned.scene, plan=planned)
await client.chat_postMessage(channel=channel_id, **payload.to_kwargs())
```

`payload.blocks` contains Slack SDK `Block` models. The fallback `payload.text` is planned with the
blocks for screen readers and notifications. The `approve` action ID is also the key in
`planned.bindings`; the host's existing Slack listener decides how to acknowledge and invoke it.

## Modal and App Home views

A Slack modal has exactly one top-level portable form. Its fields become native Block Kit input
blocks and the renderer returns an SDK `View`:

```python
from squid_ui import forms

spec = forms.FormSpec("Edit build", (forms.TextField("Name", "name"),))
document = sl.form("Save", spec, key="edit-build", on_submit=save_build)
planned = sl.planning.plan(document, target=ss.SLACK_MODAL_SDK343)
view = ss.ModalRenderer().draw(planned.scene, plan=planned)
await client.views_open(trigger_id=trigger_id, view=view.to_dict())
```

Use `ss.SLACK_HOME_SDK343` with `ss.HomeRenderer` for `views_publish`. The host owns client
construction, tokens, listeners, acknowledgement deadlines, retries, routing, and delivery. This
package deliberately does not depend on Slack Bolt.

## Assets

Slack cannot attach Squid's inline bytes at draw time. Download buttons therefore need either a
`StoredAsset` containing a public HTTPS URL or an `asset_resolver` passed to `MessageRenderer`.
The renderer validates the URL and never uploads content.
