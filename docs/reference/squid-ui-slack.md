# squid-ui-slack

Slack SDK rendering is intentionally a leaf. Plan with `squid-ui`, draw SDK models here, and keep
listeners, dispatch, clients, retries, and delivery in the host application.

```python
import squid_ui.ext.slack as ss
```

## Targets and adapter profiles

::: squid_ui.ext.slack.SLACK_MESSAGE_SDK343

::: squid_ui.ext.slack.SLACK_MODAL_SDK343

::: squid_ui.ext.slack.SLACK_HOME_SDK343

::: squid_ui.ext.slack.message

::: squid_ui.ext.slack.modal

::: squid_ui.ext.slack.home

::: squid_ui.ext.slack.SLACK_SDK_343_ADAPTER

::: squid_ui.ext.slack.SLACK_SDK_BEHAVIOR_CAPABILITIES

::: squid_ui.ext.slack.slack_sdk_adapter_profile

## Renderers and outputs

::: squid_ui.ext.slack.MessageRenderer

::: squid_ui.ext.slack.ModalRenderer

::: squid_ui.ext.slack.HomeRenderer

::: squid_ui.ext.slack.MessagePayload

::: squid_ui.ext.slack.AssetResolver

## Advanced namespaces

| Namespace | Responsibility |
|---|---|
| `squid_ui.ext.slack.adapter` | Verified SDK version profiles and adapter boundary checks. |
| `squid_ui.ext.slack.target` | Message, modal, and App Home target conveniences. |
| `squid_ui.ext.slack.renderer` | Mechanical scene-to-SDK drawing and limit audits. |
| `squid_ui.ext.slack.message_payload` | Complete outgoing message kwargs. |
