# Mail Convenience Commands (Phase 14)

Six daily-driver verbs composed over the core mail surface.
All examples use generic `example.com` addresses; substitute your own.

Each verb is a thin orchestrator over existing primitives — `mail.move`,
`mail.categorize`, `mail.send`, and the catalog. There are no new audit
namespaces and no new Graph endpoints (except `internetMessageHeaders`
for `unsubscribe`). Every mutating action is `--confirm`-gated.

---

## `mail digest`

Print (or self-mail) a digest of unread messages from the local catalog.

**Synopsis**

```
m365ctl mail digest [--since 24h] [--limit 20] [--send-to ADDR --confirm] [--json]
```

**Example invocation**

```bash
m365ctl mail digest --since 24h --limit 5
```

**Example output**

```
Mail digest — since 2026-04-24T08:00+00:00 (now 2026-04-25T08:00+00:00)
Total: 12 unread

Top senders:
     4 alerts@example.com
     3 newsletter@example.com
     2 alice@example.com
     2 bob@example.com
     1 noreply@example.com

By category:
     5 Work
     4 Triage
     3 (uncategorised)

Recent (5):
  2026-04-25T07:42  alerts@example.com                Build #1234 succeeded
  2026-04-25T07:10  alice@example.com                 Re: Q2 planning
  2026-04-25T06:55  newsletter@example.com            Weekly roundup
  2026-04-25T05:30  bob@example.com                   Lunch tomorrow?
  2026-04-25T04:18  noreply@example.com               Password expires soon
```

`--send-to me --confirm` mails the HTML rendering to your own mailbox
(subject: `[Digest] 12 unread since 24h`) via the existing
`mail.send` executor. Without `--confirm`, prints a dry-run notice
on stderr and exits 0.

---

## `mail archive`

Bulk-move messages older than N days from a folder into
`Archive/<YYYY>/<MM>` via the existing `mail.move` executor and the
Phase 10 audit/undo path.

**Synopsis**

```
m365ctl mail archive --older-than-days N --folder PATH (--plan-out FILE | --confirm)
```

**Example invocation**

```bash
m365ctl mail archive --older-than-days 90 --folder Inbox --plan-out archive.plan.json
```

**Example output (plan-out / dry run)**

```
plan: 184 mail.move op(s) -> archive.plan.json
```

**Example output (`--confirm` execute)**

```bash
m365ctl mail archive --older-than-days 90 --folder Inbox --confirm
```

```
archived: 184 ok, 0 error(s)
```

Each operation carries `rule_name = mail-archive-<YYYYMM>` and lands
the message at `Archive/<YYYY>/<MM>` derived from its `received_at`.
You must pass exactly one of `--plan-out` (dry run) or `--confirm`
(execute); passing neither, or both, exits 2.

---

## `mail unsubscribe`

Parse `List-Unsubscribe` headers (RFC 2369 + RFC 8058) for a message
and optionally act on a discovered method.

**Synopsis**

```
m365ctl mail unsubscribe <message-id> [--method http|mailto|first] [--dry-run|--confirm]
```

**Example invocation (discover only)**

```bash
m365ctl mail unsubscribe AAMkAGI...=
```

**Example output**

```
discovered 2 unsubscribe method(s):
   https  https://lists.example.com/u/abc123 [one-click]
  mailto  mailto:unsubscribe@example.com?subject=unsubscribe
```

**Example invocation (act, http one-click)**

```bash
m365ctl mail unsubscribe AAMkAGI...= --method http --confirm
```

```
POST https://lists.example.com/u/abc123 → 200
```

`--method http` without `--confirm` (and without `--dry-run`) prints a
dry-run notice on stderr and exits 0. `--method mailto` prints the
target address + suggested subject — your mail client handles the rest.
Messages with no `List-Unsubscribe` header print `(no unsubscribe header)`
on stderr and exit 0.

---

## `mail snooze`

Two modes: defer a message into `Deferred/<YYYY-MM-DD>` + tag it
`Snooze/<YYYY-MM-DD>`, or `--process` due Deferred folders back into
Inbox. Both compose existing `mail.move` + `mail.categorize` ops.

**Synopsis**

```
m365ctl mail snooze <message-id> --until <date|relative> --confirm
m365ctl mail snooze --process --confirm
```

**Example invocation (defer)**

```bash
m365ctl mail snooze AAMkAGI...= --until 5d --confirm
```

```
snoozed AAMkAGI...= until 2026-04-30: 2 ok, 0 error(s)
```

**Example invocation (process due)**

```bash
m365ctl mail snooze --process --confirm
```

```
processed 2 due folder(s), 7 op(s): 7 ok, 0 error(s)
```

`--until` accepts ISO dates (`2026-05-01`) or short relative durations
(`5d`, `24h`). Without `--confirm`, both modes print a dry-run notice
on stderr and exit 2. `--process` walks every `Deferred/<YYYY-MM-DD>`
folder where `<date> ≤ today` and moves all its messages back to Inbox,
removing the matching `Snooze/<date>` category.

---

## `mail top-senders`

Top senders by message count, read directly from the local catalog.
Optional `--since` filter restricts to messages received in the window.

**Synopsis**

```
m365ctl mail top-senders [--since 30d] [--limit 20] [--json]
```

**Example invocation**

```bash
m365ctl mail top-senders --since 30d --limit 5
```

**Example output**

```
   count  sender
     128  newsletter@example.com
      94  alerts@example.com
      57  alice@example.com
      33  bob@example.com
      21  noreply@example.com
```

Empty catalog prints `(no senders in catalog — run 'mail catalog refresh' first)`
on stderr and exits 0. With `--json` the same rows stream as
NDJSON (one record per line).

---

## `mail size-report`

Per-folder message-count + total-size breakdown from the mail catalog,
sorted by total size desc.

**Synopsis**

```
m365ctl mail size-report [--top N] [--json]
```

**Example invocation**

```bash
m365ctl mail size-report --top 5
```

**Example output**

```
   count          size  folder
    8421     2.3 GiB    Archive/2024
    3210     1.1 GiB    Inbox
    1893   612.4 MiB    Sent Items
     742   188.0 MiB    Archive/2025/03
     310    72.1 MiB    Deleted Items
```

Empty catalog prints
`(no folders in catalog — run 'mail catalog refresh' first)` on
stderr and exits 0. With `--json` the rows stream as NDJSON.
