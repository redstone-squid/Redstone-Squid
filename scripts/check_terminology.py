"""Check staged paths and text for retired project terminology."""

import os
import re
import subprocess
from hashlib import sha256

# Match compounds and plurals as well as standalone words.
RETIRED = rb"fac[e]t|taxon[o]m"

# Signatures cover the path and matching lines only. These freeze historical
# migration SQL, published contracts, and readers of previously stored data.
LEGACY_SIGNATURES = {
    "07f092262a117b36cf94677aefc217d092daf9fb86fbb97d9acb9600440c2a21",
    "15ef96e8390683e1062c4fa286d8b65c5f33fa419b9af96b998eba097b257528",
    "19650b0d9b4a80c284155643c3f75ced40aa67a2cbd69969ccbb88784c50429a",
    "1973a7bdfd9025eddc051800c6ab34bc5620cb32e79eaf2dc0d83bdaade855de",
    "1f0a6b09fc0c86407e2c1decf04315dc1f1dfb10d7bbcce4a42941a0a57b9029",
    "2731c319d571af79a52df7315177d369c857fbf669e7b6c2206dd4adf4e1b0af",
    "40bad7bd9711f3304dfa8082836d7f986f24241236140e5ab49f2272cf740691",
    "4457ab0ac44616e3349e8dbf885468c0888671bd8af595d83a6d93aabc1099a2",
    "4ba83be89270e8d81781885c7c29342381a666c0eae7b4f8576f907c5623cfac",
    "68061ef7a44e892aeca266ff263018651cdb8fc413400fc1fd952547e9000490",
    "7bcf34068a4688059335dd0da01541d422ab54b517ca39b168c2b6dcf6749974",
    "8b66725115736b860fcdcbdbc5f44ecfaf2a8d06159a68f451de3b4dc285ea6e",
    "8c8b9c0c46a065b4556c58533b431267af8dd9ea6fcbcf7c0555ba40a2039c14",
    "a5db418616389facbf7f0f26f18dd1f1e03db4c994d76c41a28fdd5e49c654b4",
    "cc2280d5feedccab78744e7c1f731e6b4b0f650073bcb8a132891dfcd99dca5f",
    "d2f0552cbdf23643904a9bedc29358c9864d2861f765d8863d8b852688c2b3f4",
    "dcb2cc66bb1a716d6e83eaed9c3f11d5a0e499bc8ae9c5b589f4c4f0ee43d9ee",
    "f2ea71220d9694e11e2658ddb83f6f8669ed67e2e46f2390191dfc68af6f9ed4",
    "fde2ed5303dcbc1a04ce03a950e188e6fee9b46ef2b56dc4e7f74386aa4ebc61",
}


def main() -> int:
    paths = subprocess.check_output(["git", "ls-files", "-z"]).split(b"\0")
    grep = subprocess.run(
        ["git", "grep", "--cached", "-IilzE", RETIRED, "--"],
        check=False,
        stdout=subprocess.PIPE,
    )
    if grep.returncode not in (0, 1):
        return grep.returncode

    pattern = re.compile(RETIRED, re.IGNORECASE)
    matched_paths = {path for path in paths if pattern.search(path)}
    matched_paths.update(filter(None, grep.stdout.split(b"\0")))

    failed = False
    for path in sorted(matched_paths):
        contents = subprocess.check_output(["git", "show", b":" + path])
        matches = [(number, line) for number, line in enumerate(contents.splitlines(), 1) if pattern.search(line)]
        signature = sha256(b"\0".join([path, *(line for _, line in matches)])).hexdigest()
        if signature in LEGACY_SIGNATURES:
            continue
        failed = True
        filename = os.fsdecode(path)
        if pattern.search(path):
            print(f"{filename}: retired terminology in path")
        for number, _ in matches:
            print(f"{filename}:{number}: retired terminology")

    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
