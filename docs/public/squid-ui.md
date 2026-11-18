# Squid UI

Squid UI is a Python framework for describing an interface in terms of its
content and controls. You write headings, paragraphs, and buttons. A platform
adapter turns them into an interface that fits the platform's limits.

| If you're building… | Start here |
| --- | --- |
| A Discord bot with interactive messages | [Build a Discord screen](squid-ui-quickstart.md) |
| A Slack app that needs Block Kit output | [Render to Slack or HTML](squid-ui-slack-quickstart.md) |
| An application that needs native HTML | [Render to Slack or HTML](squid-ui-slack-quickstart.md#html) |

The Discord adapter also runs the interface. It receives button presses and updates
messages when state changes. With Slack and HTML, your application owns delivery and
interaction handling.

Squid UI requires Python 3.14 or newer. The packages are in alpha and their Python
APIs may change. The guides use the versions released together from this repository.
