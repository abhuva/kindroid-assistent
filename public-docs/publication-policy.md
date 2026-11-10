# Publication policy

The public material is the method, the engineering process and deliberately
reviewed findings. The underlying conversational archive remains local.

## Suitable for publication

- Descriptions of methods and implementation decisions.
- Synthetic examples clearly labelled as invented.
- Aggregate benchmark results with their setup and limitations.
- Public source-code references and documentation links.

## Kept private

- Conversation transcripts, direct quotations from private chats and raw exports.
- Account or participant identifiers, source-record IDs and private media.
- Local databases, analysis caches, logs and machine-specific configuration.
- Credentials, authentication material and account configuration.
- Screenshots or figures containing readable private content or identifiers.

An aggregate is not automatically anonymous. Rare events, small groups and
distinctive quotations can identify the underlying material. Review a result in
context before deciding whether it belongs on this site.

## How publication is separated

The site is built only from the curated publication directory. It is deployed
from a dedicated documentation branch containing a reviewed set of source files.
Application state and backups are not copied to that branch or its Pages artifact.

Private drafts are stored outside the publication source. **A draft committed to
a public branch is already public**, even if it is hidden from navigation or
excluded from the rendered site. Removing a page later does not reliably erase
its Git history, caches or copies.

Automated checks reject known private paths, raw data file types, credential-like
values and unsupported publication files. They cannot prove that prose is
anonymous. Human review is part of publishing.
