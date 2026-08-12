# Step 5 Architecture Review Findings

1. **Retry Logic Bug (`sard/agent/routing.py` line 121 and `sard/agent/nodes/verify.py` line 337)**
   - **Severity**: High
   - **Reproduction**: Set `compose_max_retries = 1`. The pipeline will execute the initial `compose` and `verify`. If verification fails, `verify.py` increments `compose_retry_count` to 1 and checks `1 >= 1` (exhausted). `route_after_verification` checks `1 < 1` (False) and routes to `render`.
   - **Issue**: It performs `max_retries` total attempts instead of allowing `max_retries` *retries*.
   - **Suggested fix**: In `route_after_verification`, change the condition to `retry_count <= max_retries` or fix the increment logic in `verify.py` to correctly reflect retries vs. total attempts.

2. **Citation Proof Flaw (`sard/agent/nodes/verify.py` line 130 and 162)**
   - **Severity**: Medium
   - **Reproduction**: When the RAG service retrieves multiple chunks from the same document, they may share the same `citation_id` (e.g. `CIT-01`).
   - **Issue**: The code `duplicate_cits` identifies any `citation_id` appearing more than once in `evidence` as a duplicate. Later, `any(cid in duplicate_cits for cid in citation_ids)` fails any claim using that citation, marking it `UNSUPPORTED`. This penalizes valid claims supported by multiple chunks of the same document.
   - **Suggested fix**: Remove the `duplicate_cits` check or scope it strictly to actual identical `chunk_id` records.
