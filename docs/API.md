# Operator API

The source-checkout entry point is bin/tcl-cli.
Operational responses and argument errors are JSON; --help is human-readable text; success exits zero and rejected operations exit two.

| Command | Inputs | Result |
| --- | --- | --- |
| health | --state PATH | Verified current journal head |
| verify | --state PATH, optional --expected-head HASH | Replay verification against an independently retained head |
| snapshot | --state PATH | Consistent verified state and head in one read transaction |
| apply | --state PATH, --batch JSON_FILE, --expected-head HASH | Atomic mutation batch with stale-writer rejection |

Place --state before the command.
The batch is a JSON array of namespace, key, JSON value triples.
Namespaces and keys accept ASCII letters, digits, underscore, dot, colon, slash and hyphen, up to 256 characters.
A batch contains 1–10,000 unique keys; each canonical JSON value is at most 1 MiB and CLI files are at most 4 MiB.
NaN and Infinity are rejected.
The initial empty head is 64 zero hex digits.
Applying a batch changes the head; retain the returned head independently.
Null is a stored JSON value, not a deletion command.
History contains mutation values; use only nonsecret operator metadata.

StateStore.apply(changes, expected_head) serializes writers and returns the new head.
StateStore.claim(namespace, key, value) atomically inserts a previously absent key.
StateStore.verify(expected_head) replays history and compares all current rows.
NullifierSet(path, domain=deployment_domain) persists canonical scalar-field nullifiers.
A stale claim fails closed and may require a new read; external side effects are never retried by this API.
