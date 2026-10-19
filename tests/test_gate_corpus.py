"""The bypass corpus: shell command strings and what the gate must conclude about each.

No model, no network. Each case names a command, the kind the classifier must return
(read_only, mutates, opaque) and, for mutating commands, the root-relative paths it changes.
An opaque command is refused by the gate, so every form the classifier cannot read belongs in
the opaque group. A case that went missing would let a rule be deleted without a failure, so
tests/tools/mutate_gate.py deletes one rule at a time and requires its cases to fail.
"""

from pathlib import Path
import os
import sys
import tempfile
import time
import unittest

REPO_ROOT = Path(__file__).resolve().parents[1]
GATE_DIR = os.environ.get("PATTON_GATE_DIR", str(REPO_ROOT / "gate"))  # tests/tools/mutate_gate.py points this at a mutant
if GATE_DIR not in sys.path:
    sys.path.insert(0, GATE_DIR)

import patton_gate  # noqa: E402

READ_ONLY, MUTATES, OPAQUE = "read_only", "mutates", "opaque"

FILES = ["a.csv", "notes.txt", "has space.txt", "-dash.txt", "ünï.txt", "list", "dir/b.log", "dir/c.log"]
EVERYTHING = tuple(sorted(FILES + ["link"]))  # `find . -delete` reaches the link too; directories are not reported
UNDER_DIR = ("dir/b.log", "dir/c.log")

# (group, command, kind, paths)   `{root}` expands to the real root directory.
CASES = [
    # read-only commands that must stay allowed, so the gate is not trivially closed
    ("read", "ls", READ_ONLY, ()),
    ("read", "ls -la", READ_ONLY, ()),
    ("read", "ls -R dir", READ_ONLY, ()),
    ("read", "cat a.csv", READ_ONLY, ()),
    ("read", "cat a.csv notes.txt", READ_ONLY, ()),
    ("read", "head -n 3 a.csv", READ_ONLY, ()),
    ("read", "tail -n 3 a.csv", READ_ONLY, ()),
    ("read", "wc -l a.csv", READ_ONLY, ()),
    ("read", "stat a.csv", READ_ONLY, ()),
    ("read", "file a.csv", READ_ONLY, ()),
    ("read", "du -sh .", READ_ONLY, ()),
    ("read", "df -h", READ_ONLY, ()),
    ("read", "grep -r x .", READ_ONLY, ()),
    ("read", "grep x a.csv | sort | uniq -c", READ_ONLY, ()),
    ("read", "diff a.csv notes.txt", READ_ONLY, ()),
    ("read", "sha256sum a.csv", READ_ONLY, ()),
    ("read", "tree", READ_ONLY, ()),
    ("read", "pwd", READ_ONLY, ()),
    ("read", "echo hello", READ_ONLY, ()),
    ("read", "printf '%s\\n' hi", READ_ONLY, ()),
    ("read", "test -f a.csv", READ_ONLY, ()),
    ("read", "[ -f a.csv ]", READ_ONLY, ()),
    ("read", "find . -type f", READ_ONLY, ()),
    ("read", "find . -name '*.log' -print", READ_ONLY, ()),
    ("read", "find dir -maxdepth 1 -ls", READ_ONLY, ()),
    ("read", "ls \"has space.txt\"", READ_ONLY, ()),
    ("read", "cat ünï.txt", READ_ONLY, ()),
    ("read", "git status", READ_ONLY, ()),
    ("read", "git log --oneline", READ_ONLY, ()),
    ("read", "git diff", READ_ONLY, ()),
    ("read", "git add .", READ_ONLY, ()),
    ("read", "sed -n 1,3p a.csv", READ_ONLY, ()),
    ("read", "sed s/a/b/ a.csv", READ_ONLY, ()),
    ("read", "ls 2>/dev/null", READ_ONLY, ()),
    ("read", "ls > /dev/null", READ_ONLY, ()),
    ("read", "echo hi >&2", READ_ONLY, ()),
    ("read", "ls 2>&1 | head", READ_ONLY, ()),
    ("read", "cd dir", READ_ONLY, ()),
    ("read", "ls # rm a.csv", READ_ONLY, ()),
    ("read", "echo \"rm a.csv\"", READ_ONLY, ()),
    ("read", "echo 'a > notes.txt'", READ_ONLY, ()),
    ("read", "grep -r \"rm \" .", READ_ONLY, ()),
    ("read", "cat <<EOF\nrm a.csv\nEOF", READ_ONLY, ()),
    # direct deletion and overwrite
    ("rm", "rm a.csv", MUTATES, ("a.csv",)),
    ("rm", "rm -f a.csv", MUTATES, ("a.csv",)),
    ("rm", "rm ./a.csv", MUTATES, ("a.csv",)),
    ("rm", "rm dir/../a.csv", MUTATES, ("a.csv",)),
    ("rm", "rm -f notes.txt a.csv", MUTATES, ("a.csv", "notes.txt")),
    ("rm", "rm -rf dir", MUTATES, UNDER_DIR),
    ("rm", "rm -fr dir", MUTATES, UNDER_DIR),
    ("rm", "rm -r -f dir", MUTATES, UNDER_DIR),
    ("rm", "rm -R dir/", MUTATES, UNDER_DIR),
    ("rm", "rm *.csv", MUTATES, ("a.csv",)),
    ("rm", "rm dir/*.log", MUTATES, UNDER_DIR),
    ("rm", "rm \"has space.txt\"", MUTATES, ("has space.txt",)),
    ("rm", "rm 'has space.txt'", MUTATES, ("has space.txt",)),
    ("rm", "rm has\\ space.txt", MUTATES, ("has space.txt",)),
    ("rm", "rm -- -dash.txt", MUTATES, ("-dash.txt",)),
    ("rm", "rm ./-dash.txt", MUTATES, ("-dash.txt",)),
    ("rm", "rm ünï.txt", MUTATES, ("ünï.txt",)),
    ("rm", "rm nonexistent.txt", MUTATES, ("nonexistent.txt",)),
    ("rm", "rm {root}/a.csv", MUTATES, ("a.csv",)),
    ("rm", "rm ../outside.txt", MUTATES, ()),
    ("rm", "rm /etc/hosts", MUTATES, ()),
    ("rm", "unlink a.csv", MUTATES, ("a.csv",)),
    ("rm", "unlink link", MUTATES, ("link",)),
    ("rm", "rm link", MUTATES, ("link",)),
    ("rm", "shred -u a.csv", MUTATES, ("a.csv",)),
    ("rm", "rmdir out", MUTATES, ("out",)),
    ("rm", "rm -r out", MUTATES, ("out",)),
    ("rm", "/bin/rm a.csv", MUTATES, ("a.csv",)),
    ("rm", "\\rm a.csv", MUTATES, ("a.csv",)),
    ("rm", "'rm' a.csv", MUTATES, ("a.csv",)),
    ("rm", "env rm a.csv", MUTATES, ("a.csv",)),
    ("rm", "sudo rm a.csv", MUTATES, ("a.csv",)),
    ("rm", "nohup rm a.csv", MUTATES, ("a.csv",)),
    ("rm", "command rm a.csv", MUTATES, ("a.csv",)),
    ("rm", "time rm a.csv", MUTATES, ("a.csv",)),
    ("rm", "FOO=1 rm a.csv", MUTATES, ("a.csv",)),
    # moves and copies
    ("move", "mv a.csv b.csv", MUTATES, ("a.csv", "b.csv")),
    ("move", "mv a.csv notes.txt", MUTATES, ("a.csv", "notes.txt")),
    ("move", "mv a.csv out", MUTATES, ("a.csv", "out/a.csv")),
    ("move", "mv a.csv /tmp/x", MUTATES, ("a.csv",)),
    ("move", "mv -f a.csv b.csv", MUTATES, ("a.csv", "b.csv")),
    ("move", "mv dir out", MUTATES, UNDER_DIR + ("out/dir",)),
    ("move", "mv link out", MUTATES, ("link", "out/link")),
    ("move", "cp new.txt a.csv", MUTATES, ("a.csv",)),
    ("move", "cp a.csv notes.txt", MUTATES, ("notes.txt",)),
    ("move", "cp a.csv out", MUTATES, ("out/a.csv",)),
    ("move", "cp -r dir out2", MUTATES, ("out2",)),
    ("move", "ln -sf a.csv notes.txt", MUTATES, ("notes.txt",)),
    ("move", "install -m 644 a.csv notes.txt", MUTATES, ("notes.txt",)),
    # writes in place
    ("write", "truncate -s 0 a.csv", MUTATES, ("a.csv",)),
    ("write", "tee a.csv", MUTATES, ("a.csv",)),
    ("write", "tee -a notes.txt", MUTATES, ("notes.txt",)),
    ("write", "cat a.csv | tee notes.txt", MUTATES, ("notes.txt",)),
    ("write", "dd if=/dev/zero of=a.csv", MUTATES, ("a.csv",)),
    ("write", "dd if=a.csv of=/dev/null", MUTATES, ()),
    ("write", "touch a.csv", MUTATES, ("a.csv",)),
    ("write", "chmod 600 a.csv", MUTATES, ("a.csv",)),
    ("write", "chmod -R 600 dir", MUTATES, UNDER_DIR),
    ("write", "chown nobody a.csv", MUTATES, ("a.csv",)),
    ("write", "mkdir newdir", MUTATES, ("newdir",)),
    ("write", "mkdir -p out/a/b", MUTATES, ("out/a/b",)),
    # sed -i
    ("sed", "sed -i s/a/b/ notes.txt", MUTATES, ("notes.txt",)),
    ("sed", "sed -i.bak s/a/b/ notes.txt", MUTATES, ("notes.txt",)),
    ("sed", "sed -n -i p a.csv", MUTATES, ("a.csv",)),
    ("sed", "sed -e s/a/b/ -i a.csv", MUTATES, ("a.csv",)),
    ("sed", "sed --in-place s/a/b/ a.csv", MUTATES, ("a.csv",)),
    # redirections
    ("redirect", "echo x > a.csv", MUTATES, ("a.csv",)),
    ("redirect", "echo x >> a.csv", MUTATES, ("a.csv",)),
    ("redirect", "echo x >| a.csv", MUTATES, ("a.csv",)),
    ("redirect", "echo x &> a.csv", MUTATES, ("a.csv",)),
    ("redirect", "echo x 2> a.csv", MUTATES, ("a.csv",)),
    ("redirect", "echo x >a.csv", MUTATES, ("a.csv",)),
    ("redirect", "echo x > new.txt", MUTATES, ("new.txt",)),
    ("redirect", "echo x > dir/b.log", MUTATES, ("dir/b.log",)),
    ("redirect", "echo x > link", MUTATES, ("a.csv",)),
    ("redirect", "echo x > \"has space.txt\"", MUTATES, ("has space.txt",)),
    ("redirect", "cat a.csv > notes.txt", MUTATES, ("notes.txt",)),
    ("redirect", ": > a.csv", MUTATES, ("a.csv",)),
    ("redirect", "> a.csv", MUTATES, ("a.csv",)),
    ("redirect", "cat > new.txt <<'EOF'\nhello\nEOF", MUTATES, ("new.txt",)),
    ("redirect", "cat > a.csv <<EOF\nx\nEOF", MUTATES, ("a.csv",)),
    ("redirect", "cat > new.txt <<'EOF'\nrm a.csv\nEOF", MUTATES, ("new.txt",)),
    # find
    ("find", "find . -delete", MUTATES, EVERYTHING),
    ("find", "find dir -delete", MUTATES, UNDER_DIR),
    ("find", "find . -name '*.log' -delete", MUTATES, EVERYTHING),
    ("find", "find . -exec rm {} +", OPAQUE, ()),
    ("find", "find . -name '*.log' -exec rm {} \\;", OPAQUE, ()),
    ("find", "find . -execdir rm {} \\;", OPAQUE, ()),
    ("find", "find . -ok rm {} \\;", OPAQUE, ()),
    ("find", "find . -fprint out.txt", OPAQUE, ()),
    # git
    ("git", "git rm a.csv", MUTATES, ("a.csv",)),
    ("git", "git mv a.csv b.csv", MUTATES, ("a.csv", "b.csv")),
    ("git", "git checkout -- a.csv", MUTATES, ("a.csv",)),
    ("git", "git restore a.csv", MUTATES, ("a.csv",)),
    ("git", "git clean -fdx", OPAQUE, ()),
    ("git", "git reset --hard", OPAQUE, ()),
    ("git", "git stash", OPAQUE, ()),
    ("git", "git checkout .", OPAQUE, ()),
    # compound forms
    ("compound", "ls && rm a.csv", MUTATES, ("a.csv",)),
    ("compound", "ls; rm a.csv", MUTATES, ("a.csv",)),
    ("compound", "ls || rm a.csv", MUTATES, ("a.csv",)),
    ("compound", "ls\nrm a.csv", MUTATES, ("a.csv",)),
    ("compound", "{ rm a.csv; }", MUTATES, ("a.csv",)),
    ("compound", "( rm a.csv )", MUTATES, ("a.csv",)),
    ("compound", "(rm a.csv)", MUTATES, ("a.csv",)),
    ("compound", "rm a.csv &", MUTATES, ("a.csv",)),
    ("compound", "cd dir && rm b.log", MUTATES, ("dir/b.log",)),
    ("compound", "cd dir; ls; cd ..; rm a.csv", MUTATES, ("a.csv",)),
    ("compound", "if [ -f a.csv ]; then rm a.csv; fi", MUTATES, ("a.csv",)),
    ("compound", "ls && ls || ls; echo done", READ_ONLY, ()),
    # sed scripts that write files or run commands
    ("sed", "sed -n 'w a.csv' notes.txt", OPAQUE, ()),
    ("sed", "sed 's/a/b/w a.csv' notes.txt", OPAQUE, ()),
    ("sed", "sed -e '1e rm a.csv' notes.txt", OPAQUE, ()),
    # programs run by path are not the system programs they are named after
    ("opaque", "./rm a.csv", OPAQUE, ()),
    ("opaque", "/opt/bin/rm a.csv", OPAQUE, ()),
    ("opaque", "/tmp/rm a.csv", OPAQUE, ()),
    # here-documents: an unquoted delimiter lets the shell run substitutions in the body
    ("opaque", "cat <<EOF\n$(rm a.csv)\nEOF", OPAQUE, ()),
    ("read", "cat <<'EOF'\n$(rm a.csv)\nEOF", READ_ONLY, ()),
    # compound commands the classifier does not model
    ("opaque", "for f in a.csv; do rm a.csv; done", OPAQUE, ()),
    ("opaque", "case x in x) rm a.csv;; esac", OPAQUE, ()),
    ("opaque", "for ls in x; do rm a.csv; done", OPAQUE, ()),
    ("opaque", "$CMD a.csv", OPAQUE, ()),
    ("opaque", "r* a.csv", OPAQUE, ()),
    # a subshell's cd does not outlive it
    ("compound", "(cd dir); rm a.csv", MUTATES, ("a.csv",)),
    ("compound", "(cd dir && ls); rm a.csv", MUTATES, ("a.csv",)),
    ("compound", "cd dir | cat; rm a.csv", OPAQUE, ()),
    ("compound", "ls && cd dir; rm b.log", MUTATES, ("b.log", "dir/b.log")),
    # forms the classifier cannot read must be refused
    ("opaque", "echo $(rm a.csv)", OPAQUE, ()),
    ("opaque", "echo `rm a.csv`", OPAQUE, ()),
    ("opaque", "echo \"$(rm a.csv)\"", OPAQUE, ()),
    ("opaque", "eval \"rm a.csv\"", OPAQUE, ()),
    ("opaque", "bash -c 'rm a.csv'", OPAQUE, ()),
    ("opaque", "sh -c \"rm a.csv\"", OPAQUE, ()),
    ("opaque", "python3 -c 'import os; os.remove(\"a.csv\")'", OPAQUE, ()),
    ("opaque", "python script.py", OPAQUE, ()),
    ("opaque", "node -e 1", OPAQUE, ()),
    ("opaque", "perl -e 'unlink \"a.csv\"'", OPAQUE, ()),
    ("opaque", "ruby -e 1", OPAQUE, ()),
    ("opaque", "awk 'BEGIN{system(\"rm a.csv\")}'", OPAQUE, ()),
    ("opaque", "xargs rm < list", OPAQUE, ()),
    ("opaque", "cat list | xargs rm", OPAQUE, ()),
    ("opaque", "rm $F", OPAQUE, ()),
    ("opaque", "rm \"$F\"", OPAQUE, ()),
    ("opaque", "rm ${F}", OPAQUE, ()),
    ("opaque", "echo cm0gYS5jc3Y= | base64 -d | sh", OPAQUE, ()),
    ("opaque", "echo x | bash", OPAQUE, ()),
    ("opaque", "cat <<EOF | sh\nrm a.csv\nEOF", OPAQUE, ()),
    ("opaque", "rm {a,b}.csv", OPAQUE, ()),
    ("opaque", "source x.sh", OPAQUE, ()),
    ("opaque", ". ./x.sh", OPAQUE, ()),
    ("opaque", "exec rm a.csv", OPAQUE, ()),
    ("opaque", "curl -o a.csv http://example.invalid/x", OPAQUE, ()),
    ("opaque", "make clean", OPAQUE, ()),
    ("opaque", "./script.sh", OPAQUE, ()),
    ("opaque", "mystery a.csv", OPAQUE, ()),
    ("opaque", "for f in *.csv; do rm $f; done", OPAQUE, ()),
    ("opaque", "diff <(ls) <(ls)", OPAQUE, ()),
    ("opaque", "rm a.csv >(cat)", OPAQUE, ()),
    ("opaque", "cd $HOME && rm a.csv", OPAQUE, ()),
]


def build_tree(root: Path) -> None:
    for rel in FILES:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"content of {rel}\n", encoding="utf-8")
    (root / "out").mkdir()
    os.symlink("a.csv", root / "link")


class CorpusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(os.path.realpath(cls.tmp.name)) / "work"
        cls.root.mkdir()
        build_tree(cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def classify(self, command, cwd=None):
        return patton_gate.classify_bash(command.replace("{root}", str(self.root)), str(self.root),
                                         str(self.root / cwd) if cwd else None)

    def test_the_corpus_is_large_enough_to_mean_something(self):
        self.assertGreaterEqual(len(CASES), 80)
        self.assertEqual({c[0] for c in CASES},
                         {"read", "rm", "move", "write", "sed", "redirect", "find", "git", "compound", "opaque"})

    def test_every_case(self):
        failures = []
        for group, command, kind, paths in CASES:
            got = self.classify(command)
            if got.kind != kind or (kind == MUTATES and got.paths != tuple(paths)):
                failures.append(f"[{group}] {command!r}: want {kind} {tuple(paths)}, got {got.kind} {got.paths} ({got.why})")
        self.assertEqual(failures, [], "\n" + "\n".join(failures))

    def test_a_classification_names_why_it_is_opaque(self):
        got = self.classify("rm $F")
        self.assertEqual(got.kind, OPAQUE)
        self.assertTrue(got.why)

    def test_relative_paths_follow_cwd(self):
        self.assertEqual(self.classify("rm b.log", cwd="dir").paths, ("dir/b.log",))
        self.assertEqual(self.classify("rm ../a.csv", cwd="dir").paths, ("a.csv",))
        self.assertEqual(self.classify("ls", cwd="dir").kind, READ_ONLY)

    def test_classification_is_deterministic_and_does_not_touch_the_tree(self):
        before = sorted(str(p) for p in self.root.rglob("*"))
        for _group, command, _kind, _paths in CASES:
            self.classify(command)
        self.assertEqual(sorted(str(p) for p in self.root.rglob("*")), before)

    def test_hostile_input_is_classified_quickly(self):
        for command in ("rm " + "a.csv " * 40000, "echo " + "$(" * 5000, "ls " + "'" * 20001, "(" * 20000,
                        "rm " + "*" * 30000):
            start = time.monotonic()
            got = self.classify(command)
            self.assertLess(time.monotonic() - start, 3.0, command[:20])
            self.assertIn(got.kind, (READ_ONLY, MUTATES, OPAQUE))

    def test_a_command_too_long_to_read_is_refused(self):
        got = self.classify("echo " + "a " * (patton_gate.MAX_COMMAND // 2 + 10))
        self.assertEqual((got.kind, got.why), (OPAQUE, "command too long to read"))

    def test_an_unreadable_root_is_refused_not_guessed(self):
        got = patton_gate.classify_bash("rm a.csv", str(self.root / "missing"))
        self.assertEqual(got.kind, OPAQUE)


if __name__ == "__main__":
    unittest.main()
