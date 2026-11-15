# Squid UI HTML

Compose portable Squid UI documents as semantic HTML.

```python
import squid_ui as ui
import squid_ui.ext.html as html

planned = ui.planning.plan(ui.paragraph("Hello"), target=html.target())
output = html.Renderer().draw(planned.scene, plan=planned)
```

The host supplies browser event dispatch and transport.
