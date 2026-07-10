# CoMemBus v1.4 Reliability Design

## Compatibility boundary

v1.4 extends the existing JSON frame and AgentBus commands. It does not introduce a second wire protocol. Existing code can continue to call:

```python
client.publish(topic, payload)
payload = client.poll(topic)
```

Legacy `poll()` sends `auto_ack=true`, so successful return retains the previous destructive-consume behavior. Reliable consumers opt into `poll_reliable()`, `ack()`, `nack()`, and `renew_visibility()`.

Every newly-created `Message` has `message_id`, `delivery_attempt`, `created_at`, and nullable `visibility_deadline`. `Message.from_dict()` still accepts older frames without those keys and supplies validated defaults.

## Delivery state machine

A message moves through these states:

```text
publish -> available -> invisible -> acked/processed
                         |   |
                         |   +-> nack -> available
                         +-> visibility timeout -> available
```

`delivery_attempt` starts at zero and increments whenever poll moves a message to invisible. Capacity counts both available and invisible messages. A configured `max_queue_size` therefore bounds total outstanding work; `QueueFullError` is serialized by the server and reconstructed as the same exception class by the client.

ACK records the business result in `DedupStore`. A later publish with the same ID returns the original result with `duplicate_suppressed=true`. A duplicate ID already available or invisible is also suppressed. Unknown ACK/NACK/renew operations raise `MessageNotFoundError`; they are never reported as successful.

Visibility renewal first requeues already-expired work, then only renews a message that remains invisible. This prevents a late heartbeat from stealing a message already eligible for redelivery.

## Shared-memory lifecycle

`ObjectLeaseManager` tracks:

- `object_id`, `shm_name`
- `owner_agent`, `consumer_agents`
- `ref_count`
- `lease_deadline`
- `state`
- `created_at`, `last_access`

Internally, refcount is derived from a set of active consumer holders, making repeated acquire/release idempotent. Release does not unlink immediately. Normal GC requires both refcount zero and an expired lease.

If a consumer crashes with an outstanding reference, lease expiry classifies the object as leaked, clears expired holders, makes refcount zero, and unlinks the shared-memory object. Other consumers are protected until the shared lease expires or all work is explicitly force-cleaned. Statistics distinguish cumulative leaked and reclaimed object counts.

`force_cleanup()` is intended for owner shutdown and test `finally` blocks. Unexpected shared-memory errors propagate. Repeated cleanup of an already terminal lifecycle record is a no-op.

## SQLite/WAL state recovery

`SQLiteStateManager` uses file-backed SQLite with WAL and full synchronous writes. The main tables are:

```text
states(task_id, version, snapshot_json, compacted_version, updated_at)
patches(patch_id, task_id, expected_version, resulting_version,
        patch_json, applied_at)
```

Patch application runs under `BEGIN IMMEDIATE`:

1. Read and deserialize the current snapshot.
2. Validate `expected_version` with the existing `apply_patch()` implementation.
3. Insert the patch audit record.
4. Update the snapshot with a version-guarded SQL update.
5. Commit both changes together.

Any validation or SQL failure rolls the transaction back and propagates. Locked/busy errors alone are retried with bounded linear backoff. Exhaustion raises `SQLiteBusyError` with the final database error as its cause.

`recover(task_id)` returns the latest committed snapshot and rejects an impossible audit log that is ahead of it. `compact(task_id)` rewrites the current snapshot and removes patch rows already represented by that version in one transaction. Reopening a manager on the same database exercises real process-restart recovery.

## Patch rebase rules

`PatchRebaser` compares the state on which a stale patch was built with the latest state:

- A `set_fields` target changed in the latest state: conflict.
- A list append: safe to compose with other appends.
- A facts/artifacts key untouched since base: safe.
- The same facts/artifacts key changed since base: conflict.

Conflicts raise `PatchConflictError` with explicit field paths. The rebaser never silently chooses one scalar value over another.

## Failure injection methodology

`bench_failure_recovery.py` executes eight real recovery paths:

1. A consumer polls and closes without ACK; timeout causes attempt 2.
2. A processed message is published again; the stored result is returned and business execution remains one.
3. A consumer keeps an ObjectRef and crashes; deterministic lease expiry reclaims it.
4. Two patches start at one version; the stale non-conflicting patch is rejected first, then explicitly rebased.
5. A coordinator crashes immediately after transactional commit; a new manager recovers version 2.
6. An external SQLite transaction holds the write lock; bounded retries succeed after release.
7. A localhost LLM endpoint is unreachable; explicit mock fallback completes a durable state update.
8. CodeAct exceeds its timeout; the error is surfaced and the coordinator advances the main state through a recovery patch.

The CSV records success, recovery latency, delivery attempts, suppression/requeue/recovery/reclamation flags, shared-memory residue, and unexpected error text. The runner continues collecting rows after an unexpected scenario error, but marks that row failed and exits nonzero after writing the evidence.

All implementation and tests use only the Python standard library and Linux facilities available on openEuler 24.03-LTS-SP3.
