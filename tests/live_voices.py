"""Ask Cartesia what language every configured voice really is.

Free: it lists voices and synthesises nothing. Live only because it needs
the network and the key. The same check the server makes at startup, so a
failure here is a pod that would refuse to start.
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import voices                                                 # noqa: E402


def main():
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(ROOT, ".env"))
    except Exception:
        pass
    import cartesia
    client = cartesia.Cartesia(api_key=os.environ["CARTESIA_API_KEY"])
    said = {}

    def language_of(vid):
        try:
            voice = client.voices.get(vid)
        except cartesia.NotFoundError:
            raise LookupError(vid)
        said[vid] = (voice.name, voice.language)
        return voice.language

    problems = voices.check(language_of)
    results = []
    for code in voices.LANGUAGES:
        for role in voices.ROLES:
            vid = voices.for_role(role, code)
            name, lang = said.get(vid, ("?", None))
            ok = bool(vid) and voices.same_language(lang, code)
            results.append(ok)
            print(f"  {'ok  ' if ok else 'FAIL'} {code} {role:9} {name:12} "
                  f"Cartesia says {lang}")
    for line in problems:
        print(f"  FAIL {line}")
    results.append(not problems)
    print(f"\n  {sum(results)}/{len(results)} native")
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
