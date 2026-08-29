# squid-ui-slack

The Slack SDK renderer for [`squid-ui`](https://pypi.org/project/squid-ui/). It turns planned
Slack message, modal, and App Home scenes into `slack-sdk` Block Kit models without owning a
client, listener, network connection, or delivery lifecycle.

This is an alpha release. The Python API may change before 1.0.

```console
pip install squid-ui-slack==0.1.0a1
```

Plan with one of the package targets, then draw the result and hand it to the Slack client your
application already owns:

```python
import squid_ui as sl
import squid_ui_slack as ss

result = sl.plan(document, ss.SLACK_MESSAGE_SDK343)
payload = ss.MessageRenderer().draw(result.scene, plan=result)
await client.chat_postMessage(channel=channel_id, **payload.to_kwargs())
```

`ModalRenderer` and `HomeRenderer` return `slack_sdk.models.views.View` objects suitable for
`views_open` and `views_publish`. Public asset URLs come from an injected resolver; this package
never uploads files.
