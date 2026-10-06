"""Alert triage: assign severity and owning team from raw alert text."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import Tally, ask, choice

SEVERITY = {
    "sev1": "Customer-facing outage or total loss of a critical service",
    "sev2": "Degraded service or a real risk of outage soon",
    "sev3": "Minor issue, no customer impact",
    "info": "Informational, nothing to do",
}
OWNER = {
    "database": "Databases, replication, storage, backups",
    "network": "DNS, VPN, routing, load balancers, connectivity",
    "payments": "Payment processing, checkout, payouts",
    "platform": "Kubernetes, frontend hosting, certificates, CI/CD",
}
QUESTIONS = {
    "severity": choice("How severe is this alert?", SEVERITY),
    "owner": choice("Which team should be paged?", OWNER),
}

ALERTS = [
    ("db-prod-1: replication lag 340s and rising; replica read errors", "sev2", "database"),
    ("payments-api: 5xx rate 38% for 6 minutes, checkout failing for all customers", "sev1", "payments"),
    ("web-frontend: p95 latency 1.2s (SLO 1.0s) for 10 minutes", "sev3", "platform"),
    ("vpn-gateway-2: BGP session flapping, 12% packet loss to us-east-1", "sev2", "network"),
    ("db-prod-3: disk usage 94%, growing 1% per hour", "sev2", "database"),
    ("nightly backup job completed successfully in 42 minutes", "info", "database"),
    ("payments-worker: queue depth 50,000, consumers lagging, payouts delayed", "sev2", "payments"),
    ("k8s node ip-10-0-3-17 NotReady; pods rescheduled automatically, no customer impact", "sev3", "platform"),
    ("DNS resolution failing in us-east-1 for all internal services, total outage", "sev1", "network"),
    ("certificate for staging.internal expires in 25 days", "sev3", "platform"),
]

if __name__ == "__main__":
    t = Tally("03_incident_triage")
    for text, want_sev, want_owner in ALERTS:
        a = ask(text, QUESTIONS)
        sev, own = a["severity"]["choice"], a["owner"]["choice"]
        print(
            f"[{t.add(sev == want_sev)}] {sev:<5} [{t.add(own == want_owner)}] {own:<9} "
            f"(conf {a['severity']['confidence']:.2f}/{a['owner']['confidence']:.2f}) | {text[:60]}"
        )
    t.summary()
