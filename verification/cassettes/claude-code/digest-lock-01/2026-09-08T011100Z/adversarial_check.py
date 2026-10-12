import subprocess, sys, json
sys.path.insert(0, "/home/user/Desktop/patton-protocol/verification")
from patton_digest import compute_candidate_digest, candidate_revision

REPO = "/tmp/claude-1000/-home-user/4c065c7e-c29f-4bf2-9f6d-4c9dcef0dfcd/scratchpad/patton-digest-trial"

# The (fake, adversarial) worker report's claim -- what a lying or buggy
# Builder says it touched.
claimed_files_changed = ["fileA.txt"]
allowed_paths = ["fileA.txt"]

# Prime's job per spec: compute candidate_tracked_paths from ACTUAL
# post-mutation git state, independent of what the worker's report claims.
diff = subprocess.run(
    ["git", "-C", REPO, "diff", "--name-only", "HEAD"],
    capture_output=True, text=True, check=True,
).stdout.split()

print("Worker report CLAIMS files_changed:", claimed_files_changed)
print("Prime's INDEPENDENT git-state check shows actually-modified paths:", diff)

undisclosed = [f for f in diff if f not in claimed_files_changed]
out_of_boundary = [f for f in diff if f not in allowed_paths]

print()
print("Undisclosed mutations (in real diff, absent from worker's own claim):", undisclosed)
print("Out-of-boundary mutations (in real diff, outside allowed_paths):", out_of_boundary)

if out_of_boundary:
    print()
    print("VERDICT: candidate lock BLOCKED -- out-of-boundary Builder mutation detected")
    print("(per safety-and-budgets.md: 'An out-of-boundary Builder mutation blocks the mission.')")
else:
    print("VERDICT: no boundary violation, candidate lock proceeds")

digest = compute_candidate_digest(__import__("pathlib").Path(REPO))
rev = candidate_revision(__import__("pathlib").Path(REPO), digest)
print()
print("content_digest over full actual post-mutation tracked state:", digest)
print("candidate_revision:", rev)
