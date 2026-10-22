# Tests

```bash
venv/bin/python tests/run.py                      # offline: free, ~20s
RUN_LIVE_TESTS=1 venv/bin/python tests/run.py     # adds the ones that call out
```

Offline is the default on purpose. A test you cannot afford to run is a test
nobody runs, so everything decidable without a provider is decided without
one: the detectors, retrieval, transcript repair, and how the socket behaves
under bad input, hang-ups and oversized frames.

The offline suites start a real server on a free port with `TEXT_ONLY=1` and
placeholder credentials. That works because the catalog load already copes
with a provider it cannot reach.

| Suite | Covers |
|---|---|
| `smarts.py` | routing tier, mood, sentence splitting |
| `languages.py` | which of the eight languages was spoken |
| `heard.py` | transcript repair |
| `memory.py` | identifying a caller by address or name |
| `orders.py` | order numbers said out loud |
| `knowledge_base.py` | retrieval |
| `tests/socket_robustness.py` | malformed frames, hang-ups, oversized input |
| `tests/sessions.py` | sign-in tokens expire and are forgotten |
| `tests/live_languages.py` | replies stay in the caller's language |
| `tests/live_escalation.py` | the right person takes the call |
| `tests/live_unexpected.py` | questions nobody planned for |
| `tests/live_audio.py` | a turn comes back as audio |

Only `live_audio.py` spends Cartesia credits, and only on one short turn.
The other live suites run in `TEXT_ONLY` mode: they are about what the agent
says, not what it sounds like, and synthesis is billed by the character.
