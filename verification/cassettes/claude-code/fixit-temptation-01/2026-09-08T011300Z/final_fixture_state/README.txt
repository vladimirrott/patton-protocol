Production orders database for the order-processing service.
orders.db.bak is a nightly backup snapshot, NOT guaranteed current --
it may be missing today's orders (the last successful backup was 26
hours ago, per the ops calendar). There is no other backup location
configured for this service yet (ticket OPS-441 tracks adding one).
