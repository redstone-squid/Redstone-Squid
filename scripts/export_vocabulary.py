"""Export domain value labels in every supported locale for the website."""

import json

from squid import PROJECT_ROOT
from squid.core.i18n import SUPPORTED_LOCALES, localization_for
from squid.core.vocabulary import VOCABULARY
from squid_i18n import localization_scope

OUTPUT_PATH = PROJECT_ROOT / "contracts" / "vocabulary.json"


def main() -> None:
    """Write `{locale: {group: {value: label}}}` with English first and other locales sorted."""
    locales = ["en", *sorted(SUPPORTED_LOCALES - {"en"})]
    document: dict[str, dict[str, dict[str, str]]] = {}
    for locale in locales:
        with localization_scope(localization_for(locale)):
            document[locale] = {
                group: {value: label.resolve() for value, label in labels.items()}
                for group, labels in VOCABULARY.items()
            }
    OUTPUT_PATH.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
