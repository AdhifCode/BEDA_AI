# Runbook: Queue Backlog / Processing Delay

## 1. Detection
- Queue depth metric `redis_stream_enquiry_stream_length > 100`.
- Latency between `received_at` and step `started_at` exceeds SLA threshold (> 60 seconds).

## 2. Immediate Containment
- Scale worker replicas horizontally:
  `kubectl scale deployment/beda-worker --replicas=5` or update ECS service desired count.

## 3. Diagnosis
- Inspect slow steps in worker JSON logs (`latency_ms` field).
- Check database connection pool saturation.

## 4. Recovery
- Monitor backlog drain rate until stream length returns to baseline (< 5).

## 5. Verification
- Confirm all workers acknowledge stream messages via consumer group.

## 6. Audit Requirements
- Document scale event and peak queue depth in operational retrospective.
