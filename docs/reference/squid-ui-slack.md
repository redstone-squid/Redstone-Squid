# squid-ui-slack

Slack SDK rendering is intentionally a leaf. Plan with `squid-ui`, draw SDK models here, and keep
listeners, dispatch, clients, retries, and delivery in the host application.

```python
import squid_ui_slack as ss
```

## Targets and adapter profiles

::: squid_ui_slack.SLACK_MESSAGE_SDK343

::: squid_ui_slack.SLACK_MODAL_SDK343

::: squid_ui_slack.SLACK_HOME_SDK343

::: squid_ui_slack.message

::: squid_ui_slack.modal

::: squid_ui_slack.home

::: squid_ui_slack.SLACK_SDK_343_ADAPTER

::: squid_ui_slack.SLACK_SDK_BEHAVIOR_CAPABILITIES

::: squid_ui_slack.slack_sdk_adapter_profile

## Renderers and outputs

::: squid_ui_slack.MessageRenderer

::: squid_ui_slack.ModalRenderer

::: squid_ui_slack.HomeRenderer

::: squid_ui_slack.MessagePayload

::: squid_ui_slack.AssetResolver

## Advanced namespaces

| Namespace | Responsibility |
|---|---|
| `squid_ui_slack.adapter` | Verified SDK version profiles and adapter boundary checks. |
| `squid_ui_slack.target` | Message, modal, and App Home target conveniences. |
| `squid_ui_slack.renderer` | Mechanical scene-to-SDK drawing and limit audits. |
| `squid_ui_slack.message_payload` | Complete outgoing message kwargs. |
