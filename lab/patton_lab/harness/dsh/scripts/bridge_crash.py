"""S5s: a bridge that dies before it answers (the shim's PATTON_SHIM_BRIDGE points here)."""
import sys

sys.stdin.read()
raise SystemExit("bridge_crash.py: simulated crash")
