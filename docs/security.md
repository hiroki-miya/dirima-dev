# Security boundaries

Authentication defaults to a bearer token loaded from the environment. The token is never stored in PostgreSQL or bundled into the frontend. The browser holds it in session storage and sends it per request. `DIRIMA_AUTH_MODE=none` is allowed only on a loopback bind. Compose binds the browser and API to localhost.

Agent messages, transcripts, artifacts and repository files are untrusted data. The model system instruction explicitly states this boundary. Extractor output is schema validated and retried twice; failure creates an AttentionItem. LLM claims do not establish verification PASS. High-risk, irreversible, security, privacy, legal, external publication and financial decisions require a human.

The redaction layer masks secret-named JSON keys and common inline credentials before model calls and UI serialization. Event payloads and artifacts can retain original source data for evidence preservation; protect PostgreSQL and artifact volumes as sensitive storage. The default UI reveals raw events only after drill-down and applies redaction there too. Model provenance stores hashes and usage, not the full input.

Audit and event rows reject UPDATE and DELETE in PostgreSQL. All corrections append new records or create superseding Runs. Workspace-specific bearer tokens are bound to a single configured workspace; the API checks resource ownership on every read and mutation.
