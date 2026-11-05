"""Options that turn a read-only verb into a writer or a launcher, in the plugin's command classifier.

`git diff --output=notes.txt` overwrites a file, `rg --pre CMD` runs CMD on every file it searches, `git branch NAME`
and `git remote add` change the repository, and none of them reads as a change to a file the gate protects. The
classifier must return opaque (the gate asks) for each, and must keep the plain read-only forms allowed.
The frozen study gate in gate/ keeps the classifier it was registered with, so this runs on the plugin only.
"""

from pathlib import Path
import os
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tests"))
from test_gate_plugin import gate  # noqa: E402

OPAQUE = [
    'rg --pre "rm -rf ." foo .', "rg foo . --pre=./evil.sh", "rg --pre-glob '*.x' foo", "rg --hostname-bin ./x foo",
    "tree -o notes.txt", "tree --output notes.txt", "tree --output=notes.txt",
    "git diff --output=notes.txt", "git log --output notes.txt", "git show --output=notes.txt HEAD",
    "git diff --ext-diff", "git show --textconv HEAD", "git log --ext-diff -p", "git diff --textconv",
    'git grep -O"rm -rf ." foo', "git grep --open-files-in-pager foo", "git grep -Ovim foo",
    "git branch newb", "git branch -m a b", "git branch -D x", "git branch -d x", "git branch -c a b",
    "git branch --delete x", "git branch -f x", "git branch --set-upstream-to=origin/x",
    "git remote add x y", "git remote set-url origin x", "git remote remove origin", "git remote rename a b",
    "git remote prune origin",
    "file -C -m notes.txt", "file --compile",
    # git and GNU tools accept any unambiguous start of a long option, and tree -R runs tree again with -o in every directory
    "git diff --out=notes.txt", "git log --outp notes.txt", "git show --ext=HEAD", "git diff --text HEAD",
    "git grep --open foo", "file --comp notes.txt", "tree -R", "tree -R -L 2", "tree -R -H . -L 2", "tree --outp=notes.txt",
]
READ_ONLY = [
    "rg foo .", "rg -n --hidden foo", "tree -a", "tree -L 2", "file notes.txt",
    "git diff --stat", "git log --oneline -n 3", "git show HEAD", "git grep foo", "git status", "git add .",
    "git branch", "git branch -a", "git branch -r", "git branch -v", "git branch --list 'f*'", "git branch --show-current",
    "git diff -- notes.txt", "git log -- notes.txt", "git log --oneline", "git diff --stat --name-only", "git log --format=%H", "git diff --output-indicator-new=+",
    "git log --pretty=oneline --abbrev-commit", "git diff --no-ext-diff",
    "git branch --contains HEAD", "git branch -l 'f*'", "git branch -la 'f*'", "git remote", "git remote -v", "git remote show origin", "git remote get-url origin",
]


class OptionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        Path(self.root, "notes.txt").write_text("x\n")

    def kind(self, command):
        return gate.classify_bash(command, self.root).kind

    def test_an_option_that_writes_or_launches_is_opaque(self):
        for command in OPAQUE:
            with self.subTest(command=command):
                self.assertEqual(self.kind(command), "opaque")

    def test_the_plain_read_only_forms_stay_allowed(self):
        for command in READ_ONLY:
            with self.subTest(command=command):
                self.assertEqual(self.kind(command), "read_only")

    def test_the_option_is_found_after_a_pipe_a_semicolon_and_a_wrapper(self):
        for command in ("cat notes.txt | rg --pre ./x foo", "ls; git diff --output=notes.txt", "env git diff --output=notes.txt",
                        "true && git branch newb"):
            with self.subTest(command=command):
                self.assertNotEqual(self.kind(command), "read_only")


if __name__ == "__main__":
    unittest.main()
